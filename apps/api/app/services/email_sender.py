from __future__ import annotations

import html
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

import resend
from fastapi import HTTPException


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str
    security: str


def _smtp_config() -> SMTPConfig | None:
    host = os.getenv("OPENCLASS_SMTP_HOST", "").strip()
    username = os.getenv("OPENCLASS_SMTP_USERNAME", "").strip()
    password = os.getenv("OPENCLASS_SMTP_PASSWORD", "").strip()
    from_email = os.getenv("OPENCLASS_SMTP_FROM_EMAIL", "").strip() or username
    from_name = os.getenv("OPENCLASS_SMTP_FROM_NAME", "OpenClass").strip() or "OpenClass"
    security = os.getenv("OPENCLASS_SMTP_SECURITY", "ssl").strip().lower()
    configured_values = (host, username, password)
    if not any(configured_values):
        return None
    if not all(configured_values):
        raise HTTPException(status_code=503, detail="SMTP 邮件服务配置不完整")
    if security not in {"ssl", "starttls"}:
        raise HTTPException(status_code=503, detail="SMTP 加密方式配置无效")
    raw_port = os.getenv("OPENCLASS_SMTP_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else (465 if security == "ssl" else 587)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="SMTP 端口配置无效") from exc
    if port < 1 or port > 65535:
        raise HTTPException(status_code=503, detail="SMTP 端口配置无效")
    return SMTPConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        from_email=from_email,
        from_name=from_name,
        security=security,
    )


def _message_content(*, code: str, purpose: str) -> tuple[str, str, str]:
    safe_code = html.escape(code)
    messages = {
        "register": ("OpenClass 注册验证码", "注册 OpenClass"),
        "login": ("OpenClass 登录验证码", "登录 OpenClass"),
        "password_reset": ("OpenClass 重置密码验证码", "重置 OpenClass 密码"),
        "email_verification": ("OpenClass 邮箱验证码", "验证 OpenClass 邮箱"),
    }
    subject, heading = messages.get(purpose, messages["login"])
    plain_text = f"你的 OpenClass 验证码是：{code}\n\n验证码 10 分钟内有效。如果不是你本人操作，请忽略此邮件。"
    html_content = (
        '<div style="font-family:system-ui,sans-serif;line-height:1.6">'
        f"<h2>{heading}</h2>"
        f'<p>你的验证码是：</p><p style="font-size:28px;font-weight:700;letter-spacing:6px">{safe_code}</p>'
        "<p>验证码 10 分钟内有效。如果不是你本人操作，请忽略此邮件。</p>"
        "</div>"
    )
    return subject, plain_text, html_content


def _send_with_smtp(*, config: SMTPConfig, email: str, subject: str, plain_text: str, html_content: str) -> None:
    message = EmailMessage()
    message["From"] = formataddr((config.from_name, config.from_email))
    message["To"] = email
    message["Subject"] = subject
    message.set_content(plain_text)
    message.add_alternative(html_content, subtype="html")
    context = ssl.create_default_context()
    if config.security == "ssl":
        with smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context) as client:
            client.login(config.username, config.password)
            client.send_message(message)
        return
    with smtplib.SMTP(config.host, config.port, timeout=15) as client:
        client.starttls(context=context)
        client.login(config.username, config.password)
        client.send_message(message)


def _send_with_resend(*, email: str, subject: str, html_content: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("RESEND_FROM_EMAIL", "").strip()
    if not api_key or api_key == "re_xxxxxxxxx" or not from_email:
        return False

    resend.api_key = api_key
    resend.Emails.send(
        {
            "from": from_email,
            "to": [email],
            "subject": subject,
            "html": html_content,
        }
    )
    return True


def send_email_code(*, email: str, code: str, purpose: str) -> None:
    subject, plain_text, html_content = _message_content(code=code, purpose=purpose)
    try:
        smtp_config = _smtp_config()
        if smtp_config is not None:
            _send_with_smtp(
                config=smtp_config,
                email=email,
                subject=subject,
                plain_text=plain_text,
                html_content=html_content,
            )
            return
        if _send_with_resend(email=email, subject=subject, html_content=html_content):
            return
        raise HTTPException(status_code=503, detail="邮箱验证码服务尚未配置")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="验证码邮件发送失败，请稍后重试") from exc
