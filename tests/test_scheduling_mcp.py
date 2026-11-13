"""Scheduling (FR-10) and MCP control plane (FR-14, FR-15) tests."""
import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import smo.services.scheduling_service as scheduling_module
from smo.content.models import SourceItem
from smo.memory.sqlite_store import ContentItem, Job, MetricSnapshot, SQLiteStore
from smo.mcp_server.auth import Identity, authenticate, load_agent_keys
from smo.mcp_server.server import build_server
from smo.orchestrator.retry import MAX_ATTEMPTS, MAX_DELAY_SECONDS, backoff_seconds, is_retryable
from smo.services.content_service import ContentService, InvalidTransition
from smo.services.platform_service import BREAKER_COOLDOWN, BREAKER_THRESHOLD, PlatformService
from smo.services.scheduling_service import (
    SchedulingService,
    compute_run_at,
    engagement_analysis,
    engagement_by_hour,
    run_due_jobs,
)

NOW = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


def approved_content(tmp_db, link="https://example.com/sched", platform="linkedin"):
    service = ContentService(SQLiteStore(tmp_db))
    record = service.ingest_item(
        SourceItem(title="A title", summary="Useful update", link=link, source="rss:test"),
        platform=platform,
    ).record
    return service, service.approve(record.id)


def published_with_metrics(tmp_db, link, published_at, snapshots, platform="linkedin"):
    """Create a published item and insert (likes, reach) snapshots in order."""
    service, record = approved_content(tmp_db, link, platform)
    service.mark_published(record.id)
    with SQLiteStore(tmp_db).get_session() as session:
        session.get(ContentItem, record.id).published_at = published_at.replace(tzinfo=None)
        for likes, reach in snapshots:
            session.add(
                MetricSnapshot(
                    content_id=record.id,
                    platform=platform,
                    likes=likes,
                    reach=reach,
                    timestamp=published_at.replace(tzinfo=None),
                )
            )
        session.commit()
    return service, record


# --- Time computation -------------------------------------------------------

def test_fixed_slots_picks_next_slot_today():
    run_at = compute_run_at("fixed_slots", NOW, slots=["09:00", "17:00"])
    assert run_at == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def test_fixed_slots_rolls_over_to_tomorrow():
    late = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    run_at = compute_run_at("fixed_slots", late, slots=["09:00", "17:00"])
    assert run_at == datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)


def test_fixed_slots_respects_timezone():
    # 08:00 UTC is already 09:00 in Paris, so the 09:00 Paris slot is passed.
    run_at = compute_run_at("fixed_slots", NOW, timezone_name="Europe/Paris", slots=["09:00"])
    assert run_at == datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc)


def test_randomized_window_stays_inside_window():
    rng = random.Random(42)
    for _ in range(50):
        run_at = compute_run_at("randomized_window", NOW, window="09:00-12:00", rng=rng)
        assert datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc) <= run_at <= datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_randomized_window_moves_to_next_day_after_window():
    late = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    run_at = compute_run_at("randomized_window", late, window="09:00-12:00", rng=random.Random(1))
    assert run_at.date() == datetime(2026, 1, 2).date()
    assert 9 <= run_at.hour <= 12


def test_weighted_uses_best_engagement_hour():
    run_at = compute_run_at(
        "weighted_by_engagement", NOW, engagement_scores={18: 10.0, 9: 3.0}, exploration_rate=0.0
    )
    assert run_at == datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)


def test_weighted_falls_back_without_history():
    run_at = compute_run_at("weighted_by_engagement", NOW, engagement_scores={})
    assert run_at == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


class FixedRandom:
    """Random double for exploration decisions plus a deterministic choice."""

    def __init__(self, draw):
        self.draw = draw

    def random(self):
        return self.draw

    def choice(self, sequence):
        return sequence[0]


def test_weighted_explores_another_daytime_hour():
    run_at = compute_run_at(
        "weighted_by_engagement",
        NOW,
        engagement_scores={18: 10.0, 9: 3.0},
        rng=FixedRandom(0.0),
        exploration_rate=0.5,
    )

    assert run_at.hour == 7  # first daytime hour that is not the best one
    assert run_at > NOW


def test_weighted_exploitation_is_deterministic_when_rng_does_not_explore():
    run_at = compute_run_at(
        "weighted_by_engagement",
        NOW,
        engagement_scores={18: 10.0, 9: 3.0},
        rng=FixedRandom(0.99),
        exploration_rate=0.5,
    )
    assert run_at.hour == 18


