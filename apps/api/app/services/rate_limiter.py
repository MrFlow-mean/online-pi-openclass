from __future__ import annotations

import hashlib
import ipaddress
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.requests import Request


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window_seconds < 1:
            raise ValueError("rate-limit policy values must be positive")


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class InMemorySlidingWindowRateLimiter:
    """Process-local limiter; deploy a shared backend before running multiple API replicas."""

    def __init__(self, *, max_keys: int = 50_000) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max_keys = max(100, max_keys)

    def check(
        self,
        namespace: str,
        subject: str,
        policy: RateLimitPolicy,
        *,
        now: float | None = None,
    ) -> RateLimitDecision:
        current = time.monotonic() if now is None else now
        key = f"{namespace}:{subject}"
        cutoff = current - policy.window_seconds
        with self._lock:
            if key not in self._events and len(self._events) >= self._max_keys:
                self._prune(cutoff)
                if len(self._events) >= self._max_keys:
                    oldest_key = min(
                        self._events,
                        key=lambda candidate: self._events[candidate][-1],
                    )
                    self._events.pop(oldest_key, None)
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= policy.limit:
                retry_after = max(1, int(events[0] + policy.window_seconds - current + 0.999))
                return RateLimitDecision(
                    allowed=False,
                    limit=policy.limit,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )
            events.append(current)
            return RateLimitDecision(
                allowed=True,
                limit=policy.limit,
                remaining=max(0, policy.limit - len(events)),
                retry_after_seconds=0,
            )

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def _prune(self, cutoff: float) -> None:
        stale = [key for key, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in stale:
            self._events.pop(key, None)


def _network_list(value: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in value.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def trusted_proxy_networks_from_environment() -> tuple[
    ipaddress.IPv4Network | ipaddress.IPv6Network, ...
]:
    return _network_list(os.getenv("OPENCLASS_TRUSTED_PROXY_CIDRS", ""))


def client_ip_from_request(
    request: Request,
    *,
    trusted_proxy_networks: Iterable[
        ipaddress.IPv4Network | ipaddress.IPv6Network
    ] | None = None,
) -> str:
    peer = request.client.host if request.client else ""
    try:
        current = ipaddress.ip_address(peer)
    except ValueError:
        return peer or "unknown"

    trusted = tuple(
        trusted_proxy_networks
        if trusted_proxy_networks is not None
        else trusted_proxy_networks_from_environment()
    )
    if not any(current in network for network in trusted):
        return current.compressed

    forwarded_for = request.headers.get("x-forwarded-for", "")
    raw_hops = forwarded_for.split(",") if forwarded_for else []
    if len(raw_hops) > 64:
        return current.compressed
    hops: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in raw_hops:
        candidate = value.strip()
        if not candidate:
            return current.compressed
        try:
            hops.append(ipaddress.ip_address(candidate))
        except ValueError:
            return current.compressed

    for candidate in reversed(hops):
        if any(current in network for network in trusted):
            current = candidate
        else:
            break
    return current.compressed


def anonymized_rate_limit_subject(*parts: str | None) -> str:
    normalized = "\x1f".join((part or "").strip().casefold() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def auth_rate_limit_policies_from_environment() -> Mapping[str, RateLimitPolicy]:
    defaults = {
        "login": RateLimitPolicy(limit=10, window_seconds=300),
        "register": RateLimitPolicy(limit=5, window_seconds=900),
        "password_recovery": RateLimitPolicy(limit=5, window_seconds=900),
    }
    configured: dict[str, RateLimitPolicy] = {}
    for action, default in defaults.items():
        prefix = f"OPENCLASS_{action.upper()}_RATE_LIMIT"
        try:
            limit = int(os.getenv(f"{prefix}_COUNT", str(default.limit)))
            window = int(os.getenv(f"{prefix}_WINDOW_SECONDS", str(default.window_seconds)))
            configured[action] = RateLimitPolicy(limit=limit, window_seconds=window)
        except ValueError:
            configured[action] = default
    return configured


auth_rate_limiter = InMemorySlidingWindowRateLimiter()


def enforce_auth_rate_limit(
    action: str,
    request: Request,
    *,
    account_identifier: str | None = None,
    limiter: InMemorySlidingWindowRateLimiter | None = None,
    policies: Mapping[str, RateLimitPolicy] | None = None,
) -> RateLimitDecision:
    active_policies = policies or auth_rate_limit_policies_from_environment()
    if action not in active_policies:
        raise ValueError(f"unsupported auth rate-limit action: {action}")
    active_limiter = limiter or auth_rate_limiter
    policy = active_policies[action]
    client_ip = client_ip_from_request(request)
    decisions = [
        active_limiter.check(
            f"auth:{action}:ip",
            anonymized_rate_limit_subject(client_ip),
            policy,
        )
    ]
    if account_identifier:
        decisions.append(
            active_limiter.check(
                f"auth:{action}:account",
                anonymized_rate_limit_subject(account_identifier),
                policy,
            )
        )
    blocked = [decision for decision in decisions if not decision.allowed]
    if blocked:
        retry_after = max(decision.retry_after_seconds for decision in blocked)
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
    return min(decisions, key=lambda decision: decision.remaining)
