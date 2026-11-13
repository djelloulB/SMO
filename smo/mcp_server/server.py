"""MCP server (stdio) exposing SMO use-cases to external agents (FR-14).

Tools are registered according to the authenticated agent's scopes. Mutating
tools are audited (REC-008). Only ``read`` and ``content`` tools exist yet;
``control`` and ``admin`` tools are Phase 3.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..config.security import security_layer
from ..config.settings import settings
from ..connectors.linkedin import get_linkedin_connector
from ..content.generator import GenerationError
from ..content.images import ImageError, get_image_provider
from ..memory.sqlite_store import SQLiteStore
from ..security.audit import audit_logger
from ..services.budget_service import BudgetExceeded, BudgetService
from ..services.config_service import ConfigService
from ..services.content_service import ContentError, ContentService
from ..services.image_service import ImageService
from ..services.media_service import MediaError, MediaService
from ..services.generation_service import generate_drafts
from ..services.metrics_service import MetricsService
from ..services.platform_service import PlatformService
from ..services.publishing_service import PublishingService
from ..services.scheduling_service import SchedulingService
from .auth import Identity, authenticate_stdio

logger = logging.getLogger(__name__)


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]


def _serialize_record(record) -> dict:
    return {
        "id": record.id,
        "platform": record.platform,
        "status": record.status,
        "text": record.variant_text or record.text,
        "media_ref": record.media_ref,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def transport_security_settings() -> TransportSecuritySettings:
    """Allow loopback and the public host only (SDK DNS-rebinding protection)."""
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins: list[str] = []
    public = urlparse(settings.public_base_url)
    if public.netloc:
        hosts.append(public.netloc)
        origins.extend([f"{public.scheme}://{public.netloc}"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def build_server(identity: Identity, generator=None) -> FastMCP:
    mcp = FastMCP("smo", transport_security=transport_security_settings())
    store = SQLiteStore()
    content = ContentService(store)
    scheduling = SchedulingService(content_service=content, store=store)
    metrics = MetricsService(store=store, content=content)

    def audit(tool: str, payload: dict, result: str) -> None:
        audit_logger.log(
            actor_id=identity.actor_id,
            tool=tool,
            payload_hash=_payload_hash(payload),
            result=result,
        )

    if "read" in identity.scopes:

        @mcp.tool()
        def get_health() -> dict:
            """Return SMO status, the immutable safety flags and each platform's state."""
            return {
                "status": "ok",
                "dry_run": security_layer.dry_run,
                "human_gate": security_layer.human_gate,
                "platforms": PlatformService(store).statuses(),
                "budget": {
                    "limit_usd": BudgetService(store).limit_usd,
                    "remaining_today_usd": BudgetService(store).remaining_today(),
                },
            }

        @mcp.tool()
        def list_drafts(status: str = "draft", limit: int = 20) -> dict:
            """List content items, optionally filtered by status (draft, approved, scheduled, published, ...)."""
            records = content.list(status=status, limit=min(max(limit, 1), 100))
            return {"drafts": [_serialize_record(r) for r in records]}

        @mcp.tool()
        def get_draft(content_id: int) -> dict:
            """Return one content item with its platform variant."""
            record = content.get(content_id)
            if record is None:
                raise ValueError(f"content {content_id} not found")
            return _serialize_record(record)

        @mcp.tool()
        def get_metrics(content_id: Optional[int] = None, limit: int = 20) -> dict:
            """Latest engagement snapshots (read-only), optionally for one content item."""
            return {"metrics": metrics.list_snapshots(content_id, min(max(limit, 1), 100))}

        @mcp.tool()
        def get_queue(status: Optional[str] = None) -> dict:
            """List scheduled publication jobs, optionally filtered by status."""
            jobs = scheduling.list_jobs(status=status)
            return {
                "jobs": [
                    {
                        "job_id": j.job_id,
                        "content_id": j.content_id,
                        "platform": j.platform,
                        "status": j.status,
                        "run_at": j.run_at.isoformat(),
                    }
                    for j in jobs
                ]
            }

    if "content" in identity.scopes:

        @mcp.tool()
        def generate_content(topic: str, platforms: Optional[list[str]] = None) -> dict:
            """Generate one draft per platform from a topic. Drafts stay unapproved until approve_draft."""
            targets = platforms or ["linkedin"]
            payload = {"topic": topic, "platforms": targets}
            try:
                results = generate_drafts(topic, targets, content, generator)
            except (ContentError, GenerationError, ValueError) as exc:
                audit("generate_content", payload, "failure")
                raise ValueError(str(exc)) from exc
            audit("generate_content", payload, "success")
            return {
                "drafts": [
                    {
                        "id": r.record.id,
                        "platform": r.record.platform,
                        "status": r.record.status,
                        "text": r.record.variant_text,
                        "moderation_reasons": list(r.moderation.reasons),
                    }
                    for r in results
                ]
            }

        @mcp.tool()
        async def generate_image(content_id: int, quality: str = "low", size: str = "1024x1024") -> dict:
            """Generate an image for a draft with the configured provider. This is a paid call:
            it is refused when the daily budget (budget_per_day_usd) would be exceeded."""
            payload = {"content_id": content_id, "size": size, "quality": quality}
            try:
                service = ImageService(get_image_provider(), BudgetService(store), content)
                receipt = await service.generate_for_content(content_id, size=size, quality=quality)
            except BudgetExceeded as exc:
                audit("generate_image", payload, "blocked")
                raise ValueError(str(exc)) from exc
            except (ContentError, ImageError) as exc:
                audit("generate_image", payload, "failure")
                raise ValueError(str(exc)) from exc
            audit("generate_image", payload, "success")
            return {
                "content_id": receipt.content_id,
                "path": receipt.path,
                "cost_usd": receipt.cost_usd,
                "remaining_today_usd": receipt.remaining_today_usd,
            }

        @mcp.tool()
        def attach_image(
            content_id: int,
            path: Optional[str] = None,
            data_base64: Optional[str] = None,
        ) -> dict:
            """Attach an image you made yourself to a draft. Give either ``path`` (a file
            under data/inputs) or ``data_base64`` (PNG, JPEG or WebP, max 8 MiB).
            The image is published with the post on platforms that support it."""
            payload = {
                "content_id": content_id,
                "source": "path" if path else "base64",
                "encoded_size": len(data_base64 or ""),
            }
            try:
                receipt = MediaService(content).attach(
                    content_id, path=path, data_base64=data_base64
                )
            except (ContentError, MediaError) as exc:
                audit("attach_image", payload, "failure")
                raise ValueError(str(exc)) from exc
            audit("attach_image", payload, "success")
            return {
                "content_id": receipt.content_id,
                "path": receipt.path,
                "bytes": receipt.bytes_written,
            }

        @mcp.tool()
        def approve_draft(content_id: int) -> dict:
            """Approve a draft (the human gate). Required before scheduling."""
            payload = {"content_id": content_id}
            try:
                record = content.approve(content_id)
            except ContentError as exc:
                audit("approve_draft", payload, "failure")
                raise ValueError(str(exc)) from exc
            audit("approve_draft", payload, "success")
            return _serialize_record(record)

        @mcp.tool()
        def reject_draft(content_id: int, reason: str) -> dict:
            """Reject a draft. The reason is recorded in the audit log."""
            if not reason.strip():
                raise ValueError("a reason is required to reject a draft")
            payload = {"content_id": content_id, "reason": reason}
            try:
                record = content.reject(content_id)
            except ContentError as exc:
                audit("reject_draft", payload, "failure")
                raise ValueError(str(exc)) from exc
            audit("reject_draft", payload, "success")
            return _serialize_record(record)

        @mcp.tool()
        def schedule_post(
            content_id: int,
            account_id: str,
            strategy: str = "fixed_slots",
            at: Optional[str] = None,
            timezone: str = "UTC",
            slots: Optional[list[str]] = None,
            window: Optional[str] = None,
        ) -> dict:
            """Schedule an approved draft. strategy: fixed_slots, randomized_window or weighted_by_engagement.
            ``at`` is an ISO 8601 datetime (overrides the strategy). ``slots`` are HH:MM, ``window`` is HH:MM-HH:MM."""
            try:
                run_at = datetime.fromisoformat(at) if at else None
                job = scheduling.schedule(
                    content_id,
                    account_id,
                    strategy=strategy,
                    at=run_at,
                    timezone_name=timezone,
                    slots=slots,
                    window=window,
                    actor_id=identity.actor_id,
                )
            except (ContentError, ValueError) as exc:
                audit("schedule_post", {"content_id": content_id, "strategy": strategy}, "failure")
                raise ValueError(str(exc)) from exc
            return {"job_id": job.job_id, "status": job.status, "run_at": job.run_at.isoformat()}

    if "control" in identity.scopes:

        @mcp.tool()
        async def publish_now(
            content_id: int,
            account_id: str,
            organization_id: Optional[str] = None,
            visibility: str = "PUBLIC",
        ) -> dict:
            """Publish an approved draft immediately (respects DRY_RUN and platform pauses).
            Any queued job for this item is cancelled first."""
            payload = {"content_id": content_id, "account_id": account_id}
            record = content.get(content_id)
            if record is None:
                audit("publish_now", payload, "failure")
                raise ValueError(f"content {content_id} not found")
            if PlatformService(store).is_open(record.platform or "linkedin"):
                audit("publish_now", payload, "blocked")
                raise ValueError(f"platform {record.platform or 'linkedin'} is paused")
            scheduling.cancel_pending(content_id)
            try:
                result = await PublishingService(content_service=content).publish(
                    content_id,
                    account_id,
                    organization_id=organization_id,
                    visibility=visibility,
                    actor_id=identity.actor_id,
                )
            except ContentError as exc:
                audit("publish_now", payload, "failure")
                raise ValueError(str(exc)) from exc
            audit("publish_now", payload, "success" if result.status == "published" else "blocked")
            return {
                "content_id": content_id,
                "status": result.status,
                "dry_run_path": result.dry_run_path,
            }

        @mcp.tool()
        def pause_platform(platform: str, reason: str = "paused via MCP") -> dict:
            """Stop publishing to a platform until resume_platform is called."""
            PlatformService(store).pause(platform.lower(), reason)
            audit("pause_platform", {"platform": platform}, "success")
            return {"platforms": PlatformService(store).statuses()}

        @mcp.tool()
        def resume_platform(platform: str) -> dict:
            """Resume publishing to a platform and clear its circuit breaker."""
            PlatformService(store).resume(platform.lower())
            audit("resume_platform", {"platform": platform}, "success")
            return {"platforms": PlatformService(store).statuses()}

        @mcp.tool()
        async def trigger_metrics_poll() -> dict:
            """Collect engagement snapshots now (read-only on the platform side)."""
            result = await metrics.poll()
            audit("trigger_metrics_poll", {}, "success")
            return {"polled": result.polled, "stored": result.stored, "errors": result.errors}

    if "admin" in identity.scopes:
        config = ConfigService(store)

        @mcp.tool()
        def set_config(key: str, value: Any) -> dict:
            """Change a mutable setting (brand_voice, accounts, sources, mcp_*). Immutable controls
            (dry_run, human_gate, budget_per_day_usd, scopes) are refused (REC-002)."""
            payload = {"key": key}
            try:
                config.set(key, value)
            except ValueError as exc:
                audit("set_config", payload, "blocked" if "immutable" in str(exc) else "failure")
                raise ValueError(str(exc)) from exc
            audit("set_config", payload, "success")
            return {"applied": True, "key": key, "value": config.get(key)}

        @mcp.tool()
        async def rotate_tokens(platform: str, account_id: str) -> dict:
            """Refresh the OAuth access token of one account. Only LinkedIn is implemented."""
            payload = {"platform": platform, "account_id": account_id}
            if platform.lower() == "x":
                from ..connectors.x import XAuthError, XConnector, XTokenService

                connector = XConnector.from_environment()
                if connector is None:
                    audit("rotate_tokens", payload, "failure")
                    raise ValueError("X is not configured")
                try:
                    refreshed = await XTokenService(store).ensure_fresh(connector, account_id, force=True)
                except XAuthError as exc:
                    audit("rotate_tokens", payload, "failure")
                    raise ValueError(str(exc)) from exc
                audit("rotate_tokens", payload, "success")
                return {
                    "refreshed": True,
                    "expires_at": datetime.fromtimestamp(refreshed.expires_at, timezone.utc).isoformat(),
                }
            if platform.lower() != "linkedin":
                audit("rotate_tokens", payload, "failure")
                raise ValueError(f"token rotation is not implemented for {platform}")
            connector = get_linkedin_connector()
            if connector is None:
                audit("rotate_tokens", payload, "failure")
                raise ValueError("LinkedIn is not configured")
            token = connector.load_token(account_id)
            if token is None or not token.refresh_token:
                audit("rotate_tokens", payload, "failure")
                raise ValueError("no refresh token for this account; reconnect it")
            try:
                refreshed = await connector.refresh_access_token(token.refresh_token)
            except Exception as exc:  # noqa: BLE001 - report the type only, never the token
                audit("rotate_tokens", payload, "failure")
                raise ValueError(f"token refresh failed: {type(exc).__name__}") from exc
            connector.save_token(account_id, refreshed)
            audit("rotate_tokens", payload, "success")
            return {
                "refreshed": True,
                "expires_at": datetime.fromtimestamp(refreshed.expires_at, timezone.utc).isoformat(),
            }

    return mcp


def run_stdio() -> None:
    identity = authenticate_stdio()
    if identity is None:
        raise SystemExit("SMO_MCP_API_KEY is missing or does not match a configured MCP_API_KEY_* key")
    logger.info("MCP stdio server starting for %s (scopes: %s)", identity.actor_id, sorted(identity.scopes))
    build_server(identity).run(transport="stdio")
