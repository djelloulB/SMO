"""Per-platform pause and circuit breaker (FR-11).

State lives in SQLite so every process (web, CLI, MCP) sees the same breaker.
After ``BREAKER_THRESHOLD`` consecutive retryable failures a platform is skipped
for ``BREAKER_COOLDOWN``. A manual pause stays until it is resumed.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..memory.sqlite_store import PlatformControl, SQLiteStore

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = timedelta(minutes=15)


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


class PlatformService:
    def __init__(self, store: Optional[SQLiteStore] = None):
        self.store = store or SQLiteStore()

    def _row(self, session, platform: str) -> PlatformControl:
        row = session.get(PlatformControl, platform)
        if row is None:
            row = PlatformControl(platform=platform, status="active", consecutive_failures=0)
            session.add(row)
        return row

    def is_open(self, platform: str, now: Optional[datetime] = None) -> bool:
        """True when publications for this platform must wait."""
        current = _naive_utc(now or datetime.now(timezone.utc))
        with self.store.get_session() as session:
            row = session.get(PlatformControl, platform)
            if row is None:
                return False
            if row.status == "paused":
                return True
            return row.paused_until is not None and current < row.paused_until

    def record_success(self, platform: str) -> None:
        with self.store.get_session() as session:
            row = self._row(session, platform)
            row.consecutive_failures = 0
            row.paused_until = None
            session.commit()

    def record_failure(self, platform: str, now: Optional[datetime] = None) -> bool:
        """Count a retryable failure. Returns True when the breaker opens."""
        current = _naive_utc(now or datetime.now(timezone.utc))
        with self.store.get_session() as session:
            row = self._row(session, platform)
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            opened = row.consecutive_failures >= BREAKER_THRESHOLD
            if opened:
                row.paused_until = current + BREAKER_COOLDOWN
                row.reason = f"circuit breaker: {row.consecutive_failures} consecutive failures"
            session.commit()
            return opened

    def pause(self, platform: str, reason: str = "paused by operator") -> None:
        with self.store.get_session() as session:
            row = self._row(session, platform)
            row.status = "paused"
            row.reason = reason[:256]
            session.commit()

    def resume(self, platform: str) -> None:
        with self.store.get_session() as session:
            row = self._row(session, platform)
            row.status = "active"
            row.paused_until = None
            row.consecutive_failures = 0
            row.reason = None
            session.commit()

    def statuses(self, now: Optional[datetime] = None) -> dict[str, str]:
        current = _naive_utc(now or datetime.now(timezone.utc))
        with self.store.get_session() as session:
            rows = session.query(PlatformControl).all()
            result = {}
            for row in rows:
                if row.status == "paused":
                    result[row.platform] = "paused"
                elif row.paused_until is not None and current < row.paused_until:
                    result[row.platform] = "circuit_open"
                else:
                    result[row.platform] = "active"
            return result
