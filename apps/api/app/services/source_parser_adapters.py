from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


SourceElementType = Literal[
    "heading",
    "paragraph",
    "list_item",
    "table",
    "formula",
    "caption",
    "image",
    "footnote",
    "code",
]


class ParsedSourceElement(BaseModel):
    element_id: str
    page_no: int = Field(ge=1)
    element_type: SourceElementType = "paragraph"
    reading_order: int = Field(ge=0)
    raw_text: str = ""
    normalized_text: str = ""
    bbox: list[float] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, object] = Field(default_factory=dict)


class ParsedDocumentV2(BaseModel):
    source_id: str
    source_content_hash: str
    parser: str
    parser_version: str
    page_count: int = Field(ge=0)
    elements: list[ParsedSourceElement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class SourceParserAdapter(Protocol):
    name: str
    version: str

    def parse(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
    ) -> ParsedDocumentV2: ...


class NativeFastParserAdapter:
    name = "openclass_native_fast"
    version = "1"

    def parse(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
    ) -> ParsedDocumentV2:
        if path.suffix.lower() == ".pdf" or mime_type == "application/pdf":
            return self._parse_pdf(
                source_id=source_id,
                source_content_hash=source_content_hash,
                path=path,
            )
        if not (
            mime_type.startswith("text/")
            or path.suffix.lower() in {".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".json", ".xml"}
        ):
            raise RuntimeError(f"The native Source QA parser does not support {mime_type or path.suffix}.")
        text = path.read_text(encoding="utf-8", errors="replace")
        return ParsedDocumentV2(
            source_id=source_id,
            source_content_hash=source_content_hash,
            parser=self.name,
            parser_version=self.version,
            page_count=1,
            elements=[
                ParsedSourceElement(
                    element_id=f"{source_id}:1:0",
                    page_no=1,
                    reading_order=0,
                    raw_text=text,
                    normalized_text=_normalize_text(text),
                )
            ],
        )

    def _parse_pdf(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
    ) -> ParsedDocumentV2:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        elements: list[ParsedSourceElement] = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            elements.append(
                ParsedSourceElement(
                    element_id=f"{source_id}:{page_index}:0",
                    page_no=page_index,
                    reading_order=0,
                    raw_text=text,
                    normalized_text=_normalize_text(text),
                    confidence=1.0 if text.strip() else 0.0,
                )
            )
        return ParsedDocumentV2(
            source_id=source_id,
            source_content_hash=source_content_hash,
            parser=self.name,
            parser_version=self.version,
            page_count=len(reader.pages),
            elements=elements,
        )


class JsonSidecarParserAdapter:
    """Process-isolated parser boundary using the ParsedDocumentV2 JSON contract."""

    def __init__(self, *, name: str, version: str, command_env: str) -> None:
        self.name = name
        self.version = version
        self.command_env = command_env

    @property
    def configured(self) -> bool:
        command = (os.getenv(self.command_env) or "").strip()
        return bool(command and Path(command).expanduser().is_file())

    def parse(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
    ) -> ParsedDocumentV2:
        command = Path((os.getenv(self.command_env) or "").strip()).expanduser()
        if not command.is_file():
            raise RuntimeError(f"{self.name} sidecar is not configured")
        with tempfile.TemporaryDirectory(prefix=f"{self.name}-") as temporary:
            output_path = Path(temporary) / "parsed-document-v2.json"
            completed = subprocess.run(
                [
                    str(command),
                    "--input",
                    str(path),
                    "--output",
                    str(output_path),
                    "--source-id",
                    source_id,
                    "--source-content-hash",
                    source_content_hash,
                    "--mime-type",
                    mime_type,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
                env={
                    "PATH": os.getenv("PATH", ""),
                    "LANG": "en_US.UTF-8",
                },
            )
            if completed.returncode != 0 or not output_path.is_file():
                detail = (completed.stderr or completed.stdout or "").strip()[:500]
                raise RuntimeError(f"{self.name} sidecar failed: {detail}")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        document = ParsedDocumentV2.model_validate(payload)
        if document.source_id != source_id or document.source_content_hash != source_content_hash:
            raise RuntimeError(f"{self.name} sidecar returned mismatched source identity")
        return document.model_copy(
            update={"parser": self.name, "parser_version": self.version}
        )


class SourceParserRouter:
    def __init__(
        self,
        *,
        native: SourceParserAdapter | None = None,
        opendataloader: JsonSidecarParserAdapter | None = None,
    ) -> None:
        self.native = native or NativeFastParserAdapter()
        self.opendataloader = opendataloader or JsonSidecarParserAdapter(
            name="opendataloader",
            version=os.getenv("OPENCLASS_OPENDATALOADER_VERSION", "2.5.0"),
            command_env="OPENCLASS_OPENDATALOADER_COMMAND",
        )

    def parse_fast(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
    ) -> ParsedDocumentV2:
        selected = (os.getenv("OPENCLASS_SOURCE_QA_FAST_PARSER") or "native").strip().lower()
        is_pdf = path.suffix.lower() == ".pdf" or mime_type == "application/pdf"
        if selected == "opendataloader" and is_pdf and self.opendataloader.configured:
            return self.opendataloader.parse(
                source_id=source_id,
                source_content_hash=source_content_hash,
                path=path,
                mime_type=mime_type,
            )
        return self.native.parse(
            source_id=source_id,
            source_content_hash=source_content_hash,
            path=path,
            mime_type=mime_type,
        )

    def parse_shadow_opendataloader(
        self,
        *,
        source_id: str,
        source_content_hash: str,
        path: Path,
        mime_type: str,
    ) -> ParsedDocumentV2 | None:
        if os.getenv("OPENCLASS_OPENDATALOADER_SHADOW", "0") != "1":
            return None
        if not self.opendataloader.configured:
            return None
        return self.opendataloader.parse(
            source_id=source_id,
            source_content_hash=source_content_hash,
            path=path,
            mime_type=mime_type,
        )


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()


source_parser_router = SourceParserRouter()
