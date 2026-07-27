from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.billing_service import BillingConfig, BillingService
from app.services.openrouter_provisioning import (
    OpenRouterConfig,
    OpenRouterProvisioningService,
)


def _billing_config() -> BillingConfig:
    return BillingConfig(
        mode="sandbox",
        client_id="client-id",
        client_secret="client-secret",
        webhook_id="webhook-id",
        currency="USD",
        public_origin="https://openclass.example",
        credit_value_percent=75,
        top_up_amounts_cents=(500, 10_000),
    )


def _openrouter_config(tmp_path: Path, *, safety_buffer_usd: int = 25) -> OpenRouterConfig:
    return OpenRouterConfig(
        provisioning_enabled=True,
        management_api_key="management-secret",
        api_origin="https://openrouter.example",
        sync_interval_seconds=0.1,
        safety_buffer_microusd=safety_buffer_usd * 1_000_000,
        credentials_dir=tmp_path / "openrouter-credentials",
        model_map={"deepseek:*": "deepseek/deepseek-chat"},
    )


def _record_capture(
    service: BillingService,
    *,
    user_id: str,
    order_id: str,
    capture_id: str,
    amount_cents: int,
) -> bool:
    timestamp = "2026-07-27T00:00:00+00:00"
    with service._transaction() as connection:
        connection.execute(
            """
            INSERT INTO paypal_orders (
                paypal_order_id, local_order_id, user_id, amount_cents, currency,
                credits, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'USD', ?, 'CREATED', ?, ?)
            """,
            (
                order_id,
                f"local-{order_id}",
                user_id,
                amount_cents,
                amount_cents,
                timestamp,
                timestamp,
            ),
        )
    return service._credit_capture(
        order_id,
        {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {
                "currency_code": "USD",
                "value": f"{amount_cents / 100:.2f}",
            },
        },
    )


class FakeOpenRouter:
    def __init__(self, *, credits: float = 1_000) -> None:
        self.credits = credits
        self.total_usage = 0.0
        self.keys: dict[str, dict[str, Any]] = {}
        self.secrets: dict[str, str] = {}
        self.counter = 0
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer management-secret"
        path = request.url.path
        if path == "/api/v1/credits":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "total_credits": self.credits,
                        "total_usage": self.total_usage,
                    }
                },
            )
        if path == "/api/v1/keys" and request.method == "GET":
            return httpx.Response(200, json={"data": list(self.keys.values())})
        if path == "/api/v1/keys" and request.method == "POST":
            body = json.loads(request.content)
            self.counter += 1
            key_hash = f"hash-{self.counter}"
            plaintext = f"sk-or-v1-user-secret-{self.counter}"
            record = {
                "hash": key_hash,
                "label": body["name"],
                "name": body["name"],
                "limit": body["limit"],
                "limit_remaining": body["limit"],
                "limit_reset": body["limit_reset"],
                "usage": 0,
                "disabled": False,
            }
            self.keys[key_hash] = record
            self.secrets[key_hash] = plaintext
            return httpx.Response(201, json={"data": record, "key": plaintext})
        if path.startswith("/api/v1/keys/"):
            key_hash = path.rsplit("/", 1)[-1]
            record = self.keys.get(key_hash)
            if record is None:
                return httpx.Response(404, json={"error": "missing"})
            if request.method == "GET":
                return httpx.Response(200, json={"data": record})
            if request.method == "PATCH":
                body = json.loads(request.content)
                record.update(body)
                record["label"] = body["name"]
                record["limit_remaining"] = max(0, body["limit"] - record["usage"])
                return httpx.Response(200, json={"data": record})
            if request.method == "DELETE":
                del self.keys[key_hash]
                self.secrets.pop(key_hash, None)
                return httpx.Response(200, json={"data": {"deleted": True}})
        return httpx.Response(404, json={"error": "unexpected"})


