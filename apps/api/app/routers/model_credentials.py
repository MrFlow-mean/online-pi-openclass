from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models import (
    AIProviderCredentialInput,
    AIProviderCredentialStatus,
    UserView,
)
from app.routers.auth import current_user
from app.services.ai_model_catalog import text_model_provider_enabled
from app.services.pi_agent_runtime import (
    PI_PERSONAL_API_PROVIDERS,
    pi_personal_api_configured,
    remove_pi_personal_api_key,
    save_pi_personal_api_key,
)


router = APIRouter(prefix="/api/model-credentials")
_PROVIDER_LABELS = {"deepseek": "DeepSeek"}


def _registered_user(user: UserView) -> None:
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="登录后才能保存个人 API Key")


def _provider(provider: str) -> str:
    normalized = provider.strip().lower().replace("_", "-")
    if (
        normalized not in PI_PERSONAL_API_PROVIDERS
        or not text_model_provider_enabled(normalized)
    ):
        raise HTTPException(status_code=400, detail="该模型服务商暂不支持个人 API Key")
    return normalized


def _status(*, user: UserView, provider: str) -> AIProviderCredentialStatus:
    return AIProviderCredentialStatus(
        provider=provider,
        label=_PROVIDER_LABELS.get(provider, provider),
        configured=pi_personal_api_configured(
            owner_user_id=user.id,
            provider=provider,
        ),
        manageable=user.role != "guest",
    )


@router.get("", response_model=list[AIProviderCredentialStatus])
def list_model_credentials(
    user: UserView = Depends(current_user),
) -> list[AIProviderCredentialStatus]:
    return [
        _status(user=user, provider=provider)
        for provider in sorted(PI_PERSONAL_API_PROVIDERS)
        if text_model_provider_enabled(provider)
    ]


@router.put("/{provider}", response_model=AIProviderCredentialStatus)
def save_model_credential(
    provider: str,
    payload: AIProviderCredentialInput,
    user: UserView = Depends(current_user),
) -> AIProviderCredentialStatus:
    _registered_user(user)
    normalized_provider = _provider(provider)
    try:
        save_pi_personal_api_key(
            owner_user_id=user.id,
            provider=normalized_provider,
            api_key=payload.api_key.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status(user=user, provider=normalized_provider)


@router.delete("/{provider}", response_model=AIProviderCredentialStatus)
def delete_model_credential(
    provider: str,
    user: UserView = Depends(current_user),
) -> AIProviderCredentialStatus:
    _registered_user(user)
    normalized_provider = _provider(provider)
    remove_pi_personal_api_key(
        owner_user_id=user.id,
        provider=normalized_provider,
    )
    return _status(user=user, provider=normalized_provider)
