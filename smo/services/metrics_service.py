"""Read-only engagement collection into ``metric_snapshots`` (FR-13).

SMO never writes to or answers an audience here: it only reads counts so the
scheduler can rank posting hours (``weighted_by_engagement``).
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..memory.sqlite_store import Job, MetricSnapshot, SQLiteStore
from .content_service import ContentService

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    polled: int = 0
    stored: int = 0
    errors: int = 0


class MetricsService:
    def __init__(self, store: Optional[SQLiteStore] = None, content: Optional[ContentService] = None, connector=None):
        self.store = store or SQLiteStore()
        self.content = content or ContentService(self.store)
        self._connector = connector

    @property
    def connector(self):
        if self._connector is None:
            from ..connectors.linkedin import get_linkedin_connector

            self._connector = get_linkedin_connector()
        return self._connector

    def list_snapshots(self, content_id: Optional[int] = None, limit: int = 20) -> list[dict]:
        with self.store.get_session() as session:
            query = session.query(MetricSnapshot)
            if content_id is not None:
                query = query.filter(MetricSnapshot.content_id == content_id)
            rows = query.order_by(MetricSnapshot.id.desc()).limit(limit).all()
            return [
                {
                    "content_id": row.content_id,
                    "platform": row.platform,
                    "likes": row.likes,
                    "views": row.views,
                    "reach": row.reach,
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                }
                for row in rows
            ]

    def _account_for(self, content_id: int) -> Optional[str]:
        with self.store.get_session() as session:
            row = (
                session.query(Job.account_id)
                .filter(Job.content_id == content_id, Job.status == "completed")
                .order_by(Job.id.desc())
                .first()
            )
            return row[0] if row and row[0] else None

    async def poll(self, now: Optional[datetime] = None) -> PollResult:
        result = PollResult()
        connector = self.connector
        if connector is None:
            logger.warning("Metrics poll skipped: LinkedIn is not configured")
            return result

        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
        for content_id, platform, external_id in self.content.list_published():
            if platform != "linkedin":
                continue
            result.polled += 1
            account_id = self._account_for(content_id)
            token = connector.load_token(account_id) if account_id else None
            if token is None:
                result.errors += 1
                logger.warning("Metrics skipped for content %s: no account token", content_id)
                continue
            try:
                if token.is_expired() and token.refresh_token:
                    token = await connector.refresh_access_token(token.refresh_token)
                    connector.save_token(account_id, token)
                values = await connector.fetch_metrics(token, external_id)
            except Exception:  # noqa: BLE001 - one failing post must not stop the poll
                logger.exception("Metrics fetch failed for content %s", content_id)
                result.errors += 1
                continue

            with self.store.get_session() as session:
                session.add(
                    MetricSnapshot(
                        content_id=content_id,
                        platform=platform,
                        likes=values.get("likes", 0),
                        views=values.get("views", 0),
                        reach=values.get("reach", 0),
                        timestamp=moment,
                    )
                )
                session.commit()
            result.stored += 1

        logger.info("Metrics poll: %s polled, %s stored, %s errors", result.polled, result.stored, result.errors)
        return result


def run_metrics_poll() -> PollResult:
    """Entry point for the scheduler (synchronous wrapper)."""
    return asyncio.run(MetricsService().poll())