def test_capture_provisions_private_user_key_and_uses_absolute_cumulative_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_OPENROUTER_PROVISIONING_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_API_KEY", "configured")
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter()
    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=fake.transport(),
    )

    assert _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=10_000,
    ) is True
    assert billing.wallet("user-1")["balance_credits"] == 10_000
    assert billing.wallet("user-1")["model_access_status"] == "syncing"
    assert asyncio.run(provisioning.run_once()) is True

    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["target_limit_microusd"] == 75_000_000
    assert key["status"] == "ready"
    assert fake.keys[str(key["key_hash"])]["limit"] == 75.0
    secret_path = provisioning.config.credentials_dir / f"{key['key_name']}.key"
    assert stat.S_IMODE(provisioning.config.credentials_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    plaintext = secret_path.read_text(encoding="utf-8").strip()
    assert provisioning.api_key_for_user("user-1") == plaintext

    assert _record_capture(
        billing,
        user_id="user-1",
        order_id="order-2",
        capture_id="capture-2",
        amount_cents=10_000,
    ) is True
    assert asyncio.run(provisioning.run_once()) is True
    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["target_limit_microusd"] == 150_000_000
    assert fake.keys[str(key["key_hash"])]["limit"] == 150.0

    database_bytes = billing.database_path.read_bytes()
    assert plaintext.encode("utf-8") not in database_bytes
    assert "management-secret".encode("utf-8") not in database_bytes
    with sqlite3.connect(billing.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM openrouter_limit_grants"
        ).fetchone()[0] == 2


def test_duplicate_capture_creates_one_grant_and_low_shared_balance_blocks_key(
    tmp_path: Path,
) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter(credits=50)
    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=fake.transport(),
    )
    capture = {
        "id": "capture-1",
        "status": "COMPLETED",
        "amount": {"currency_code": "USD", "value": "100.00"},
    }
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=10_000,
    )
    assert billing._credit_capture("order-1", capture) is False
    assert asyncio.run(provisioning.run_once()) is True

    with sqlite3.connect(billing.database_path) as connection:
        connection.row_factory = sqlite3.Row
        grants = connection.execute("SELECT * FROM openrouter_limit_grants").fetchall()
        assert len(grants) == 1
        assert grants[0]["delta_microusd"] == 75_000_000
        assert grants[0]["status"] == "retry"
    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["status"] == "blocked"
    assert fake.keys == {}


def test_partial_refunds_lower_target_and_consumed_refund_disables_key(
    tmp_path: Path,
) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter()
    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=fake.transport(),
    )
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=10_000,
    )
    asyncio.run(provisioning.run_once())
    key = provisioning.store.key("user-1")
    assert key is not None
    fake.keys[str(key["key_hash"])]["usage"] = 70.0

    with billing._transaction() as connection:
        billing._debit_refund_in_transaction(
            connection,
            "event-refund-1",
            "PAYMENT.CAPTURE.REFUNDED",
            {
                "id": "refund-1",
                "amount": {"currency_code": "USD", "value": "20.00"},
                "supplementary_data": {"related_ids": {"capture_id": "capture-1"}},
            },
        )
    assert asyncio.run(provisioning.run_once()) is True
    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["target_limit_microusd"] == 60_000_000
    assert key["usage_microusd"] == 70_000_000
    assert key["status"] == "blocked"
    assert key["disabled"] == 1
    assert fake.keys[str(key["key_hash"])]["disabled"] is True
    assert billing.wallet("user-1")["balance_credits"] == 8_000


def test_two_users_receive_distinct_secrets(tmp_path: Path) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter()
    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=fake.transport(),
    )
    for index in (1, 2):
        _record_capture(
            billing,
            user_id=f"user-{index}",
            order_id=f"order-{index}",
            capture_id=f"capture-{index}",
            amount_cents=500,
        )
    assert asyncio.run(provisioning.run_once()) is True
    assert asyncio.run(provisioning.run_once()) is True

    first = provisioning.store.key("user-1")
    second = provisioning.store.key("user-2")
    assert first is not None and second is not None
    assert first["key_hash"] != second["key_hash"]
    assert first["key_name"] != second["key_name"]
    assert provisioning.api_key_for_user("user-1") != provisioning.api_key_for_user(
        "user-2"
    )
    assert first["target_limit_microusd"] == 3_750_000
    assert second["target_limit_microusd"] == 3_750_000


