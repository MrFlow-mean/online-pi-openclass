from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import httpx


class BillingError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class BillingConfig:
    mode: str
    client_id: str
    client_secret: str
    webhook_id: str
    currency: str
    public_origin: str
    credit_value_percent: int
    top_up_amounts_cents: tuple[int, ...]

    @property
    def api_origin(self) -> str:
        if self.mode == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @classmethod
    def from_env(cls) -> BillingConfig:
        mode = os.getenv("OPENCLASS_PAYPAL_MODE", "sandbox").strip().lower()
        if mode not in {"sandbox", "live"}:
            raise RuntimeError("OPENCLASS_PAYPAL_MODE must be sandbox or live")
        currency = os.getenv("OPENCLASS_PAYPAL_CURRENCY", "USD").strip().upper()
        if currency != "USD":
            raise RuntimeError("OpenClass credit top-ups currently require USD")
        try:
            credit_value_percent = int(os.getenv("OPENCLASS_CREDIT_VALUE_PERCENT", "75"))
        except ValueError as exc:
            raise RuntimeError("OPENCLASS_CREDIT_VALUE_PERCENT must be an integer") from exc
        if not 1 <= credit_value_percent <= 100:
            raise RuntimeError("OPENCLASS_CREDIT_VALUE_PERCENT must be between 1 and 100")
        raw_amounts = os.getenv("OPENCLASS_PAYPAL_TOP_UP_USD", "5,10,20,50,100")
        amounts: list[int] = []
        for raw_amount in raw_amounts.split(","):
            try:
                amount = Decimal(raw_amount.strip())
            except InvalidOperation as exc:
                raise RuntimeError("OPENCLASS_PAYPAL_TOP_UP_USD contains an invalid amount") from exc
            amount_cents = int(amount * 100)
            if amount <= 0 or amount != Decimal(amount_cents) / 100:
                raise RuntimeError("PayPal top-up amounts must be positive with at most two decimals")
            amounts.append(amount_cents)
        if not amounts:
            raise RuntimeError("At least one PayPal top-up amount is required")
        public_origin = (
            os.getenv("OPENCLASS_WEB_ORIGIN")
            or os.getenv("OPENCLASS_PUBLIC_ORIGIN")
            or "http://localhost:3000"
        ).rstrip("/")
        return cls(
            mode=mode,
            client_id=os.getenv("OPENCLASS_PAYPAL_CLIENT_ID", "").strip(),
            client_secret=os.getenv("OPENCLASS_PAYPAL_CLIENT_SECRET", "").strip(),
            webhook_id=os.getenv("OPENCLASS_PAYPAL_WEBHOOK_ID", "").strip(),
            currency=currency,
            public_origin=public_origin,
            credit_value_percent=credit_value_percent,
            top_up_amounts_cents=tuple(dict.fromkeys(amounts)),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_object(value: Any, *, detail: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BillingError(502, detail)
    return value


class BillingService:
    def __init__(
        self,
        database_path: Path,
        *,
        config: BillingConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.database_path = database_path
        self.config = config or BillingConfig.from_env()
        self.transport = transport
        self._lock = threading.RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS credit_wallets (
                    user_id TEXT PRIMARY KEY,
                    balance_credits INTEGER NOT NULL DEFAULT 0,
                    reserved_credits INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS credit_ledger (
                    entry_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    delta_credits INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reference_id TEXT NOT NULL UNIQUE,
                    provider TEXT,
                    model TEXT,
                    upstream_cost_microusd INTEGER,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_credit_ledger_user_created
                    ON credit_ledger(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS paypal_orders (
                    paypal_order_id TEXT PRIMARY KEY,
                    local_order_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    capture_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_paypal_orders_user_created
                    ON paypal_orders(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS paypal_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS credit_reservations (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reserved_credits INTEGER NOT NULL,
                    charged_credits INTEGER,
                    upstream_cost_microusd INTEGER,
                    status TEXT NOT NULL,
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_credit_reservations_user_created
                    ON credit_reservations(user_id, created_at DESC);
                """
            )

    def packages(self) -> list[dict[str, object]]:
        return [
            {
                "id": f"usd_{amount_cents}",
                "amount_cents": amount_cents,
                "amount_usd": f"{Decimal(amount_cents) / 100:.2f}",
                "credits": self._credits_for_amount(amount_cents),
            }
            for amount_cents in self.config.top_up_amounts_cents
        ]

    def wallet(self, user_id: str) -> dict[str, object]:
        with self._transaction() as connection:
            row = self._wallet_row(connection, user_id)
            return self._wallet_view(row)

    def transactions(self, user_id: str, *, limit: int = 50) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 100))
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT entry_id, kind, delta_credits, balance_after, provider, model,
                       upstream_cost_microusd, metadata_json, created_at
                FROM credit_ledger
                WHERE user_id = ?
                ORDER BY created_at DESC, entry_id DESC
                LIMIT ?
                """,
                (user_id, safe_limit),
            ).fetchall()
        return [
            {
                "entry_id": str(row["entry_id"]),
                "kind": str(row["kind"]),
                "delta_credits": int(row["delta_credits"]),
                "balance_after": int(row["balance_after"]),
                "provider": row["provider"],
                "model": row["model"],
                "upstream_cost_microusd": row["upstream_cost_microusd"],
                "metadata": json.loads(str(row["metadata_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def reserve_model_call(
        self,
        *,
        user_id: str,
        request_id: str,
        provider: str,
        model: str,
        reserve_credits: int | None = None,
    ) -> int:
        credits = reserve_credits if reserve_credits is not None else self._model_call_reserve_credits()
        if credits <= 0:
            raise RuntimeError("Model call reserve credits must be positive")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT status, reserved_credits FROM credit_reservations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if existing["status"] == "reserved":
                    return int(existing["reserved_credits"])
                raise BillingError(409, "模型请求计费标识已结算")
            wallet = self._wallet_row(connection, user_id)
            available = int(wallet["balance_credits"]) - int(wallet["reserved_credits"])
            if available < credits:
                raise BillingError(402, "积分余额不足，请先充值")
            timestamp = _now()
            connection.execute(
                "UPDATE credit_wallets SET reserved_credits = reserved_credits + ?, updated_at = ? WHERE user_id = ?",
                (credits, timestamp, user_id),
            )
            connection.execute(
                """
                INSERT INTO credit_reservations (
                    request_id, user_id, provider, model, reserved_credits,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (request_id, user_id, provider, model, credits, timestamp, timestamp),
            )
        return credits

    def settle_model_call(
        self,
        *,
        request_id: str,
        upstream_cost_usd: Decimal,
        usage: dict[str, object],
    ) -> int:
        if upstream_cost_usd < 0:
            raise RuntimeError("Upstream model cost cannot be negative")
        upstream_cost_microusd = int(
            (upstream_cost_usd * 1_000_000).to_integral_value(rounding=ROUND_CEILING)
        )
        charged_credits = self._credits_for_upstream_cost(upstream_cost_usd)
        with self._transaction() as connection:
            reservation = connection.execute(
                "SELECT * FROM credit_reservations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if reservation is None:
                raise BillingError(404, "模型请求计费预授权不存在")
            if reservation["status"] == "settled":
                return int(reservation["charged_credits"] or 0)
            if reservation["status"] != "reserved":
                raise BillingError(409, "模型请求计费预授权已释放")
            wallet = self._wallet_row(connection, str(reservation["user_id"]))
            balance_after = int(wallet["balance_credits"]) - charged_credits
            reserved_after = max(
                0,
                int(wallet["reserved_credits"]) - int(reservation["reserved_credits"]),
            )
            timestamp = _now()
            connection.execute(
                """
                UPDATE credit_wallets
                SET balance_credits = ?, reserved_credits = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (balance_after, reserved_after, timestamp, reservation["user_id"]),
            )
            if charged_credits:
                connection.execute(
                    """
                    INSERT INTO credit_ledger (
                        entry_id, user_id, kind, delta_credits, balance_after,
                        reference_id, provider, model, upstream_cost_microusd,
                        metadata_json, created_at
                    ) VALUES (?, ?, 'model_usage', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"cle_{uuid.uuid4().hex}",
                        reservation["user_id"],
                        -charged_credits,
                        balance_after,
                        f"model:{request_id}",
                        reservation["provider"],
                        reservation["model"],
                        upstream_cost_microusd,
                        json.dumps(usage, separators=(",", ":")),
                        timestamp,
                    ),
                )
            connection.execute(
                """
                UPDATE credit_reservations
                SET charged_credits = ?, upstream_cost_microusd = ?, status = 'settled',
                    usage_json = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    charged_credits,
                    upstream_cost_microusd,
                    json.dumps(usage, separators=(",", ":")),
                    timestamp,
                    request_id,
                ),
            )
        return charged_credits

    def release_model_call(self, *, request_id: str) -> bool:
        with self._transaction() as connection:
            reservation = connection.execute(
                "SELECT * FROM credit_reservations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if reservation is None or reservation["status"] != "reserved":
                return False
            wallet = self._wallet_row(connection, str(reservation["user_id"]))
            reserved_after = max(
                0,
                int(wallet["reserved_credits"]) - int(reservation["reserved_credits"]),
            )
            timestamp = _now()
            connection.execute(
                "UPDATE credit_wallets SET reserved_credits = ?, updated_at = ? WHERE user_id = ?",
                (reserved_after, timestamp, reservation["user_id"]),
            )
            connection.execute(
                "UPDATE credit_reservations SET status = 'released', updated_at = ? WHERE request_id = ?",
                (timestamp, request_id),
            )
        return True

    async def paypal_client_config(self) -> dict[str, str]:
        self._require_paypal()
        response = await self._paypal_request(
            "POST",
            "/v1/identity/generate-token",
            json_body=None,
        )
        client_token = str(response.get("client_token") or "")
        if not client_token:
            raise BillingError(502, "PayPal 未返回客户端令牌")
        return {
            "client_id": self.config.client_id,
            "client_token": client_token,
            "currency": self.config.currency,
            "mode": self.config.mode,
        }

    async def create_paypal_order(
        self,
        user_id: str,
        package_id: str,
        *,
        payment_method: str = "redirect",
    ) -> dict[str, object]:
        self._require_paypal()
        package = next((item for item in self.packages() if item["id"] == package_id), None)
        if package is None:
            raise BillingError(400, "无效的充值套餐")
        if payment_method not in {"redirect", "paypal", "card", "apple_pay", "google_pay"}:
            raise BillingError(400, "无效的 PayPal 付款方式")
        local_order_id = f"oc_{uuid.uuid4().hex}"
        return_url = f"{self.config.public_origin}/wallet?paypal=approved"
        cancel_url = f"{self.config.public_origin}/wallet?paypal=cancelled"
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": local_order_id,
                    "custom_id": local_order_id,
                    "description": f"OpenClass {package['credits']} Credits",
                    "amount": {
                        "currency_code": self.config.currency,
                        "value": package["amount_usd"],
                    },
                }
            ],
        }
        if payment_method == "redirect":
            payload["payment_source"] = {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                    }
                }
            }
        elif payment_method == "card":
            payload["payment_source"] = {
                "card": {
                    "attributes": {
                        "verification": {"method": "SCA_WHEN_REQUIRED"},
                    },
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "shipping_preference": "NO_SHIPPING",
                    },
                }
            }
        response = await self._paypal_request(
            "POST",
            "/v2/checkout/orders",
            json_body=payload,
            request_id=local_order_id,
        )
        paypal_order_id = str(response.get("id") or "")
        approve_url = next(
            (
                str(link.get("href"))
                for link in response.get("links", [])
                if isinstance(link, dict) and link.get("rel") in {"payer-action", "approve"}
            ),
            "",
        )
        if not paypal_order_id or (payment_method == "redirect" and not approve_url):
            raise BillingError(502, "PayPal 未返回可用的订单")
        timestamp = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO paypal_orders (
                    paypal_order_id, local_order_id, user_id, amount_cents, currency,
                    credits, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paypal_order_id,
                    local_order_id,
                    user_id,
                    int(package["amount_cents"]),
                    self.config.currency,
                    int(package["credits"]),
                    str(response.get("status") or "CREATED"),
                    timestamp,
                    timestamp,
                ),
            )
        return {"order_id": paypal_order_id, "approve_url": approve_url or None}

    async def capture_paypal_order(self, user_id: str, paypal_order_id: str) -> dict[str, object]:
        self._require_paypal()
        order = self._owned_order(user_id, paypal_order_id)
        if order["status"] == "COMPLETED" and order["capture_id"]:
            return {
                "order_id": paypal_order_id,
                "status": "COMPLETED",
                "credited": False,
                "wallet": self.wallet(user_id),
            }
        response = await self._paypal_request(
            "POST",
            f"/v2/checkout/orders/{paypal_order_id}/capture",
            json_body={},
            request_id=f"capture-{paypal_order_id}",
        )
        capture = self._completed_capture(response)
        credited = self._credit_capture(paypal_order_id, capture)
        return {
            "order_id": paypal_order_id,
            "status": "COMPLETED",
            "credited": credited,
            "wallet": self.wallet(user_id),
        }

    async def process_webhook(self, headers: dict[str, str], event: dict[str, Any]) -> bool:
        self._require_paypal()
        if not self.config.webhook_id:
            raise BillingError(503, "PayPal Webhook 尚未配置")
        verification = await self._paypal_request(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            json_body={
                "auth_algo": headers.get("paypal-auth-algo", ""),
                "cert_url": headers.get("paypal-cert-url", ""),
                "transmission_id": headers.get("paypal-transmission-id", ""),
                "transmission_sig": headers.get("paypal-transmission-sig", ""),
                "transmission_time": headers.get("paypal-transmission-time", ""),
                "webhook_id": self.config.webhook_id,
                "webhook_event": event,
            },
        )
        if verification.get("verification_status") != "SUCCESS":
            raise BillingError(400, "PayPal Webhook 签名验证失败")
        return self._apply_webhook_event(event)

    def _apply_webhook_event(self, event: dict[str, Any]) -> bool:
        event_id = str(event.get("id") or "")
        event_type = str(event.get("event_type") or "")
        if not event_id or not event_type:
            raise BillingError(400, "PayPal Webhook 缺少事件标识")
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM paypal_webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone():
                return False
            resource = _json_object(event.get("resource"), detail="PayPal Webhook 资源格式无效")
            if event_type == "PAYMENT.CAPTURE.COMPLETED":
                paypal_order_id = self._related_order_id(resource)
                capture = self._validate_capture(resource)
                self._credit_capture_in_transaction(connection, paypal_order_id, capture)
            elif event_type in {"PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"}:
                self._debit_refund_in_transaction(connection, event_id, event_type, resource)
            connection.execute(
                "INSERT INTO paypal_webhook_events (event_id, event_type, processed_at) VALUES (?, ?, ?)",
                (event_id, event_type, _now()),
            )
        return True

    def _credit_capture(self, paypal_order_id: str, capture: dict[str, Any]) -> bool:
        with self._transaction() as connection:
            return self._credit_capture_in_transaction(connection, paypal_order_id, capture)

    def _credit_capture_in_transaction(
        self,
        connection: sqlite3.Connection,
        paypal_order_id: str,
        capture: dict[str, Any],
    ) -> bool:
        order = connection.execute(
            "SELECT * FROM paypal_orders WHERE paypal_order_id = ?",
            (paypal_order_id,),
        ).fetchone()
        if order is None:
            raise BillingError(404, "PayPal 订单不存在")
        capture_id = str(capture["id"])
        amount = _json_object(capture.get("amount"), detail="PayPal 收款金额格式无效")
        amount_cents = self._amount_to_cents(amount.get("value"))
        currency = str(amount.get("currency_code") or "")
        if amount_cents != int(order["amount_cents"]) or currency != str(order["currency"]):
            raise BillingError(409, "PayPal 收款金额与订单不一致")
        reference_id = f"paypal:capture:{capture_id}"
        if connection.execute(
            "SELECT 1 FROM credit_ledger WHERE reference_id = ?",
            (reference_id,),
        ).fetchone():
            connection.execute(
                "UPDATE paypal_orders SET status = 'COMPLETED', capture_id = ?, updated_at = ? WHERE paypal_order_id = ?",
                (capture_id, _now(), paypal_order_id),
            )
            return False
        wallet = self._wallet_row(connection, str(order["user_id"]))
        balance_after = int(wallet["balance_credits"]) + int(order["credits"])
        timestamp = _now()
        connection.execute(
            "UPDATE credit_wallets SET balance_credits = ?, updated_at = ? WHERE user_id = ?",
            (balance_after, timestamp, order["user_id"]),
        )
        connection.execute(
            """
            INSERT INTO credit_ledger (
                entry_id, user_id, kind, delta_credits, balance_after, reference_id,
                metadata_json, created_at
            ) VALUES (?, ?, 'paypal_top_up', ?, ?, ?, ?, ?)
            """,
            (
                f"cle_{uuid.uuid4().hex}",
                order["user_id"],
                order["credits"],
                balance_after,
                reference_id,
                json.dumps(
                    {
                        "paypal_order_id": paypal_order_id,
                        "amount_cents": order["amount_cents"],
                        "currency": order["currency"],
                    },
                    separators=(",", ":"),
                ),
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE paypal_orders SET status = 'COMPLETED', capture_id = ?, updated_at = ? WHERE paypal_order_id = ?",
            (capture_id, timestamp, paypal_order_id),
        )
        return True

    def _debit_refund_in_transaction(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        event_type: str,
        resource: dict[str, Any],
    ) -> None:
        related_ids = _json_object(
            _json_object(resource.get("supplementary_data"), detail="PayPal 退款缺少关联信息").get("related_ids"),
            detail="PayPal 退款缺少关联信息",
        )
        capture_id = str(related_ids.get("capture_id") or "")
        order = connection.execute(
            "SELECT * FROM paypal_orders WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()
        if order is None:
            raise BillingError(404, "退款对应的 PayPal 订单不存在")
        reference_id = f"paypal:refund:{event_id}"
        if connection.execute(
            "SELECT 1 FROM credit_ledger WHERE reference_id = ?",
            (reference_id,),
        ).fetchone():
            return
        amount = _json_object(resource.get("amount"), detail="PayPal 退款金额格式无效")
        if str(amount.get("currency_code") or "") != str(order["currency"]):
            raise BillingError(409, "PayPal 退款币种与订单不一致")
        refund_cents = self._amount_to_cents(amount.get("value"))
        if refund_cents <= 0 or refund_cents > int(order["amount_cents"]):
            raise BillingError(409, "PayPal 退款金额超出订单范围")
        credits_to_reverse = math.ceil(int(order["credits"]) * refund_cents / int(order["amount_cents"]))
        wallet = self._wallet_row(connection, str(order["user_id"]))
        balance_after = int(wallet["balance_credits"]) - credits_to_reverse
        timestamp = _now()
        connection.execute(
            "UPDATE credit_wallets SET balance_credits = ?, updated_at = ? WHERE user_id = ?",
            (balance_after, timestamp, order["user_id"]),
        )
        connection.execute(
            """
            INSERT INTO credit_ledger (
                entry_id, user_id, kind, delta_credits, balance_after, reference_id,
                metadata_json, created_at
            ) VALUES (?, ?, 'paypal_refund', ?, ?, ?, ?, ?)
            """,
            (
                f"cle_{uuid.uuid4().hex}",
                order["user_id"],
                -credits_to_reverse,
                balance_after,
                reference_id,
                json.dumps(
                    {
                        "paypal_order_id": order["paypal_order_id"],
                        "capture_id": capture_id,
                        "refund_cents": refund_cents,
                        "event_type": event_type,
                    },
                    separators=(",", ":"),
                ),
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE paypal_orders SET status = ?, updated_at = ? WHERE paypal_order_id = ?",
            ("REFUNDED" if refund_cents == int(order["amount_cents"]) else "PARTIALLY_REFUNDED", timestamp, order["paypal_order_id"]),
        )

    def _owned_order(self, user_id: str, paypal_order_id: str) -> sqlite3.Row:
        with self._transaction() as connection:
            order = connection.execute(
                "SELECT * FROM paypal_orders WHERE paypal_order_id = ? AND user_id = ?",
                (paypal_order_id, user_id),
            ).fetchone()
        if order is None:
            raise BillingError(404, "PayPal 订单不存在")
        return order

    def _wallet_row(self, connection: sqlite3.Connection, user_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM credit_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is not None:
            return row
        timestamp = _now()
        connection.execute(
            "INSERT INTO credit_wallets (user_id, balance_credits, reserved_credits, updated_at) VALUES (?, 0, 0, ?)",
            (user_id, timestamp),
        )
        row = connection.execute(
            "SELECT * FROM credit_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        assert row is not None
        return row

    def _wallet_view(self, row: sqlite3.Row) -> dict[str, object]:
        balance = int(row["balance_credits"])
        reserved = int(row["reserved_credits"])
        return {
            "user_id": str(row["user_id"]),
            "balance_credits": balance,
            "reserved_credits": reserved,
            "available_credits": balance - reserved,
            "paypal_configured": self.config.configured,
            "currency": self.config.currency,
            "updated_at": str(row["updated_at"]),
        }

    def _credits_for_amount(self, amount_cents: int) -> int:
        return amount_cents

    def _credits_for_upstream_cost(self, upstream_cost_usd: Decimal) -> int:
        credit_value_usd = Decimal(self.config.credit_value_percent) / Decimal(10_000)
        return int((upstream_cost_usd / credit_value_usd).to_integral_value(rounding=ROUND_CEILING))

    def _model_call_reserve_credits(self) -> int:
        raw_value = os.getenv("OPENCLASS_MODEL_CALL_RESERVE_CREDITS", "25").strip()
        try:
            reserve_credits = int(raw_value)
        except ValueError as exc:
            raise RuntimeError("OPENCLASS_MODEL_CALL_RESERVE_CREDITS must be an integer") from exc
        if not 1 <= reserve_credits <= 100_000:
            raise RuntimeError("OPENCLASS_MODEL_CALL_RESERVE_CREDITS must be between 1 and 100000")
        return reserve_credits

    def _completed_capture(self, order: dict[str, Any]) -> dict[str, Any]:
        if order.get("status") != "COMPLETED":
            raise BillingError(409, "PayPal 订单尚未完成")
        purchase_units = order.get("purchase_units")
        if not isinstance(purchase_units, list) or len(purchase_units) != 1:
            raise BillingError(502, "PayPal 订单明细格式无效")
        payments = _json_object(purchase_units[0].get("payments"), detail="PayPal 收款明细缺失")
        captures = payments.get("captures")
        if not isinstance(captures, list) or len(captures) != 1:
            raise BillingError(502, "PayPal 收款记录格式无效")
        return self._validate_capture(_json_object(captures[0], detail="PayPal 收款记录格式无效"))

    def _validate_capture(self, capture: dict[str, Any]) -> dict[str, Any]:
        if capture.get("status") != "COMPLETED" or not capture.get("id"):
            raise BillingError(409, "PayPal 收款尚未完成")
        return capture

    def _related_order_id(self, resource: dict[str, Any]) -> str:
        supplementary_data = _json_object(
            resource.get("supplementary_data"),
            detail="PayPal 收款缺少关联订单",
        )
        related_ids = _json_object(
            supplementary_data.get("related_ids"),
            detail="PayPal 收款缺少关联订单",
        )
        paypal_order_id = str(related_ids.get("order_id") or "")
        if not paypal_order_id:
            raise BillingError(400, "PayPal 收款缺少订单标识")
        return paypal_order_id

    def _amount_to_cents(self, value: Any) -> int:
        try:
            amount = Decimal(str(value))
        except InvalidOperation as exc:
            raise BillingError(502, "PayPal 金额格式无效") from exc
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise BillingError(502, "PayPal 金额精度无效")
        return int(cents)

    def _require_paypal(self) -> None:
        if not self.config.configured:
            raise BillingError(503, "PayPal 支付尚未配置")

    async def _paypal_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.config.api_origin, transport=self.transport, timeout=30) as client:
            token_response = await client.post(
                "/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self.config.client_id, self.config.client_secret),
                headers={"Accept": "application/json", "Accept-Language": "en_US"},
            )
            if token_response.status_code >= 400:
                raise BillingError(502, "PayPal 身份验证失败")
            token_payload = _json_object(token_response.json(), detail="PayPal 身份验证响应无效")
            access_token = str(token_payload.get("access_token") or "")
            if not access_token:
                raise BillingError(502, "PayPal 身份验证响应缺少令牌")
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            request_options: dict[str, Any] = {"headers": headers}
            if json_body is not None:
                request_options["json"] = json_body
            response = await client.request(method, path, **request_options)
        if response.status_code >= 400:
            raise BillingError(502, f"PayPal 请求失败（{response.status_code}）")
        return _json_object(response.json(), detail="PayPal 响应格式无效")
