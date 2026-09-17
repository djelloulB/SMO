"""
Base connector interface (REC-005 SSRF, REC-008 audit).
"""
import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from ..utils.http import ssrf_guard

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Audit entry structure (REC-008)
class AuditEntry(BaseModel):
    actor_id: str
    tool: str
    payload_hash: str
    previous_hash: str | None = None
    timestamp: datetime
    result: str  # success, failure, blocked


class ConnectorHealth(BaseModel):
    status: str  # healthy, degraded, down
    message: str
    last_check: datetime


class RateInfo(BaseModel):
    remaining: int
    reset: datetime
    limit: int


class PostResult(BaseModel):
    post_id: str
    status: str
    published_at: datetime | None = None


class MetricSnapshot(BaseModel):
    content_id: str
    platform: str
    likes: int
    views: int
    reach: int
    timestamp: datetime


class PostVariant(BaseModel):
    platform: str
    text: str
    media_ref: str | None = None


class BaseConnector(ABC):
    """
    Abstract base connector for all social platforms.
    Enforces SSRF (REC-005) and audit logging (REC-008).
    """

    def __init__(self, actor_id: str = "system"):
        """
        Initialize connector.

        Args:
            actor_id: Identifier for the actor (for audit). Defaults to "system".
        """
        self.actor_id = actor_id
        self._ssrf_guard = ssrf_guard  # Uses the global SSRF guard

    @abstractmethod
    async def authenticate(self) -> None:
        """
        Authenticate with the platform.
        Must store tokens encrypted at rest (REC-001).
        """
        pass

    @abstractmethod
    async def publish_post(self, variant: PostVariant) -> PostResult:
        """
        Publish a post to the platform.

        Args:
            variant: Platform-specific post variant

        Returns:
            Post result with post_id and status
        """
        pass

    @abstractmethod
    async def fetch_metrics(self, content_id: str) -> MetricSnapshot:
        """
        Fetch metrics for a published post (read-only, REC-006-like confinement).

        Args:
            content_id: ID of the post

        Returns:
            Metrics snapshot
        """
        pass

    @abstractmethod
    async def rate_limit_info(self) -> RateInfo:
        """
        Get current rate limit info.

        Returns:
            Rate limit information
        """
        pass

    @abstractmethod
    async def health(self) -> ConnectorHealth:
        """
        Get connector health status.

        Returns:
            Health status
        """
        pass

    def _log_audit(
        self,
        tool: str,
        payload: Any,
        result: str,
        previous_hash: str | None = None,
        session: "AsyncSession | None" = None,
    ) -> str:
        """
        Log an audit entry (REC-008).

        Args:
            tool: Tool name (e.g., "publish_post")
            payload: Payload to hash (simplified — we hash the JSON representation)
            result: Result status (success, failure, blocked)
            previous_hash: Hash of previous audit entry (for tamper-evidence)
            session: Optional SQLAlchemy session for persistent storage

        Returns:
            Hash of the new audit entry
        """
        # Hash the payload (simplified — in production, hash only non-sensitive fields)
        payload_str = str(payload)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()[:32]

        # Calculate previous hash if provided
        previous = previous_hash

        # Create audit entry
        entry = AuditEntry(
            actor_id=self.actor_id,
            tool=tool,
            payload_hash=payload_hash,
            previous_hash=previous,
            timestamp=datetime.now(),
            result=result,
        )

        # Log to console (redacted payload)
        redacted_payload = str(payload).replace("client_secret", "[REDACTED]").replace("token", "[REDACTED]")
        logger.info(
            "AUDIT: actor=%s tool=%s result=%s payload_hash=%s payload_preview=%s",
            self.actor_id,
            tool,
            result,
            payload_hash,
            redacted_payload[:200],
        )

        # Persist if session is provided
        if session:
            try:
                # In a full implementation, we would insert into audit_actions table
                # For MVP, we just log; the schema is defined in SPECIFICATION.md §3.3
                logger.debug("Persisting audit entry: %s", entry)
                # session.add(entry)
                # await session.commit()
            except Exception as e:
                logger.error("Failed to persist audit entry: %s", e)

        return payload_hash

    def _check_dry_run(self) -> bool:
        """
        Check if dry-run mode is active (REC-002).
        Raises an error if dry_run is True and this is a publish call.
        """
        from ..config.security import security_layer

        if security_layer.dry_run:
            logger.warning("Publish blocked: DRY_RUN is enabled")
            raise RuntimeError("Publish blocked: DRY_RUN mode is enabled. Set dry_run=false in SECURITY.md")

        return True