def test_orphan_key_is_deleted_and_recreated_when_plaintext_was_not_saved(
    tmp_path: Path,
) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter()
    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=fake.transport(),
    )
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=500,
    )
    key = provisioning.store.key("user-1")
    assert key is not None
    fake.keys["orphan-hash"] = {
        "hash": "orphan-hash",
        "label": key["key_name"],
        "name": key["key_name"],
        "limit": 3.75,
        "usage": 0,
        "disabled": False,
    }

    assert asyncio.run(provisioning.run_once()) is True

    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["status"] == "ready"
    assert key["key_hash"] != "orphan-hash"
    assert "orphan-hash" not in fake.keys
    assert provisioning.api_key_for_user("user-1").startswith("sk-or-v1-")


def test_startup_recovery_requeues_leased_grants(tmp_path: Path) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=FakeOpenRouter().transport(),
    )
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=500,
    )
    claimed = provisioning.store.claim(lease_seconds=3_600)
    assert claimed is not None
    assert provisioning.store.recover_leases() == 1
    recovered = provisioning.store.claim()
    assert recovered is not None
    assert recovered["grant_id"] == claimed["grant_id"]


@pytest.mark.parametrize("status_code", [429, 500])
def test_openrouter_api_failures_are_retried_without_losing_the_grant(
    tmp_path: Path,
    status_code: int,
) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())

    def fail_credits(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/credits":
            return httpx.Response(status_code, json={"error": "temporary"})
        return httpx.Response(200, json={"data": []})

    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=httpx.MockTransport(fail_credits),
    )
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=500,
    )

    assert asyncio.run(provisioning.run_once()) is True

    with sqlite3.connect(billing.database_path) as connection:
        connection.row_factory = sqlite3.Row
        grant = connection.execute("SELECT * FROM openrouter_limit_grants").fetchone()
        assert grant is not None
        assert grant["status"] == "retry"
        assert grant["attempts"] == 1
        assert str(status_code) in grant["last_error"]
    assert provisioning.store.target("user-1") == 3_750_000


def test_lost_create_response_recovers_the_orphan_on_retry(tmp_path: Path) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter()
    lose_first_create_response = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lose_first_create_response
        if request.url.path == "/api/v1/keys" and request.method == "POST":
            response = fake.handle(request)
            if lose_first_create_response:
                lose_first_create_response = False
                raise httpx.ReadTimeout("response lost", request=request)
            return response
        return fake.handle(request)

    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=500,
    )
    assert asyncio.run(provisioning.run_once()) is True
    assert list(fake.keys) == ["hash-1"]
    assert not provisioning.config.credentials_dir.exists()
    with sqlite3.connect(billing.database_path) as connection:
        connection.execute(
            "UPDATE openrouter_limit_grants SET available_at = '2000-01-01T00:00:00+00:00'"
        )
        connection.commit()

    assert asyncio.run(provisioning.run_once()) is True

    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["status"] == "ready"
    assert key["key_hash"] == "hash-2"
    assert "hash-1" not in fake.keys
    assert provisioning.api_key_for_user("user-1") == fake.secrets["hash-2"]


def test_capture_arriving_during_remote_sync_remains_pending_until_its_limit_is_applied(
    tmp_path: Path,
) -> None:
    billing = BillingService(tmp_path / "billing.sqlite3", config=_billing_config())
    fake = FakeOpenRouter()
    second_capture_recorded = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal second_capture_recorded
        if (
            request.method == "PATCH"
            and request.url.path.startswith("/api/v1/keys/")
            and not second_capture_recorded
        ):
            second_capture_recorded = True
            _record_capture(
                billing,
                user_id="user-1",
                order_id="order-2",
                capture_id="capture-2",
                amount_cents=10_000,
            )
        return fake.handle(request)

    provisioning = OpenRouterProvisioningService(
        billing,
        config=_openrouter_config(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    _record_capture(
        billing,
        user_id="user-1",
        order_id="order-1",
        capture_id="capture-1",
        amount_cents=10_000,
    )

    assert asyncio.run(provisioning.run_once()) is True
    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["target_limit_microusd"] == 150_000_000
    assert key["status"] == "syncing"
    with sqlite3.connect(billing.database_path) as connection:
        statuses = connection.execute(
            "SELECT status FROM openrouter_limit_grants ORDER BY created_at, grant_id"
        ).fetchall()
    assert [row[0] for row in statuses].count("pending") == 1

    assert asyncio.run(provisioning.run_once()) is True
    key = provisioning.store.key("user-1")
    assert key is not None
    assert key["status"] == "ready"
    assert fake.keys[str(key["key_hash"])]["limit"] == 150.0