def test_engagement_uses_only_the_latest_snapshot_per_post(tmp_db):
    published_with_metrics(
        tmp_db, "https://example.com/latest", datetime(2025, 12, 30, 9), [(1, 0), (100, 0)]
    )

    scores = engagement_by_hour(SQLiteStore(tmp_db), min_samples=1)

    assert scores == {9: pytest.approx(100.0)}


def test_engagement_is_per_platform(tmp_db):
    published_with_metrics(tmp_db, "https://example.com/li", datetime(2025, 12, 30, 9), [(100, 0)])
    published_with_metrics(
        tmp_db, "https://example.com/x", datetime(2025, 12, 30, 15), [(5, 0)], platform="x"
    )

    assert engagement_by_hour(SQLiteStore(tmp_db), platform="x", min_samples=1) == {
        15: pytest.approx(5.0)
    }
    assert engagement_by_hour(SQLiteStore(tmp_db), platform="linkedin", min_samples=1) == {
        9: pytest.approx(100.0)
    }


def test_recent_posts_outweigh_older_ones_for_the_same_hour(tmp_db):
    published_with_metrics(
        tmp_db, "https://example.com/old", NOW - timedelta(days=120, minutes=5), [(100, 0)]
    )
    published_with_metrics(tmp_db, "https://example.com/new", NOW - timedelta(minutes=5), [(10, 0)])

    scores = engagement_by_hour(SQLiteStore(tmp_db), now=NOW, min_samples=1)

    # Same hour (07:55): the four-half-life-old post carries 1/16 of the weight.
    assert 10.0 < scores[7] < 20.0


def test_hours_with_too_few_samples_are_ignored(tmp_db):
    published_with_metrics(tmp_db, "https://example.com/one", datetime(2025, 12, 30, 18), [(100, 0)])

    assert engagement_analysis(SQLiteStore(tmp_db))[0].samples == 1
    assert engagement_by_hour(SQLiteStore(tmp_db), min_samples=3) == {}
    run_at = compute_run_at("weighted_by_engagement", NOW, engagement_scores={})
    assert run_at.hour == 9


def test_schedule_optimizes_for_the_item_platform(tmp_db):
    for index in range(3):
        published_with_metrics(
            tmp_db, f"https://example.com/li-{index}", datetime(2025, 12, 30, 9), [(100, 0)]
        )
        published_with_metrics(
            tmp_db,
            f"https://example.com/x-{index}",
            datetime(2025, 12, 30, 15, index),
            [(5, 0)],
            platform="x",
        )
    service = SchedulingService(store=SQLiteStore(tmp_db))
    _, record = approved_content(tmp_db, "https://example.com/x-to-schedule", platform="x")

    job = service.schedule(
        record.id,
        "x-account",
        strategy="weighted_by_engagement",
        now=NOW,
        rng=FixedRandom(0.99),
    )

    assert job.platform == "x"
    assert job.run_at == datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        compute_run_at("random_guess", NOW)


def test_invalid_window_rejected():
    with pytest.raises(ValueError):
        compute_run_at("randomized_window", NOW, window="12:00-09:00")


# --- Queueing ---------------------------------------------------------------

def test_schedule_requires_approved_content(tmp_db):
    service = ContentService(SQLiteStore(tmp_db))
    record = service.ingest_item(
        SourceItem(title="A title", summary="Useful update", link="https://example.com/draft", source="rss:test")
    ).record

    with pytest.raises(InvalidTransition):
        SchedulingService(service).schedule(record.id, "acc", now=NOW)


def test_schedule_queues_job_and_marks_content(tmp_db):
    content, record = approved_content(tmp_db)
    scheduling = SchedulingService(content)

    job = scheduling.schedule(record.id, "acc-1", strategy="fixed_slots", slots=["09:00"], now=NOW, actor_id="test")

    assert job.status == "pending"
    assert job.run_at == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    assert content.get(record.id).status == "scheduled"
    with SQLiteStore(tmp_db).get_session() as session:
        row = session.get(Job, job.job_id)
        assert row.account_id == "acc-1"
        assert row.scheduled_at == datetime(2026, 1, 1, 9, 0)


