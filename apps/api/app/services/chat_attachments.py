from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import ChatAttachmentRef, SourceIngestionRecord
from app.services.codex_app_server import CodexAppServerError
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import source_download_path
MAX_CHAT_ATTACHMENT_IMAGE_DATA_URL_CHARS = 20 * 1024 * 1024
SUPPORTED_CHAT_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


@dataclass(frozen=True)
class VerifiedChatAttachment:
    source: SourceIngestionRecord
    path: Path


@dataclass(frozen=True)
class PreparedChatAttachments:
    prompt_context: str
    image_inputs: list[str]
    metadata: list[dict[str, Any]]


def verify_chat_attachments(
    *,
    owner_user_id: str,
    package_id: str,
    attachments: list[ChatAttachmentRef],
) -> list[VerifiedChatAttachment]:
    verified: list[VerifiedChatAttachment] = []
    seen: set[str] = set()
    for attachment in attachments:
        source_id = attachment.source_ingestion_id.strip()
        if source_id in seen:
            continue
        seen.add(source_id)
        source = source_evidence_store.get_source(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=source_id,
        )
        if source is None:
            raise CodexAppServerError("找不到已添加的附件，或该附件不属于当前课程。")
        path = source_download_path(source)
        if path is None:
            raise CodexAppServerError(f"附件“{source.file_name or source.title}”的原文件不可用，请重新添加。")
        verified.append(VerifiedChatAttachment(source=source, path=path))
    return verified


def prepare_chat_attachments(
    *,
    attachments: list[VerifiedChatAttachment],
) -> PreparedChatAttachments:
    if not attachments:
        return PreparedChatAttachments(prompt_context="", image_inputs=[], metadata=[])
    rows = ["Verified user attachments selected for this turn:"]
    image_inputs: list[str] = []
    metadata: list[dict[str, Any]] = []
    for item in attachments:
        mime_type = (item.source.mime_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        rows.append(
            f"- {item.source.file_name or item.source.title} ("
            f"MIME: {mime_type}; bytes: {item.source.size_bytes}; source id: {item.source.id})"
        )
        metadata.append(
            {
                "source_ingestion_id": item.source.id,
                "name": item.source.file_name or item.source.title,
                "mime_type": mime_type,
                "size_bytes": item.source.size_bytes,
                "kind": "image" if mime_type.startswith("image/") else "file",
            }
        )
        if mime_type in SUPPORTED_CHAT_IMAGE_MIME_TYPES:
            encoded = base64.b64encode(item.path.read_bytes()).decode("ascii")
            image_data_url = f"data:{mime_type};base64,{encoded}"
            if len(image_data_url) > MAX_CHAT_ATTACHMENT_IMAGE_DATA_URL_CHARS:
                raise CodexAppServerError(
                    f"图片附件“{item.source.file_name or item.source.title}”过大，请压缩后重试。"
                )
            image_inputs.append(image_data_url)
            continue
        if item.source.status != "ready":
            raise CodexAppServerError(
                f"附件“{item.source.file_name or item.source.title}”仍在解析，请稍后再发送。"
            )
        rows.append(
            "  The attachment is a verified source scope. Its content must be obtained from "
            "the backend source retrieval context for this turn."
        )
    rows.append("Do not infer attachment content from its name or metadata.")
    return PreparedChatAttachments(
        prompt_context="\n".join(rows),
        image_inputs=image_inputs,
        metadata=metadata,
    )
