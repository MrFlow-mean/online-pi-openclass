from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.billing_service import BillingConfig, BillingError, BillingService


def _config(*, webhook_id: str = "webhook-1") -> BillingConfig:
    return BillingConfig(
        mode="sandbox",
        client_id="client-id",
        client_secret="client-secret",
        webhook_id=webhook_id,
        currency="USD",
        public_origin="https://openclass.example",
        credit_value_percent=75,
        top_up_amounts_cents=(500, 10000),
    )


def _paypal_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path == "/v2/checkout/orders":
            return httpx.Response(
                201,
                json={
                    "id": "ORDER-1",
                    "status": "CREATED",
                    "links": [{"rel": "approve", "href": "https://paypal.example/approve"}],
                },
            )
        if request.url.path == "/v2/checkout/orders/ORDER-1/capture":
            return httpx.Response(
                201,
                json={
                    "id": "ORDER-1",
                    "status": "COMPLETED",
                    "purchase_units": [
                        {
                            "payments": {
                                "captures": [
                                    {
                                        "id": "CAPTURE-1",
                                        "status": "COMPLETED",
                                        "amount": {"currency_code": "USD", "value": "100.00"},
                                    }
                                ]
                            }
                        }
                    ],
                },
            )
        if request.url.path == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS"})
        return httpx.Response(404, json={"name": "NOT_FOUND"})

    return httpx.MockTransport(handler)


def test_create_capture_and_repeat_are_idempotent(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    service = BillingService(tmp_path / "billing.sqlite3", config=_config(), transport=_paypal_transport(requests))

    created = asyncio.run(service.create_paypal_order("user-1", "usd_10000"))
    captured = asyncio.run(service.capture_paypal_order("user-1", str(created["order_id"])))
    repeated = asyncio.run(service.capture_paypal_order("user-1", str(created["order_id"])))

    assert created == {"order_id": "ORDER-1", "approve_url": "https://paypal.example/approve"}
    assert captured["credited"] is True
    assert repeated["credited"] is False
    assert service.wallet("user-1")["balance_credits"] == 7500
    assert len(service.transactions("user-1")) == 1
    create_body = json.loads(next(request.content for request in requests if request.url.path == "/v2/checkout/orders"))
    assert create_body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "100.00"}
    assert create_body["purchase_units"][0]["description"] == "OpenClass 7500 Credits"


def test_completed_webhook_credits_once_and_partial_refund_debits_proportionally(tmp_path: Path) -> None:
    service = BillingService(tmp_path / "billing.sqlite3", config=_config(), transport=_paypal_transport([]))
    asyncio.run(service.create_paypal_order("user-1", "usd_10000"))
    headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://paypal.example/cert",
        "paypal-transmission-id": "transmission-1",
        "paypal-transmission-sig": "signature",
        "paypal-transmission-time": "2026-07-25T00:00:00Z",
    }
    completed_event: dict[str, Any] = {
        "id": "EVENT-CAPTURE",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAPTURE-1",
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "100.00"},
            "supplementary_data": {"related_ids": {"order_id": "ORDER-1"}},
        },
    }
    assert asyncio.run(service.process_webhook(headers, completed_event)) is True
    assert asyncio.run(service.process_webhook(headers, completed_event)) is False

    refund_event = {
        "id": "EVENT-REFUND",
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": "REFUND-1",
            "amount": {"currency_code": "USD", "value": "20.00"},
            "supplementary_data": {"related_ids": {"capture_id": "CAPTURE-1"}},
        },
    }
    assert asyncio.run(service.process_webhook(headers, refund_event)) is True
    assert service.wallet("user-1")["balance_credits"] == 6000
    assert [entry["delta_credits"] for entry in service.transactions("user-1")] == [-1500, 7500]


def test_capture_rejects_mismatched_amount(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path == "/v2/checkout/orders":
            return httpx.Response(201, json={"id": "ORDER-1", "status": "CREATED", "links": [{"rel": "approve", "href": "https://paypal.example/approve"}]})
        return httpx.Response(
            201,
            json={
                "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "CAPTURE-1", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "99.00"}}]}}],
            },
        )

    service = BillingService(tmp_path / "billing.sqlite3", config=_config(), transport=httpx.MockTransport(handler))
    asyncio.run(service.create_paypal_order("user-1", "usd_10000"))
    with pytest.raises(BillingError, match="金额与订单不一致"):
        asyncio.run(service.capture_paypal_order("user-1", "ORDER-1"))
    assert service.wallet("user-1")["balance_credits"] == 0


def test_model_call_reservation_settles_actual_cost_and_is_idempotent(tmp_path: Path) -> None:
    service = BillingService(tmp_path / "billing.sqlite3", config=_config(), transport=_paypal_transport([]))
    asyncio.run(service.create_paypal_order("user-1", "usd_10000"))
    asyncio.run(service.capture_paypal_order("user-1", "ORDER-1"))

    assert service.reserve_model_call(
        user_id="user-1",
        request_id="request-1",
        provider="deepseek",
        model="deepseek-v4-flash",
        reserve_credits=25,
    ) == 25
    assert service.wallet("user-1")["available_credits"] == 7475
    charged = service.settle_model_call(
        request_id="request-1",
        upstream_cost_usd=Decimal("0.1234"),
        usage={"input_tokens": 100, "output_tokens": 20},
    )

    assert charged == 13
    assert service.settle_model_call(
        request_id="request-1",
        upstream_cost_usd=Decimal("99"),
        usage={},
    ) == 13
    assert service.wallet("user-1")["balance_credits"] == 7487
    usage_entry = service.transactions("user-1")[0]
    assert usage_entry["delta_credits"] == -13
    assert usage_entry["upstream_cost_microusd"] == 123400


def test_model_call_reservation_rejects_insufficient_available_credits(tmp_path: Path) -> None:
    service = BillingService(tmp_path / "billing.sqlite3", config=_config())

    with pytest.raises(BillingError) as error:
        service.reserve_model_call(
            user_id="user-1",
            request_id="request-1",
            provider="deepseek",
            model="deepseek-v4-flash",
            reserve_credits=25,
        )

    assert error.value.status_code == 402
    assert service.wallet("user-1")["reserved_credits"] == 0