def test_schedule_at_past_time_rejected(tmp_db):
    content, record = approved_content(tmp_db, link="https://example.com/past")
    with pytest.raises(ValueError):
        SchedulingService(content).schedule(record.id, "acc", at=NOW - timedelta(hours=1), now=NOW)


# --- Dispatcher -------------------------------------------------------------

class FakePublisher:
    calls = []
    error = None

    def __init__(self, content_service=None, **_):
        self.content = content_service

    async def publish(self, content_id, account_id, **kwargs):
        FakePublisher.calls.append((content_id, account_id, kwargs))
        if FakePublisher.error is not None:
            raise FakePublisher.error
        return None


@pytest.fixture
def fake_publisher(monkeypatch):
    FakePublisher.calls = []
    FakePublisher.error = None
    monkeypatch.setattr(scheduling_module, "PublishingService", FakePublisher)
    return FakePublisher


def test_due_job_runs_once(tmp_db, fake_publisher):
    content, record = approved_content(tmp_db, link="https://example.com/due")
    store = SQLiteStore(tmp_db)
    job = SchedulingService(content, store).schedule(
        record.id, "acc-9", strategy="fixed_slots", slots=["09:00"], now=NOW, organization_id="org-1"
    )

    assert run_due_jobs(now=NOW, store=store) == 0
    assert run_due_jobs(now=job.run_at + timedelta(minutes=1), store=store) == 1
    assert run_due_jobs(now=job.run_at + timedelta(minutes=2), store=store) == 0

    assert len(fake_publisher.calls) == 1
    content_id, account_id, kwargs = fake_publisher.calls[0]
    assert (content_id, account_id) == (record.id, "acc-9")
    assert kwargs["organization_id"] == "org-1"
    assert SchedulingService(content, store).list_jobs(status="completed")[0].job_id == job.job_id


def test_permanent_failure_marks_job_and_content_failed(tmp_db, fake_publisher):
    fake_publisher.error = RuntimeError("bad payload")
    content, record = approved_content(tmp_db, link="https://example.com/fail")
    store = SQLiteStore(tmp_db)
    job = SchedulingService(content, store).schedule(
        record.id, "acc", strategy="fixed_slots", slots=["09:00"], now=NOW
    )

    run_due_jobs(now=job.run_at + timedelta(minutes=1), store=store)

    assert SchedulingService(content, store).list_jobs(status="failed")[0].job_id == job.job_id
    assert content.get(record.id).status == "failed"


# --- Retries, dead-letter, circuit breaker (FR-11) --------------------------

def _transient_error():
    request = httpx.Request("POST", "https://api.linkedin.com/v2/ugcPosts")
    return httpx.ConnectError("connection refused", request=request)


def _queued_job(tmp_db, link):
    content, record = approved_content(tmp_db, link=link)
    store = SQLiteStore(tmp_db)
    job = SchedulingService(content, store).schedule(
        record.id, "acc", strategy="fixed_slots", slots=["09:00"], now=NOW
    )
    return content, record, store, job


def test_transient_failure_requeues_with_backoff(tmp_db, fake_publisher):
    fake_publisher.error = _transient_error()
    content, record, store, job = _queued_job(tmp_db, "https://example.com/retry")
    first = job.run_at + timedelta(minutes=1)

    run_due_jobs(now=first, store=store, rng=random.Random(0))

    queued = SchedulingService(content, store).list_jobs(status="pending")[0]
    assert queued.job_id == job.job_id
    assert queued.run_at > first
    assert content.get(record.id).status == "scheduled"
    with store.get_session() as session:
        row = session.get(Job, job.job_id)
        assert row.retry_count == 1
        assert row.last_error == "ConnectError"


def test_transient_failure_dead_letters_after_max_attempts(tmp_db, fake_publisher):
    fake_publisher.error = _transient_error()
    content, record, store, job = _queued_job(tmp_db, "https://example.com/dead")
    moment = job.run_at + timedelta(minutes=1)

    for _ in range(MAX_ATTEMPTS):
        moment += timedelta(hours=2)
        run_due_jobs(now=moment, store=store, rng=random.Random(0))

    assert SchedulingService(content, store).list_jobs(status="dead_letter")[0].job_id == job.job_id
    assert content.get(record.id).status == "failed"
    assert len(fake_publisher.calls) == MAX_ATTEMPTS


