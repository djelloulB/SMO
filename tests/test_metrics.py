"""Read-only metrics collection (FR-13) and its use for weighted scheduling."""
import time
from datetime import datetime, timezone

import pytest

from smo.connectors.linkedin import LinkedInToken
from smo.content.models import SourceItem
from smo.memory.sqlite_store import ContentItem, Job, SQLiteStore
from smo.services.content_service import ContentService
from smo.services.metrics_service import MetricsService
from smo.services.scheduling_service import SchedulingService, engagement_by_hour

NOW = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


class FakeMetricsConnector:
    def __init__(self, fail=False):
        self.requested = []
        self.fail = fail

    def load_token(self, account_id):
        return LinkedInToken("access", None, int(time.time()) + 3600)

    def save_token(self, account_id, token):
        pass

    async def fetch_metrics(self, token, post_urn):
        self.requested.append(post_urn)
        if self.fail:
            raise RuntimeError("linkedin unavailable")
        return {"likes": 7, "views": 0, "reach": 40}


def published_content(tmp_db, link, external_id="urn:li:share:9"):
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    record = content.ingest_item(
        SourceItem(title="A title", summary="Useful update", link=link, source="rss:test")
    ).record
    content.approve(record.id)
    job = SchedulingService(content, store).schedule(
        record.id, "acc-metrics", strategy="fixed_slots", slots=["09:00"], now=NOW
    )
    with store.get_session() as session:
        session.get(Job, job.job_id).status = "completed"
        session.commit()
    content.mark_published(record.id)
    content.set_external_id(record.id, external_id)
    return store, content, record


@pytest.mark.asyncio
async def test_poll_stores_snapshot_for_published_post(tmp_db):
    store, content, record = published_content(tmp_db, "https://example.com/metrics")
    connector = FakeMetricsConnector()

    result = await MetricsService(store=store, content=content, connector=connector).poll(now=NOW)

    assert (result.polled, result.stored, result.errors) == (1, 1, 0)
    assert connector.requested == ["urn:li:share:9"]
    snapshots = MetricsService(store=store, content=content, connector=connector).list_snapshots(record.id)
    assert snapshots[0]["likes"] == 7
    assert snapshots[0]["reach"] == 40


@pytest.mark.asyncio
async def test_failing_post_does_not_stop_poll(tmp_db):
    store, content, _ = published_content(tmp_db, "https://example.com/metrics-fail")

    result = await MetricsService(store=store, content=content, connector=FakeMetricsConnector(fail=True)).poll(now=NOW)

    assert (result.polled, result.stored, result.errors) == (1, 0, 1)


@pytest.mark.asyncio
async def test_snapshots_feed_engagement_by_publish_hour(tmp_db):
    store, content, record = published_content(tmp_db, "https://example.com/metrics-hour")
    await MetricsService(store=store, content=content, connector=FakeMetricsConnector()).poll(now=NOW)

    with store.get_session() as session:
        published_at = session.get(ContentItem, record.id).published_at

    assert engagement_by_hour(store, min_samples=1) == {published_at.hour: 47.0}
    # One post is not enough evidence for the scheduler's default threshold.
    assert engagement_by_hour(store) == {}
