from __future__ import annotations

import html
import os

import resend
from fastapi import HTTPException


def send_email_code(*, email: str, code: str, purpose: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("RESEND_FROM_EMAIL", "").strip()
    if not api_key or api_key == "re_xxxxxxxxx" or not from_email:
        raise HTTPException(status_code=503, detail="邮箱验证码服务尚未配置")

    resend.api_key = api_key
    safe_code = html.escape(code)
    is_registration = purpose == "register"
    subject = "OpenClass 注册验证码" if is_registration else "OpenClass 登录验证码"
    heading = "注册 OpenClass" if is_registration else "登录 OpenClass"
    try:
        resend.Emails.send(
            {
                "from": from_email,
                "to": [email],
                "subject": subject,
                "html": (
                    '<div style="font-family:system-ui,sans-serif;line-height:1.6">'
                    f"<h2>{heading}</h2>"
                    f'<p>你的验证码是：</p><p style="font-size:28px;font-weight:700;letter-spacing:6px">{safe_code}</p>'
                    "<p>验证码 10 分钟内有效。如果不是你本人操作，请忽略此邮件。</p>"
                    "</div>"
                ),
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="验证码邮件发送失败，请稍后重试") from exc
