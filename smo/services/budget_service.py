"""Daily spend cap for paid operations (budget_per_day_usd, REC-002).

A paid call first records its estimated cost, then checks the day's total. If the
total is over the cap, the reservation is removed and the call is refused. Two
processes that both reserve near the cap cannot both pass: each sees the other's
row, and both release, which errs on the side of refusing.
"""
from datetime import datetime, timezone
from typing import Optional

from ..config.security import security_layer
from ..memory.sqlite_store import SpendEntry, SQLiteStore


class BudgetExceeded(Exception):
    """The paid call would take today's spend over the daily cap."""


class BudgetService:
    def __init__(self, store: Optional[SQLiteStore] = None, limit_usd: Optional[float] = None):
        self.store = store or SQLiteStore()
        self.limit_usd = security_layer.budget_per_day_usd if limit_usd is None else limit_usd

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def spent_today(self) -> float:
        with self.store.get_session() as session:
            total = (
                session.query(SpendEntry.amount_usd)
                .filter(SpendEntry.day == self._today())
                .all()
            )
            return round(sum(row[0] for row in total), 6)

    def remaining_today(self) -> float:
        return round(max(self.limit_usd - self.spent_today(), 0.0), 6)

    def reserve(self, kind: str, provider: str, amount_usd: float, content_id: Optional[int] = None) -> int:
        with self.store.get_session() as session:
            entry = SpendEntry(
                day=self._today(),
                kind=kind,
                provider=provider,
                amount_usd=round(amount_usd, 6),
                content_id=content_id,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(entry)
            session.commit()
            entry_id = entry.id

        total = self.spent_today()
        if total > self.limit_usd + 1e-9:
            self.release(entry_id)
            raise BudgetExceeded(
                f"daily budget of ${self.limit_usd:.2f} would be exceeded "
                f"(spent ${total - amount_usd:.4f}, this call ${amount_usd:.4f})"
            )
        return entry_id

    def release(self, entry_id: int) -> None:
        with self.store.get_session() as session:
            row = session.get(SpendEntry, entry_id)
            if row is not None:
                session.delete(row)
                session.commit()
