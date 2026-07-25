from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.models import UserView
from app.routers.auth import current_user
from app.services.billing_service import BillingError, BillingService
from app.services.workspace_state import DATABASE_PATH


router = APIRouter(prefix="/api/billing", tags=["billing"])
billing_service = BillingService(DATABASE_PATH)


class PayPalOrderRequest(BaseModel):
    package_id: str = Field(min_length=1, max_length=40)
    payment_method: Literal["redirect", "paypal", "card", "apple_pay", "google_pay"] = "redirect"


class PayPalOrderResponse(BaseModel):
    order_id: str
    approve_url: str | None = None


class PayPalClientConfigResponse(BaseModel):
    client_id: str
    client_token: str
    currency: str
    mode: Literal["sandbox", "live"]


class PayPalCaptureResponse(BaseModel):
    order_id: str
    status: str
    credited: bool
    wallet: dict[str, object]


def _member(user: UserView = Depends(current_user)) -> UserView:
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="请先登录正式账号再使用积分钱包")
    return user


def _raise_billing_error(error: BillingError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get("/wallet")
def wallet(user: UserView = Depends(_member)) -> dict[str, object]:
    return {
        "wallet": billing_service.wallet(user.id),
        "packages": billing_service.packages(),
    }


@router.get("/transactions")
def transactions(
    limit: int = Query(default=50, ge=1, le=100),
    user: UserView = Depends(_member),
) -> list[dict[str, object]]:
    return billing_service.transactions(user.id, limit=limit)


@router.get("/paypal/client-config", response_model=PayPalClientConfigResponse)
async def paypal_client_config(
    response: Response,
    user: UserView = Depends(_member),
) -> dict[str, str]:
    del user
    response.headers["Cache-Control"] = "no-store"
    try:
        return await billing_service.paypal_client_config()
    except BillingError as error:
        _raise_billing_error(error)


@router.post("/paypal/orders", response_model=PayPalOrderResponse)
async def create_paypal_order(
    payload: PayPalOrderRequest,
    user: UserView = Depends(_member),
) -> dict[str, object]:
    try:
        return await billing_service.create_paypal_order(
            user.id,
            payload.package_id,
            payment_method=payload.payment_method,
        )
    except BillingError as error:
        _raise_billing_error(error)


@router.post("/paypal/orders/{order_id}/capture", response_model=PayPalCaptureResponse)
async def capture_paypal_order(
    order_id: str,
    user: UserView = Depends(_member),
) -> dict[str, object]:
    try:
        return await billing_service.capture_paypal_order(user.id, order_id)
    except BillingError as error:
        _raise_billing_error(error)


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request) -> dict[str, object]:
    try:
        payload: Any = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="PayPal Webhook 不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="PayPal Webhook 格式无效")
    try:
        processed = await billing_service.process_webhook(
            {key.lower(): value for key, value in request.headers.items()},
            payload,
        )
    except BillingError as error:
        _raise_billing_error(error)
    return {"received": True, "processed": processed}
