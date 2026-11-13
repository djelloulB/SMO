"""Attach an image to a draft (FR-07 media variants).

Two sources are accepted, because SMO runs both autonomously and under an
external agent:

* ``path``: a file the operator or agent dropped under ``data/inputs`` (mounted
  volume). The path is confined there (REC-006).
* ``data_base64``: bytes sent by an MCP client that already made its own image.

In both cases the content is validated by magic bytes, not by the filename or
the declared type, size-capped, copied under ``data/media`` and linked to the
draft through ``variants.media_ref``.
"""
import base64
import binascii
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..utils.safeio import (
    BASE_INPUT_DIR,
    BASE_OUTPUT_DIR,
    ensure_safe_write,
    get_safe_path,
    sanitize_filename,
)
from .content_service import ContentError, ContentService

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 8 * 1024 * 1024  # 8 MiB: enough for a 1024x1024 PNG, no zip bombs
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class MediaError(Exception):
    """The media source is missing, too large, or not a supported image."""


@dataclass
class MediaReceipt:
    content_id: int
    path: str
    bytes_written: int
    extension: str


def sniff_extension(data: bytes) -> Optional[str]:
    """Return the extension implied by the file signature, or None if unsupported."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def mime_type_for(extension: str) -> str:
    try:
        return MIME_TYPES[extension.lower()]
    except KeyError:
        raise MediaError(f"no MIME type for extension '{extension}'") from None


class MediaService:
    def __init__(
        self,
        content: Optional[ContentService] = None,
        output_dir: Optional[Path] = None,
        input_dir: Optional[Path] = None,
    ):
        self.content = content or ContentService()
        self.output_dir = Path(output_dir) if output_dir else BASE_OUTPUT_DIR
        self.input_dir = Path(input_dir) if input_dir else BASE_INPUT_DIR

    def attach(
        self,
        content_id: int,
        path: Optional[str] = None,
        data_base64: Optional[str] = None,
    ) -> MediaReceipt:
        if (path is None) == (data_base64 is None):
            raise MediaError("provide exactly one image source: path or data_base64")
        record = self.content.get(content_id)
        if record is None:
            raise ContentError(f"content {content_id} not found")
        if record.status == "published":
            raise MediaError(f"content {content_id} is already published; its media can no longer change")

        if path is not None:
            data = self._read_confined(path)
        else:
            data = self._decode_base64(data_base64 or "")

        return self._store(content_id, data)

    # --- Sources ---
    def _read_confined(self, path: str) -> bytes:
        try:
            source = get_safe_path(
                self.input_dir,
                path,
                allowed_extensions=ALLOWED_EXTENSIONS,
            )
        except ValueError as exc:
            raise MediaError(str(exc)) from exc
        if not source.is_file():
            raise MediaError(f"file '{path}' does not exist under {self.input_dir}")
        if source.stat().st_size > MAX_MEDIA_BYTES:
            raise MediaError(f"file '{path}' is larger than {MAX_MEDIA_BYTES} bytes")
        return source.read_bytes()

    @staticmethod
    def _decode_base64(data_base64: str) -> bytes:
        encoded = "".join(data_base64.split())
        # Refuse before decoding so a huge payload never allocates memory.
        if len(encoded) * 3 // 4 > MAX_MEDIA_BYTES:
            raise MediaError(f"image is larger than {MAX_MEDIA_BYTES} bytes")
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MediaError("image data is not valid base64") from exc

    # --- Storage ---
    def _store(self, content_id: int, data: bytes) -> MediaReceipt:
        if not data:
            raise MediaError("image is empty")
        if len(data) > MAX_MEDIA_BYTES:
            raise MediaError(f"image is larger than {MAX_MEDIA_BYTES} bytes")
        extension = sniff_extension(data)
        if extension is None:
            raise MediaError("unsupported image format (PNG, JPEG or WebP only)")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = secrets.token_hex(4)
        path = self.output_dir / sanitize_filename(
            f"content-{content_id}-{timestamp}-{suffix}{extension}"
        )
        ensure_safe_write(path, data)
        self.content.set_media_ref(content_id, str(path))
        logger.info("Attached %s image to content %s (%s bytes)", extension, content_id, len(data))
        return MediaReceipt(
            content_id=content_id,
            path=str(path),
            bytes_written=len(data),
            extension=extension,
        )