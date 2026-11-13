"""Publishing use-case: human gate, dry-run and LinkedIn publication."""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config.security import security_layer
from ..connectors.linkedin import LinkedInConnector, get_linkedin_connector
from ..connectors.wordpress import WordPressConnector
from ..content.moderation import moderate
from ..security.audit import audit_logger
from ..utils.safeio import (
    BASE_DRYRUN_DIR,
    BASE_OUTPUT_DIR,
    ensure_safe_write,
    resolve_media_ref,
    sanitize_filename,
)
from .content_service import ContentError, ContentService, InvalidTransition
from .account_service import AccountService
from .media_service import MAX_MEDIA_BYTES, mime_type_for, sniff_extension

logger = logging.getLogger(__name__)

PUBLISHABLE_STATUSES = frozenset({"approved", "scheduled"})


@dataclass
class PublishResult:
    content_id: int
    status: str  # "published" or "dry_run"
    dry_run_path: Optional[str] = None
    payload: Optional[dict] = None
    api_result: Optional[dict] = None


@dataclass
class MediaAttachment:
    path: Path
    data: bytes
    filename: str
    mime_type: str


class PublishingService:
    """Publish approved content, honoring the immutable security layer."""

    def __init__(
        self,
        connector: Optional[LinkedInConnector] = None,
        content_service: Optional[ContentService] = None,
        dryrun_dir: Optional[Path] = None,
        media_dir: Optional[Path] = None,
    ):
        self._connector = connector
        self.content = content_service or ContentService()
        self.dryrun_dir = Path(dryrun_dir) if dryrun_dir else BASE_DRYRUN_DIR
        self.media_dir = Path(media_dir) if media_dir else BASE_OUTPUT_DIR

    @property
    def connector(self) -> Optional[LinkedInConnector]:
        if self._connector is None:
            self._connector = get_linkedin_connector()
        return self._connector

    async def publish(
        self,
        content_id: int,
        account_id: str,
        organization_id: Optional[str] = None,
        author_urn: Optional[str] = None,
        visibility: str = "PUBLIC",
        actor_id: str = "cli",
        mark_failed_on_error: bool = True,
    ) -> PublishResult:
        record = self.content.get(content_id)
        if record is None:
            raise ContentError(f"content {content_id} not found")

        allowed = set(PUBLISHABLE_STATUSES)
        if not security_layer.human_gate:
            allowed.add("draft")
        if record.status not in allowed:
            raise InvalidTransition(
                f"content {content_id} is '{record.status}'; approve it before publishing "
                "(human gate)"
            )

        platform = record.platform or "linkedin"
        AccountService(self.content.store).require_active(platform, account_id)

        text = record.variant_text or record.text
        moderation = moderate(text, record.platform or "linkedin")
        if not moderation.allowed:
            raise ContentError(f"moderation failed: {'; '.join(moderation.reasons)}")

        media = self._load_media(record)

        if (record.platform or "linkedin") == "wordpress":
            return await self._publish_wordpress(
                content_id, text, media, mark_failed_on_error, actor_id
            )
        if (record.platform or "linkedin") == "x":
            return await self._publish_x(
                content_id, account_id, text, media, mark_failed_on_error, actor_id
            )

        connector = self.connector
        token = connector.load_token(account_id) if connector else None

        if security_layer.dry_run:
            return await self._dry_run(
                content_id,
                text,
                visibility,
                organization_id,
                author_urn,
                token,
                connector,
                actor_id,
                media,
            )

        if connector is None:
            raise ContentError("LinkedIn is not configured")
        if token is None:
            raise ContentError(f"no LinkedIn token for account {account_id}")

        if token.is_expired():
            if not token.refresh_token:
                raise ContentError("token expired and no refresh token available")
            token = await connector.refresh_access_token(token.refresh_token)
            connector.save_token(account_id, token)

        try:
            result = await connector.publish_post(
                token,
                text,
                visibility,
                organization_id=organization_id,
                author_urn=author_urn,
                image_data=media.data if media else None,
            )
        except Exception:
            if mark_failed_on_error:
                self.content.mark_failed(content_id)
            audit_logger.log(
                actor_id=actor_id,
                tool="publish_post",
                payload_hash=self._payload_hash({"content_id": content_id}),
                result="failure",
            )
            raise

        self.content.mark_published(content_id)
        self.content.set_external_id(content_id, (result or {}).get("id"))
        audit_logger.log(
            actor_id=actor_id,
            tool="publish_post",
            payload_hash=self._payload_hash(
                {
                    "content_id": content_id,
                    "author": LinkedInConnector.resolve_author_urn(author_urn, organization_id),
                    "media": media.filename if media else None,
                }
            ),
            result="success",
        )
        logger.info("Published content %s to LinkedIn", content_id)
        return PublishResult(content_id=content_id, status="published", api_result=result)

    async def _publish_wordpress(
        self,
        content_id: int,
        text: str,
        media: Optional[MediaAttachment],
        mark_failed_on_error: bool,
        actor_id: str,
    ) -> PublishResult:
        connector = WordPressConnector.from_environment()
        if connector is None:
            raise ContentError("WordPress is not configured (ACCOUNTS_WORDPRESS_SITE, WORDPRESS_APP_*)")

        payload = WordPressConnector.build_post(text)
        if media is not None:
            payload["media_filename"] = media.filename
        if security_layer.dry_run:
            if media is not None:
                payload["featured_media"] = "DRY_RUN"
            return self._write_dry_run(content_id, "wordpress", payload, actor_id)

        try:
            featured_media = None
            if media is not None:
                uploaded = await connector.upload_media(media.filename, media.data, media.mime_type)
                try:
                    featured_media = int(uploaded["id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ContentError("WordPress did not return a media id") from exc
            result = await connector.publish_post(text, featured_media=featured_media)
        except Exception:
            self._record_failure(content_id, "wordpress", mark_failed_on_error, actor_id)
            raise

        return self._record_success(content_id, "wordpress", result, actor_id)

    async def _publish_x(
        self,
        content_id: int,
        account_id: str,
        text: str,
        media: Optional[MediaAttachment],
        mark_failed_on_error: bool,
        actor_id: str,
    ) -> PublishResult:
        from ..connectors.x import (
            X_MAX_IMAGE_BYTES,
            XAuthError,
            XConnector,
            XTokenService,
            x_enabled,
        )

        if not x_enabled():
            raise ContentError("X is disabled (set X_ENABLED=true; paid X API tiers apply)")
        connector = XConnector.from_environment()
        if connector is None:
            raise ContentError("X is not configured (X_CLIENT_ID, X_CLIENT_SECRET, X_REDIRECT_URI)")

        if media is not None and len(media.data) > X_MAX_IMAGE_BYTES:
            raise ContentError(f"X accepts images up to {X_MAX_IMAGE_BYTES} bytes")

        payload: dict = {"text": text}
        if media is not None:
            payload["media"] = {"media_ids": ["DRY_RUN"]}
            payload["media_filename"] = media.filename
        if security_layer.dry_run:
            return self._write_dry_run(content_id, "x", payload, actor_id)

        try:
            token = await XTokenService(self.content.store).ensure_fresh(connector, account_id)
            media_ids = None
            if media is not None:
                media_ids = [
                    await connector.upload_media(token.access_token, media.data, media.mime_type)
                ]
            result = await connector.publish_post(token.access_token, text, media_ids=media_ids)
        except XAuthError as exc:
            self._record_failure(content_id, "x", mark_failed_on_error, actor_id)
            raise ContentError(str(exc)) from exc
        except Exception:
            self._record_failure(content_id, "x", mark_failed_on_error, actor_id)
            raise

        return self._record_success(content_id, "x", result, actor_id)

    def _write_dry_run(self, content_id: int, platform: str, payload: dict, actor_id: str) -> PublishResult:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.dryrun_dir / sanitize_filename(f"content-{content_id}-{platform}-{timestamp}.json")
        ensure_safe_write(path, json.dumps(payload, indent=2).encode("utf-8"))
        audit_logger.log(
            actor_id=actor_id,
            tool="publish_post",
            payload_hash=self._payload_hash(payload),
            result="blocked",
        )
        logger.info("DRY_RUN: wrote %s payload to %s", platform, path)
        return PublishResult(content_id=content_id, status="dry_run", dry_run_path=str(path), payload=payload)

    def _record_failure(self, content_id: int, platform: str, mark_failed_on_error: bool, actor_id: str) -> None:
        if mark_failed_on_error:
            self.content.mark_failed(content_id)
        audit_logger.log(
            actor_id=actor_id,
            tool="publish_post",
            payload_hash=self._payload_hash({"content_id": content_id, "platform": platform}),
            result="failure",
        )

    def _record_success(self, content_id: int, platform: str, result: dict, actor_id: str) -> PublishResult:
        self.content.mark_published(content_id)
        self.content.set_external_id(content_id, (result or {}).get("id") or None)
        audit_logger.log(
            actor_id=actor_id,
            tool="publish_post",
            payload_hash=self._payload_hash({"content_id": content_id, "platform": platform}),
            result="success",
        )
        logger.info("Published content %s to %s", content_id, platform)
        return PublishResult(content_id=content_id, status="published", api_result=result)

    async def _dry_run(
        self,
        content_id: int,
        text: str,
        visibility: str,
        organization_id: Optional[str],
        author_urn: Optional[str],
        token,
        connector: Optional[LinkedInConnector],
        actor_id: str,
        media: Optional[MediaAttachment] = None,
    ) -> PublishResult:
        author = LinkedInConnector.resolve_author_urn(author_urn, organization_id)
        if author is None and token is not None and connector is not None:
            try:
                profile = await connector.get_profile(token)
                author = f"urn:li:person:{profile['id']}"
            except Exception:  # noqa: BLE001 - dry-run must not require a live call
                logger.warning("DRY_RUN: could not resolve profile, using placeholder")
        if author is None:
            author = "urn:li:person:DRY_RUN"

        payload = LinkedInConnector.build_post_payload(
            author,
            text,
            visibility,
            "urn:li:image:DRY_RUN" if media is not None else None,
        )
        if media is not None:
            payload["media_filename"] = media.filename
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = sanitize_filename(f"content-{content_id}-{timestamp}.json")
        path = self.dryrun_dir / filename
        ensure_safe_write(path, json.dumps(payload, indent=2).encode("utf-8"))

        audit_logger.log(
            actor_id=actor_id,
            tool="publish_post",
            payload_hash=self._payload_hash(payload),
            result="blocked",
        )
        logger.info("DRY_RUN: wrote outbound payload to %s", path)
        return PublishResult(
            content_id=content_id,
            status="dry_run",
            dry_run_path=str(path),
            payload=payload,
        )

    def _load_media(self, record) -> Optional[MediaAttachment]:
        """Read the attached image, confined to the media directory (REC-006)."""
        media_ref = getattr(record, "media_ref", None)
        if not media_ref:
            return None
        try:
            path = resolve_media_ref(media_ref, self.media_dir)
        except ValueError as exc:
            raise ContentError(str(exc)) from exc
        if path.stat().st_size > MAX_MEDIA_BYTES:
            raise ContentError(f"attached media is larger than {MAX_MEDIA_BYTES} bytes")
        data = path.read_bytes()
        extension = sniff_extension(data)
        if extension is None:
            raise ContentError("attached media is not a PNG, JPEG or WebP image")
        return MediaAttachment(
            path=path,
            data=data,
            filename=sanitize_filename(path.name),
            mime_type=mime_type_for(extension),
        )

    @staticmethod
    def _payload_hash(payload: dict) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:32]
