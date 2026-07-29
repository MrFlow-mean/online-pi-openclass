from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.models import UserView
from app.routers.auth import current_user
from app.services.email_sender import send_contact_message
from app.services.rate_limiter import (
    InMemorySlidingWindowRateLimiter,
    RateLimitPolicy,
    anonymized_rate_limit_subject,
    client_ip_from_request,
)


router = APIRouter(prefix="/api", tags=["contact"])
contact_rate_limiter = InMemorySlidingWindowRateLimiter()
CONTACT_RATE_LIMIT = RateLimitPolicy(limit=5, window_seconds=3600)


class ContactMessageRequest(BaseModel):
    subject: str
    message: str

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2 or len(normalized) > 120:
            raise ValueError("联系主题应为 2 到 120 个字符")
        return normalized

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10 or len(normalized) > 4000:
            raise ValueError("联系内容应为 10 到 4000 个字符")
        return normalized


class ContactMessageResponse(BaseModel):
    message: str


def _enforce_contact_rate_limit(request: Request, user_id: str) -> None:
    subjects = (
        ("contact:user", user_id),
        ("contact:ip", client_ip_from_request(request)),
    )
    decisions = [
        contact_rate_limiter.check(
            namespace,
            anonymized_rate_limit_subject(subject),
            CONTACT_RATE_LIMIT,
        )
        for namespace, subject in subjects
    ]
    blocked = [decision for decision in decisions if not decision.allowed]
    if blocked:
        retry_after = max(decision.retry_after_seconds for decision in blocked)
        raise HTTPException(
            status_code=429,
            detail="联系消息发送过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/contact", response_model=ContactMessageResponse)
def submit_contact_message(
    payload: ContactMessageRequest,
    request: Request,
    user: UserView = Depends(current_user),
) -> ContactMessageResponse:
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="登录正式账号后才能发送联系消息")
    _enforce_contact_rate_limit(request, user.id)
    send_contact_message(
        sender_email=user.email,
        sender_name=user.display_name or user.email,
        user_id=user.id,
        subject=payload.subject,
        message=payload.message,
    )
    return ContactMessageResponse(message="联系消息已发送")
