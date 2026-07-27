from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import httpx

from app.services.billing_service import BillingService


logger = logging.getLogger(__name__)


def _now_datetime() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_datetime().isoformat()


def _enabled(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("Boolean OpenRouter settings must be true or false")


def _usd_to_microusd(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise OpenRouterAPIError(502, "OpenRouter returned an invalid USD amount") from exc
    if amount < 0:
        raise OpenRouterAPIError(502, "OpenRouter returned a negative USD amount")
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class OpenRouterConfig:
    provisioning_enabled: bool
    management_api_key: str
    api_origin: str
    sync_interval_seconds: float
    safety_buffer_microusd: int
    credentials_dir: Path
    model_map: dict[str, str]

    @property
    def configured(self) -> bool:
        return bool(self.management_api_key)

    @property
    def active(self) -> bool:
        return self.provisioning_enabled and self.configured

    @classmethod
    def from_env(cls, database_path: Path) -> OpenRouterConfig:
        try:
            interval = float(
                (os.getenv("OPENCLASS_OPENROUTER_SYNC_INTERVAL_SECONDS") or "10").strip()
            )
        except ValueError as exc:
            raise RuntimeError(
                "OPENCLASS_OPENROUTER_SYNC_INTERVAL_SECONDS must be a number"
            ) from exc
        if not 0.1 <= interval <= 3600:
            raise RuntimeError(
                "OPENCLASS_OPENROUTER_SYNC_INTERVAL_SECONDS must be between 0.1 and 3600"
            )
        try:
            safety_buffer = Decimal(
                (os.getenv("OPENCLASS_OPENROUTER_SAFETY_BUFFER_USD") or "25").strip()
            )
        except InvalidOperation as exc:
            raise RuntimeError(
                "OPENCLASS_OPENROUTER_SAFETY_BUFFER_USD must be a valid USD amount"
            ) from exc
        if safety_buffer < 0:
            raise RuntimeError(
                "OPENCLASS_OPENROUTER_SAFETY_BUFFER_USD cannot be negative"
            )
        raw_model_map = (os.getenv("OPENCLASS_OPENROUTER_MODEL_MAP_JSON") or "{}").strip()
        try:
            parsed_model_map = json.loads(raw_model_map)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OPENCLASS_OPENROUTER_MODEL_MAP_JSON must be valid JSON") from exc
        if not isinstance(parsed_model_map, dict) or not all(
            isinstance(key, str)
            and key.strip()
            and isinstance(value, str)
            and value.strip()
            for key, value in parsed_model_map.items()
        ):
            raise RuntimeError(
                "OPENCLASS_OPENROUTER_MODEL_MAP_JSON must be a string-to-string object"
            )
        configured_dir = (os.getenv("OPENCLASS_OPENROUTER_CREDENTIALS_DIR") or "").strip()
        credentials_dir = (
            Path(configured_dir).expanduser()
            if configured_dir
            else database_path.parent / "openrouter-credentials"
        )
        if not credentials_dir.is_absolute():
            credentials_dir = database_path.parent / credentials_dir
        return cls(
            provisioning_enabled=_enabled(
                os.getenv("OPENCLASS_OPENROUTER_PROVISIONING_ENABLED")
            ),
            management_api_key=(os.getenv("OPENROUTER_MANAGEMENT_API_KEY") or "").strip(),
            api_origin=(
                os.getenv("OPENCLASS_OPENROUTER_API_ORIGIN")
                or "https://openrouter.ai"
            ).rstrip("/"),
            sync_interval_seconds=interval,
            safety_buffer_microusd=int(
                (safety_buffer * 1_000_000).to_integral_value(rounding=ROUND_FLOOR)
            ),
            credentials_dir=credentials_dir.resolve(),
            model_map={
                str(key).strip(): str(value).strip()
                for key, value in parsed_model_map.items()
            },
        )

    def resolve_model(self, provider: str, model: str) -> str:
        for key in (f"{provider}:{model}", f"{provider}:*", f"*:{model}", "*"):
            mapped = self.model_map.get(key)
            if mapped:
                return mapped
        raise RuntimeError(
            "The selected platform model has no OpenRouter mapping configured"
        )


class OpenRouterAPIError(RuntimeError):
    def __init__(self, status_code: int, operation: str) -> None:
        super().__init__(f"{operation} failed with HTTP {status_code}")
        self.status_code = status_code
        self.operation = operation


class OpenRouterCoverageError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(
        self,
        config: OpenRouterConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.config.configured:
            raise OpenRouterAPIError(503, "OpenRouter management authentication")
        headers = {
            "Authorization": f"Bearer {self.config.management_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=self.config.api_origin,
            transport=self.transport,
            timeout=30,
        ) as client:
            response = await client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
            )
        if response.status_code >= 400:
            raise OpenRouterAPIError(response.status_code, operation)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenRouterAPIError(502, operation) from exc
        if not isinstance(payload, dict):
            raise OpenRouterAPIError(502, operation)
        return payload

    async def credits(self) -> tuple[int, int]:
        payload = await self._request(
            "GET", "/api/v1/credits", operation="OpenRouter credit lookup"
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OpenRouterAPIError(502, "OpenRouter credit lookup")
        return _usd_to_microusd(data.get("total_credits")), _usd_to_microusd(
            data.get("total_usage")
        )

    async def list_keys(self) -> list[dict[str, Any]]:
        keys: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._request(
                "GET",
                "/api/v1/keys",
                operation="OpenRouter key listing",
                params={"include_disabled": "true", "offset": offset},
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise OpenRouterAPIError(502, "OpenRouter key listing")
            page = [item for item in data if isinstance(item, dict)]
            keys.extend(page)
            if len(page) < 100:
                return keys
            offset += len(page)

    async def get_key(self, key_hash: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/api/v1/keys/{key_hash}",
            operation="OpenRouter key lookup",
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OpenRouterAPIError(502, "OpenRouter key lookup")
        return data

    async def create_key(self, *, name: str, limit_microusd: int) -> tuple[dict[str, Any], str]:
        payload = await self._request(
            "POST",
            "/api/v1/keys",
            operation="OpenRouter key creation",
            json_body={
                "name": name,
                "limit": float(Decimal(limit_microusd) / Decimal(1_000_000)),
                "limit_reset": None,
                "include_byok_in_limit": False,
            },
        )
        data = payload.get("data")
        plaintext = payload.get("key")
        if not isinstance(data, dict) or not isinstance(plaintext, str) or not plaintext:
            raise OpenRouterAPIError(502, "OpenRouter key creation")
        return data, plaintext

    async def update_key(
        self,
        key_hash: str,
        *,
        name: str,
        limit_microusd: int,
        disabled: bool,
    ) -> dict[str, Any]:
        payload = await self._request(
            "PATCH",
            f"/api/v1/keys/{key_hash}",
            operation="OpenRouter key update",
            json_body={
                "name": name,
                "limit": float(Decimal(limit_microusd) / Decimal(1_000_000)),
                "limit_reset": None,
                "include_byok_in_limit": False,
                "disabled": disabled,
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OpenRouterAPIError(502, "OpenRouter key update")
        return data

    async def delete_key(self, key_hash: str) -> None:
        await self._request(
            "DELETE",
            f"/api/v1/keys/{key_hash}",
            operation="OpenRouter key deletion",
        )


class OpenRouterProvisioningStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def claim(self, *, lease_seconds: int = 60) -> dict[str, Any] | None:
        now = _now_datetime()
        timestamp = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM openrouter_limit_grants
                WHERE (
                    status IN ('pending', 'retry') AND available_at <= ?
                ) OR (
                    status = 'leased' AND lease_until IS NOT NULL AND lease_until <= ?
                )
                ORDER BY created_at ASC, grant_id ASC
                LIMIT 1
                """,
                (timestamp, timestamp),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE openrouter_limit_grants
                SET status = 'leased', attempts = attempts + 1, lease_until = ?,
                    updated_at = ?
                WHERE grant_id = ?
                """,
                (lease_until, timestamp, row["grant_id"]),
            )
            claimed = dict(row)
            claimed["attempts"] = int(row["attempts"]) + 1
            return claimed

    def recover_leases(self) -> int:
        timestamp = _now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE openrouter_limit_grants
                SET status = 'retry', available_at = ?, lease_until = NULL,
                    updated_at = ?
                WHERE status = 'leased'
                """,
                (timestamp, timestamp),
            )
            return max(0, int(cursor.rowcount))

    def target_snapshot(self, user_id: str) -> tuple[int, int]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(delta_microusd), 0) AS target, "
                "COALESCE(MAX(rowid), 0) AS watermark "
                "FROM openrouter_limit_grants WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return max(0, int(row["target"])), int(row["watermark"])

    def target(self, user_id: str) -> int:
        target, _watermark = self.target_snapshot(user_id)
        return target

    def key(self, user_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM openrouter_user_keys WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def outstanding_microusd(self) -> int:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN target_limit_microusd > usage_microusd
                        THEN target_limit_microusd - usage_microusd
                        ELSE 0
                    END
                ), 0) AS outstanding
                FROM openrouter_user_keys
                """
            ).fetchone()
        return int(row["outstanding"])

    def save_remote_key(
        self,
        *,
        user_id: str,
        key_hash: str,
        key_label: str,
        target_microusd: int,
        usage_microusd: int,
        disabled: bool,
        status: str,
        last_error: str | None = None,
    ) -> None:
        timestamp = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE openrouter_user_keys
                SET key_hash = ?, key_label = ?, target_limit_microusd = ?,
                    usage_microusd = ?, disabled = ?, status = ?, last_error = ?,
                    synced_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    key_hash,
                    key_label,
                    target_microusd,
                    usage_microusd,
                    int(disabled),
                    status,
                    last_error,
                    timestamp,
                    timestamp,
                    user_id,
                ),
            )

    def clear_remote_key(self, user_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE openrouter_user_keys
                SET key_hash = NULL, key_label = NULL, usage_microusd = 0,
                    disabled = 1, status = 'syncing', synced_at = NULL,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (_now(), user_id),
            )

    def complete_through(
        self,
        user_id: str,
        *,
        target_microusd: int,
        watermark: int,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE openrouter_limit_grants
                SET target_limit_microusd = ?, status = 'completed', lease_until = NULL,
                    last_error = NULL, updated_at = ?
                WHERE user_id = ? AND rowid <= ? AND status != 'completed'
                """,
                (target_microusd, _now(), user_id, watermark),
            )
            current_target = max(
                0,
                int(
                    connection.execute(
                        "SELECT COALESCE(SUM(delta_microusd), 0) AS target "
                        "FROM openrouter_limit_grants WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["target"]
                ),
            )
            has_pending = connection.execute(
                """
                SELECT 1 FROM openrouter_limit_grants
                WHERE user_id = ? AND status IN ('pending', 'retry', 'leased')
                LIMIT 1
                """,
                (user_id,),
            ).fetchone() is not None
            connection.execute(
                """
                UPDATE openrouter_user_keys
                SET target_limit_microusd = ?,
                    status = CASE WHEN ? THEN 'syncing' ELSE status END,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (current_target, int(has_pending), _now(), user_id),
            )

    def retry(self, grant: dict[str, Any], *, error: str) -> None:
        attempts = max(1, int(grant.get("attempts") or 1))
        delay_seconds = min(300, 2 ** min(attempts, 8))
        available_at = (_now_datetime() + timedelta(seconds=delay_seconds)).isoformat()
        timestamp = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE openrouter_limit_grants
                SET status = 'retry', available_at = ?, lease_until = NULL,
                    last_error = ?, updated_at = ?
                WHERE grant_id = ?
                """,
                (available_at, error[:300], timestamp, grant["grant_id"]),
            )
            connection.execute(
                """
                UPDATE openrouter_user_keys
                SET status = 'blocked', last_error = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (error[:300], timestamp, grant["user_id"]),
            )

    def set_coverage_status(self, *, covered: bool) -> None:
        timestamp = _now()
        with self._transaction() as connection:
            if not covered:
                connection.execute(
                    """
                    UPDATE openrouter_user_keys
                    SET status = 'blocked', last_error = 'shared_credit_coverage',
                        updated_at = ?
                    WHERE target_limit_microusd > usage_microusd
                      AND key_hash IS NOT NULL
                      AND status = 'ready'
                    """,
                    (timestamp,),
                )
                return
            connection.execute(
                """
                UPDATE openrouter_user_keys AS user_key
                SET status = 'ready', last_error = NULL, updated_at = ?
                WHERE user_key.last_error = 'shared_credit_coverage'
                  AND user_key.disabled = 0
                  AND user_key.target_limit_microusd > user_key.usage_microusd
                  AND NOT EXISTS (
                      SELECT 1 FROM openrouter_limit_grants AS grant_task
                      WHERE grant_task.user_id = user_key.user_id
                        AND grant_task.status IN ('pending', 'retry', 'leased')
                  )
                """,
                (timestamp,),
            )


class OpenRouterProvisioningService:
    def __init__(
        self,
        billing_service: BillingService,
        *,
        config: OpenRouterConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.billing_service = billing_service
        self.config = config or OpenRouterConfig.from_env(billing_service.database_path)
        self.client = OpenRouterClient(self.config, transport=transport)
        self.store = OpenRouterProvisioningStore(billing_service.database_path)
        self.last_available_microusd: int | None = None
        self.balance_covered: bool | None = None

    def health(self, *, worker_healthy: bool) -> dict[str, bool]:
        return {
            "configured": self.config.configured,
            "provisioning_enabled": self.config.provisioning_enabled,
            "worker_healthy": worker_healthy,
        }

    def api_key_for_user(self, user_id: str) -> str:
        if not self.config.active:
            raise RuntimeError("OpenRouter user provisioning is not configured")
        key = self.store.key(user_id)
        if (
            key is None
            or key.get("status") != "ready"
            or bool(key.get("disabled"))
            or int(key.get("target_limit_microusd") or 0)
            <= int(key.get("usage_microusd") or 0)
        ):
            raise RuntimeError("模型额度尚未就绪，请稍后重试")
        return self._read_secret(user_id)

    async def run_once(self) -> bool:
        if not self.config.active:
            return False
        grant = self.store.claim()
        if grant is None:
            return False
        try:
            await self._sync_grant(grant)
        except Exception as exc:
            safe_error = self._safe_error(exc)
            self.store.retry(grant, error=safe_error)
            logger.warning(
                "OpenRouter provisioning deferred for user grant %s: %s",
                grant["grant_id"],
                safe_error,
            )
        return True

    async def refresh_balance(self) -> None:
        if not self.config.active:
            return
        total_credits, total_usage = await self.client.credits()
        available = max(0, total_credits - total_usage)
        required = self.store.outstanding_microusd() + self.config.safety_buffer_microusd
        covered = available >= required
        self.last_available_microusd = available
        if covered != self.balance_covered:
            if not covered:
                logger.warning(
                    "OpenRouter shared credits are below committed allowance coverage"
                )
            self.store.set_coverage_status(covered=covered)
            self.balance_covered = covered

    async def _sync_grant(self, grant: dict[str, Any]) -> None:
        user_id = str(grant["user_id"])
        target, watermark = self.store.target_snapshot(user_id)
        key = self.store.key(user_id)
        if key is None:
            raise RuntimeError("OpenRouter user key record is missing")
        key_name = str(key["key_name"])
        remote: dict[str, Any] | None = None
        key_hash = str(key.get("key_hash") or "")
        if key_hash:
            if not self._secret_path(user_id).is_file():
                await self._delete_remote_if_present(key_hash)
                self.store.clear_remote_key(user_id)
                key = self.store.key(user_id) or key
                key_hash = ""
            else:
                try:
                    remote = await self.client.get_key(key_hash)
                except OpenRouterAPIError as exc:
                    if exc.status_code != 404:
                        raise
                    self._delete_secret(user_id)
                    self.store.clear_remote_key(user_id)
                    key = self.store.key(user_id) or key
                    key_hash = ""
        if remote is not None:
            usage_microusd = max(
                int(key.get("usage_microusd") or 0),
                _usd_to_microusd(remote.get("usage", 0)),
            )
            self.store.save_remote_key(
                user_id=user_id,
                key_hash=key_hash,
                key_label=str(remote.get("label") or key.get("key_label") or key_name),
                target_microusd=target,
                usage_microusd=usage_microusd,
                disabled=bool(remote.get("disabled")),
                status="syncing",
            )
        else:
            usage_microusd = int(key.get("usage_microusd") or 0)

        if target <= 0:
            if remote is not None:
                remote = await self.client.update_key(
                    key_hash,
                    name=key_name,
                    limit_microusd=0,
                    disabled=True,
                )
                usage_microusd = max(
                    usage_microusd,
                    _usd_to_microusd(remote.get("usage", usage_microusd)),
                )
                self.store.save_remote_key(
                    user_id=user_id,
                    key_hash=key_hash,
                    key_label=str(remote.get("label") or key_name),
                    target_microusd=0,
                    usage_microusd=usage_microusd,
                    disabled=True,
                    status="blocked",
                )
            else:
                self._mark_without_remote_key(user_id=user_id, target=0, status="blocked")
            self.store.complete_through(
                user_id,
                target_microusd=target,
                watermark=watermark,
            )
            return

        remote_limit = _usd_to_microusd(remote.get("limit", 0)) if remote else 0
        if remote is None or target > remote_limit:
            total_credits, total_usage = await self.client.credits()
            available = max(0, total_credits - total_usage)
            self.last_available_microusd = available
            required = self.store.outstanding_microusd() + self.config.safety_buffer_microusd
            if available < required:
                raise OpenRouterCoverageError(
                    "OpenRouter shared credits do not cover user allowances and the safety buffer"
                )

        if remote is None:
            remote, key_hash = await self._create_or_recover_key(
                user_id=user_id,
                key_name=key_name,
                target_microusd=target,
            )
            usage_microusd = max(
                usage_microusd,
                _usd_to_microusd(remote.get("usage", 0)),
            )
        disabled = target <= usage_microusd
        remote = await self.client.update_key(
            key_hash,
            name=key_name,
            limit_microusd=target,
            disabled=disabled,
        )
        usage_microusd = max(
            usage_microusd,
            _usd_to_microusd(remote.get("usage", usage_microusd)),
        )
        disabled = disabled or bool(remote.get("disabled"))
        self.store.save_remote_key(
            user_id=user_id,
            key_hash=key_hash,
            key_label=str(remote.get("label") or key_name),
            target_microusd=target,
            usage_microusd=usage_microusd,
            disabled=disabled,
            status="blocked" if disabled else "ready",
        )
        self.store.complete_through(
            user_id,
            target_microusd=target,
            watermark=watermark,
        )

    async def _create_or_recover_key(
        self,
        *,
        user_id: str,
        key_name: str,
        target_microusd: int,
    ) -> tuple[dict[str, Any], str]:
        matching = [
            item
            for item in await self.client.list_keys()
            if str(item.get("name") or "") == key_name and item.get("hash")
        ]
        secret_path = self._secret_path(user_id)
        if len(matching) == 1 and secret_path.is_file():
            return matching[0], str(matching[0]["hash"])
        for orphan in matching:
            await self.client.delete_key(str(orphan["hash"]))
        self._delete_secret(user_id)
        remote, plaintext = await self.client.create_key(
            name=key_name,
            limit_microusd=target_microusd,
        )
        key_hash = str(remote.get("hash") or "")
        if not key_hash:
            raise OpenRouterAPIError(502, "OpenRouter key creation")
        self._write_secret(user_id, plaintext)
        return remote, key_hash

    async def _delete_remote_if_present(self, key_hash: str) -> None:
        try:
            await self.client.delete_key(key_hash)
        except OpenRouterAPIError as exc:
            if exc.status_code != 404:
                raise

    def _mark_without_remote_key(self, *, user_id: str, target: int, status: str) -> None:
        timestamp = _now()
        with self.store._transaction() as connection:
            connection.execute(
                """
                UPDATE openrouter_user_keys
                SET target_limit_microusd = ?, usage_microusd = 0, disabled = 1,
                    status = ?, last_error = NULL, synced_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (target, status, timestamp, timestamp, user_id),
            )

    def _secret_path(self, user_id: str) -> Path:
        key = self.store.key(user_id)
        if key is None:
            raise RuntimeError("OpenRouter user key record is missing")
        return self.config.credentials_dir / f"{key['key_name']}.key"

    def _write_secret(self, user_id: str, plaintext: str) -> None:
        directory = self.config.credentials_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        target = self._secret_path(user_id)
        temporary = target.with_name(
            f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(plaintext)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
            target.chmod(0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _read_secret(self, user_id: str) -> str:
        path = self._secret_path(user_id)
        try:
            plaintext = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("模型额度密钥不可用，请稍后重试") from exc
        if not plaintext or any(character.isspace() for character in plaintext):
            raise RuntimeError("模型额度密钥不可用，请稍后重试")
        return plaintext

    def _delete_secret(self, user_id: str) -> None:
        try:
            self._secret_path(user_id).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, OpenRouterAPIError):
            return str(exc)
        if isinstance(exc, OpenRouterCoverageError):
            return str(exc)
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return "OpenRouter request failed temporarily"
        return type(exc).__name__


class OpenRouterProvisioningWorker:
    def __init__(self, service: OpenRouterProvisioningService) -> None:
        self.service = service
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self.healthy = True

    def start(self) -> None:
        if not self.service.config.active or self._task is not None:
            return
        self.service.store.recover_leases()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="openrouter-provisioning")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.service.run_once()
                if not processed:
                    await self.service.refresh_balance()
                self.healthy = True
            except Exception:
                processed = False
                self.healthy = False
                logger.exception("OpenRouter provisioning worker failed")
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.service.config.sync_interval_seconds,
                )
            except TimeoutError:
                pass