def test_read_timeout_is_not_retried(tmp_db, fake_publisher):
    request = httpx.Request("POST", "https://api.linkedin.com/v2/ugcPosts")
    fake_publisher.error = httpx.ReadTimeout("no answer", request=request)
    content, record, store, job = _queued_job(tmp_db, "https://example.com/timeout")

    run_due_jobs(now=job.run_at + timedelta(minutes=1), store=store)

    assert SchedulingService(content, store).list_jobs(status="failed")[0].job_id == job.job_id


def test_circuit_breaker_holds_platform_then_recovers(tmp_db, fake_publisher):
    fake_publisher.error = _transient_error()
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    jobs = []
    for index in range(BREAKER_THRESHOLD):
        _, record, _, job = _queued_job(tmp_db, f"https://example.com/breaker-{index}")
        jobs.append(job)
    moment = jobs[0].run_at + timedelta(minutes=1)

    for _ in range(BREAKER_THRESHOLD):
        run_due_jobs(now=moment, store=store, rng=random.Random(0))
    attempts_before = len(fake_publisher.calls)
    held_at = moment + timedelta(seconds=30)
    run_due_jobs(now=held_at, store=store, rng=random.Random(0))
    assert len(fake_publisher.calls) == attempts_before
    assert PlatformService(store).statuses(now=held_at)["linkedin"] == "circuit_open"

    fake_publisher.error = None
    recovered_at = moment + BREAKER_COOLDOWN + timedelta(hours=1)
    run_due_jobs(now=recovered_at, store=store, rng=random.Random(0))
    assert len(fake_publisher.calls) > attempts_before
    assert PlatformService(store).statuses(now=recovered_at)["linkedin"] == "active"


def test_manual_pause_blocks_until_resumed(tmp_db, fake_publisher):
    content, record, store, job = _queued_job(tmp_db, "https://example.com/paused")
    platforms = PlatformService(store)
    platforms.pause("linkedin", "maintenance")

    assert run_due_jobs(now=job.run_at + timedelta(minutes=1), store=store) == 0
    assert platforms.statuses()["linkedin"] == "paused"

    platforms.resume("linkedin")
    assert run_due_jobs(now=job.run_at + timedelta(minutes=1), store=store) == 1


def test_backoff_grows_and_is_capped():
    rng = random.Random(0)
    first, second = backoff_seconds(1, rng), backoff_seconds(2, rng)
    assert 60 <= first <= 66
    assert 120 <= second <= 132
    assert backoff_seconds(30, rng) <= MAX_DELAY_SECONDS * 1.1


def test_only_transient_http_errors_are_retryable():
    request = httpx.Request("POST", "https://api.linkedin.com/v2/ugcPosts")

    def status_error(code):
        return httpx.HTTPStatusError("err", request=request, response=httpx.Response(code, request=request))

    assert is_retryable(status_error(503)) is True
    assert is_retryable(status_error(429)) is True
    assert is_retryable(status_error(400)) is False
    assert is_retryable(httpx.ReadTimeout("slow", request=request)) is False


# --- Agent keys -------------------------------------------------------------

def test_agent_scopes_follow_key_name():
    env = {"MCP_API_KEY_HERMES": "key-hermes", "MCP_API_KEY_OTHER": "key-other", "PATH": "x"}
    keys = load_agent_keys(env)

    assert keys["key-hermes"].actor_id == "agent:hermes"
    assert keys["key-hermes"].scopes == frozenset({"read", "content"})
    assert keys["key-other"].scopes == frozenset({"read"})


def test_authenticate_rejects_unknown_key():
    env = {"MCP_API_KEY_HERMES": "key-hermes"}
    assert authenticate("key-hermes", env).actor_id == "agent:hermes"
    assert authenticate("wrong", env) is None
    assert authenticate(None, env) is None
    assert authenticate("", env) is None


# --- MCP tool exposure ------------------------------------------------------

def tool_names(server):
    return {tool.name for tool in server._tool_manager.list_tools()}


def test_read_scope_hides_mutating_tools(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    names = tool_names(build_server(Identity("agent:reader", frozenset({"read"}))))

    assert {"get_health", "list_drafts", "get_draft", "get_queue"} <= names
    assert not {"approve_draft", "reject_draft", "schedule_post"} & names


def test_content_scope_exposes_workflow_tools(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    names = tool_names(build_server(Identity("agent:hermes", frozenset({"read", "content"}))))

    assert {"approve_draft", "reject_draft", "schedule_post"} <= names
