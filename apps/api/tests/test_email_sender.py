from __future__ import annotations

from email.message import EmailMessage

import pytest
from fastapi import HTTPException

from app.services import email_sender


SMTP_ENV_KEYS = (
    "OPENCLASS_SMTP_HOST",
    "OPENCLASS_SMTP_PORT",
    "OPENCLASS_SMTP_SECURITY",
    "OPENCLASS_SMTP_USERNAME",
    "OPENCLASS_SMTP_PASSWORD",
    "OPENCLASS_SMTP_FROM_EMAIL",
    "OPENCLASS_SMTP_FROM_NAME",
)


def _clear_email_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (*SMTP_ENV_KEYS, "RESEND_API_KEY", "RESEND_FROM_EMAIL"):
        monkeypatch.delenv(key, raising=False)


def test_send_email_code_uses_ssl_smtp_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("OPENCLASS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("OPENCLASS_SMTP_PORT", "465")
    monkeypatch.setenv("OPENCLASS_SMTP_SECURITY", "ssl")
    monkeypatch.setenv("OPENCLASS_SMTP_USERNAME", "mailer@example.com")
    monkeypatch.setenv("OPENCLASS_SMTP_PASSWORD", "client-security-password")
    monkeypatch.setenv("OPENCLASS_SMTP_FROM_NAME", "OpenClass Mail")
    sent: dict[str, object] = {}

    class FakeSMTPClient:
        def __init__(self, host: str, port: int, **kwargs: object) -> None:
            sent.update(host=host, port=port, options=kwargs)

        def __enter__(self) -> FakeSMTPClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            sent.update(username=username, password=password)

        def send_message(self, message: EmailMessage) -> None:
            sent["message"] = message

    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", FakeSMTPClient)

    email_sender.send_email_code(email="student@example.com", code="123456", purpose="register")

    assert sent["host"] == "smtp.example.com"
    assert sent["port"] == 465
    assert sent["username"] == "mailer@example.com"
    assert sent["password"] == "client-security-password"
    message = sent["message"]
    assert isinstance(message, EmailMessage)
    assert message["To"] == "student@example.com"
    assert message["From"] == "OpenClass Mail <mailer@example.com>"
    assert message["Subject"] == "OpenClass 注册验证码"
    plain_body = message.get_body(preferencelist=("plain",))
    assert plain_body is not None
    assert "123456" in plain_body.get_content()


def test_send_email_code_falls_back_to_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "re_real_key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "OpenClass <login@example.com>")
    sent: dict[str, object] = {}
    monkeypatch.setattr(email_sender.resend.Emails, "send", lambda payload: sent.update(payload))

    email_sender.send_email_code(email="student@example.com", code="654321", purpose="login")

    assert sent["to"] == ["student@example.com"]
    assert sent["subject"] == "OpenClass 登录验证码"
    assert "654321" in str(sent["html"])


def test_send_email_code_rejects_incomplete_smtp_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_email_env(monkeypatch)
    monkeypatch.setenv("OPENCLASS_SMTP_HOST", "smtp.example.com")

    with pytest.raises(HTTPException) as exc_info:
        email_sender.send_email_code(email="student@example.com", code="123456", purpose="register")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "SMTP 邮件服务配置不完整"


def test_send_email_code_requires_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_email_env(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        email_sender.send_email_code(email="student@example.com", code="123456", purpose="register")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "邮箱验证码服务尚未配置"
