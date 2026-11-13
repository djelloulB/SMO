"""Scheduling use-case: choose a run time, queue a Job, dispatch it when due (FR-10, FR-11).

The ``jobs`` table is the queue (shared by CLI, MCP and the web process). Times
are stored as naive UTC and returned as aware UTC.

``weighted_by_engagement`` is the self-optimizing strategy: it ranks hours from
``metric_snapshots`` (newest snapshot per post, exponential time decay, per
platform), then exploits the best hour most of the time and occasionally
explores another daytime hour so the ranking keeps learning.
"""
import asyncio
import hashlib
import json
import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..memory.sqlite_store import ContentItem, Job, MetricSnapshot, SQLiteStore
from ..orchestrator.retry import MAX_ATTEMPTS, backoff_seconds, is_retryable
from ..security.audit import audit_logger
from .content_service import ContentError, ContentService, InvalidTransition
from .platform_service import PlatformService
from .publishing_service import PublishingService

logger = logging.getLogger(__name__)

STRATEGIES = frozenset({"fixed_slots", "randomized_window", "weighted_by_engagement"})
DEFAULT_SLOTS = ("09:00", "17:00")
DEFAULT_WINDOW = "09:00-12:00"

# Self-optimizing schedule knobs.
ENGAGEMENT_HALF_LIFE_DAYS = 30.0
MIN_ENGAGEMENT_SAMPLES = 3
EXPLORATION_RATE = 0.15
EXPLORATION_HOURS = range(7, 22)  # never explore at night


@dataclass
class ScheduledJob:
    job_id: int
    content_id: int
    platform: str
    status: str
    run_at: datetime


@dataclass(frozen=True)
class HourPerformance:
    """Decayed engagement estimate for one hour of the day."""

    hour: int
    score: float
    samples: int


def parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"invalid time '{value}', expected HH:MM") from exc


def parse_window(window: str) -> tuple[time, time]:
    start, sep, end = window.partition("-")
    if not sep:
        raise ValueError("window must be 'HH:MM-HH:MM'")
    start_t, end_t = parse_time(start), parse_time(end)
    if end_t <= start_t:
        raise ValueError("window end must be after its start")
    return start_t, end_t


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone '{timezone_name}'") from exc


def _next_occurrence(local_now: datetime, at: time) -> datetime:
    candidate = local_now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def compute_run_at(
    strategy: str,
    now: datetime,
    timezone_name: str = "UTC",
    slots: Optional[list[str]] = None,
    window: Optional[str] = None,
    engagement_scores: Optional[dict[int, float]] = None,
    rng: Optional[random.Random] = None,
    exploration_rate: float = EXPLORATION_RATE,
) -> datetime:
    """Return the next run time for ``strategy`` as an aware UTC datetime."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy '{strategy}'")
    tz = _zone(timezone_name)
    local_now = now.astimezone(tz)

    if strategy == "fixed_slots":
        times = [parse_time(s) for s in (slots or DEFAULT_SLOTS)]
        local = min(_next_occurrence(local_now, t) for t in times)

    elif strategy == "randomized_window":
        start, end = parse_window(window or DEFAULT_WINDOW)
        end_today = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if local_now < end_today:
            start_today = local_now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            lower, upper = max(local_now, start_today), end_today
        else:
            day = local_now.date() + timedelta(days=1)
            lower = datetime.combine(day, start, tzinfo=tz)
            upper = datetime.combine(day, end, tzinfo=tz)
        span = int((upper - lower).total_seconds())
        generator = rng or random.Random()
        local = lower + timedelta(seconds=generator.randint(0, span))

    else:  # weighted_by_engagement: exploit the best hour, sometimes explore another
        generator = rng or random.Random()
        if engagement_scores:
            best_hour = max(engagement_scores, key=engagement_scores.get)
            if exploration_rate > 0 and generator.random() < exploration_rate:
                candidates = [hour for hour in EXPLORATION_HOURS if hour != best_hour]
                if candidates:
                    best_hour = generator.choice(candidates)
                    logger.info("Engagement schedule exploration: trying hour %s", best_hour)
            local = _next_occurrence(local_now, time(best_hour, 0))
        else:
            local = _next_occurrence(local_now, parse_time(DEFAULT_SLOTS[0]))

    return local.astimezone(timezone.utc)


def engagement_analysis(
    store: SQLiteStore,
    platform: Optional[str] = None,
    now: Optional[datetime] = None,
    half_life_days: float = ENGAGEMENT_HALF_LIFE_DAYS,
) -> list[HourPerformance]:
    """Rank posting hours from the **newest** snapshot of each post.

    Only one snapshot per post is used: polls every 6 hours must not count the
    same post several times. Older posts are discounted exponentially, so a
    recent reading at an hour quickly replaces stale numbers.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)

    with store.get_session() as session:
        query = (
            session.query(
                ContentItem.published_at,
                MetricSnapshot.content_id,
                MetricSnapshot.platform,
                MetricSnapshot.likes,
                MetricSnapshot.reach,
            )
            .join(MetricSnapshot, MetricSnapshot.content_id == ContentItem.id)
            .filter(ContentItem.published_at.isnot(None))
        )
        if platform:
            query = query.filter(MetricSnapshot.platform == platform)
        rows = query.order_by(MetricSnapshot.id).all()

    latest: dict[tuple[int, str], tuple[datetime, int, int]] = {}
    for published_at, content_id, snapshot_platform, likes, reach in rows:
        # Rows are ordered by snapshot id, so the last write is the newest poll.
        latest[(content_id, snapshot_platform)] = (published_at, likes or 0, reach or 0)

    weighted: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for published_at, likes, reach in latest.values():
        age_days = max(0.0, (moment - published_at).total_seconds() / 86400)
        weight = math.pow(0.5, age_days / half_life_days) if half_life_days > 0 else 1.0
        bucket = weighted[published_at.hour]
        bucket[0] += weight * (likes + reach)
        bucket[1] += weight
        bucket[2] += 1

    return [
        HourPerformance(hour=hour, score=values[0] / values[1], samples=int(values[2]))
        for hour, values in sorted(weighted.items())
    ]


def engagement_by_hour(
    store: SQLiteStore,
    platform: Optional[str] = None,
    now: Optional[datetime] = None,
    half_life_days: float = ENGAGEMENT_HALF_LIFE_DAYS,
    min_samples: int = MIN_ENGAGEMENT_SAMPLES,
) -> dict[int, float]:
    """Average engagement per hour, keeping only hours with enough observations."""
    return {
        item.hour: item.score
        for item in engagement_analysis(store, platform, now, half_life_days)
        if item.samples >= min_samples
    }


def _update_job(store: SQLiteStore, job_id: int, **fields) -> None:
    with store.get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        for name, value in fields.items():
            setattr(job, name, value)
        session.commit()


def _claim_job(store: SQLiteStore, job_id: int) -> bool:
    """Atomically move a pending job to running. Returns False if another worker won."""
    with store.get_session() as session:
        claimed = (
            session.query(Job)
            .filter(Job.id == job_id, Job.status == "pending")
            .update({"status": "running", "run_at": datetime.now(timezone.utc).replace(tzinfo=None)})
        )
        session.commit()
        return claimed == 1


def _execute_job(store: SQLiteStore, job_id: int, now: datetime, rng: Optional[random.Random]) -> None:
    with store.get_session() as session:
        job = session.get(Job, job_id)
        content_id = job.content_id
        account_id = job.account_id
        platform = job.platform
        attempts = (job.retry_count or 0) + 1
        options = json.loads(job.options or "{}")

    content = ContentService(store)
    service = PublishingService(content_service=content)
    breaker = PlatformService(store)
    try:
        asyncio.run(
            service.publish(
                content_id,
                account_id,
                organization_id=options.get("organization_id"),
                author_urn=options.get("author_urn"),
                visibility=options.get("visibility", "PUBLIC"),
                actor_id="scheduler",
                mark_failed_on_error=False,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the job row records the outcome
        error_type = type(exc).__name__
        if is_retryable(exc):
            breaker.record_failure(platform, now)
            if attempts < MAX_ATTEMPTS:
                retry_at = (now + timedelta(seconds=backoff_seconds(attempts, rng))).replace(tzinfo=None)
                _update_job(
                    store,
                    job_id,
                    status="pending",
                    retry_count=attempts,
                    scheduled_at=retry_at,
                    next_retry_at=retry_at,
                    last_error=error_type,
                )
                logger.warning("Job %s attempt %s failed (%s), retry at %s", job_id, attempts, error_type, retry_at)
                return
            _update_job(store, job_id, status="dead_letter", dead_letter=1, retry_count=attempts, last_error=error_type)
        else:
            _update_job(store, job_id, status="failed", retry_count=attempts, last_error=error_type)
        if not isinstance(exc, InvalidTransition):
            content.mark_failed(content_id)
        logger.error("Job %s ended as %s (%s)", job_id, "dead_letter" if is_retryable(exc) else "failed", error_type)
        return

    breaker.record_success(platform)
    _update_job(store, job_id, status="completed", retry_count=attempts)


@lru_cache(maxsize=1)
def _default_store() -> SQLiteStore:
    return SQLiteStore()


def run_due_jobs(
    now: Optional[datetime] = None,
    store: Optional[SQLiteStore] = None,
    limit: int = 20,
    rng: Optional[random.Random] = None,
) -> int:
    """Run every pending job whose time has come and whose platform is not held open."""
    store = store or _default_store()
    current_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current = current_utc.replace(tzinfo=None)
    breaker = PlatformService(store)
    with store.get_session() as session:
        due = [
            (row.id, row.platform)
            for row in session.query(Job.id, Job.platform)
            .filter(Job.status == "pending", Job.scheduled_at <= current)
            .order_by(Job.scheduled_at)
            .limit(limit)
            .all()
        ]
    ran = 0
    for job_id, platform in due:
        if breaker.is_open(platform, current_utc):
            continue
        if _claim_job(store, job_id):
            _execute_job(store, job_id, current_utc, rng)
            ran += 1
    return ran


class SchedulingService:
    """Queues approved content and lists the job queue."""

    def __init__(self, content_service: Optional[ContentService] = None, store: Optional[SQLiteStore] = None):
        self.store = store or SQLiteStore()
        self.content = content_service or ContentService(self.store)

    def schedule(
        self,
        content_id: int,
        account_id: str,
        strategy: str = "fixed_slots",
        at: Optional[datetime] = None,
        timezone_name: str = "UTC",
        slots: Optional[list[str]] = None,
        window: Optional[str] = None,
        organization_id: Optional[str] = None,
        author_urn: Optional[str] = None,
        visibility: str = "PUBLIC",
        actor_id: str = "cli",
        now: Optional[datetime] = None,
        rng: Optional[random.Random] = None,
    ) -> ScheduledJob:
        record = self.content.get(content_id)
        if record is None:
            raise ContentError(f"content {content_id} not found")
        if record.status != "approved":
            raise InvalidTransition(
                f"content {content_id} is '{record.status}'; approve it before scheduling (human gate)"
            )

        platform = record.platform or "linkedin"
        now = now or datetime.now(timezone.utc)
        if at is not None:
            run_at = (at if at.tzinfo else at.replace(tzinfo=_zone(timezone_name))).astimezone(timezone.utc)
            if run_at <= now:
                raise ValueError("scheduled time must be in the future")
        else:
            scores = (
                engagement_by_hour(self.store, platform=platform, now=now)
                if strategy == "weighted_by_engagement"
                else None
            )
            run_at = compute_run_at(strategy, now, timezone_name, slots, window, scores, rng)

        options = {"organization_id": organization_id, "author_urn": author_urn, "visibility": visibility}
        with self.store.get_session() as session:
            job = Job(
                content_id=content_id,
                platform=platform,
                status="pending",
                scheduled_at=run_at.replace(tzinfo=None),
                account_id=account_id,
                options=json.dumps(options),
            )
            session.add(job)
            session.commit()
            job_row_id = job.id

        try:
            self.content.mark_scheduled(content_id)
        except Exception:
            with self.store.get_session() as session:
                row = session.get(Job, job_row_id)
                if row is not None:
                    session.delete(row)
                    session.commit()
            raise

        payload = {"content_id": content_id, "run_at": run_at.isoformat(), "strategy": strategy}
        audit_logger.log(
            actor_id=actor_id,
            tool="schedule_post",
            payload_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32],
            result="success",
        )
        logger.info("Queued content %s as job %s at %s", content_id, job_row_id, run_at.isoformat())
        return ScheduledJob(job_id=job_row_id, content_id=content_id, platform=platform, status="pending", run_at=run_at)

    def cancel_pending(self, content_id: int) -> int:
        """Cancel queued (not yet running) jobs for a content item. Returns how many were cancelled."""
        with self.store.get_session() as session:
            cancelled = (
                session.query(Job)
                .filter(Job.content_id == content_id, Job.status == "pending")
                .update({"status": "cancelled"})
            )
            session.commit()
            return cancelled

    def list_jobs(self, status: Optional[str] = None, limit: int = 50) -> list[ScheduledJob]:
        with self.store.get_session() as session:
            query = session.query(Job)
            if status:
                query = query.filter(Job.status == status)
            rows = query.order_by(Job.id.desc()).limit(limit).all()
            return [
                ScheduledJob(
                    job_id=row.id,
                    content_id=row.content_id,
                    platform=row.platform,
                    status=row.status,
                    run_at=(row.scheduled_at or datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=timezone.utc),
                )
                for row in rows
            ]
