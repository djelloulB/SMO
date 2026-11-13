"""MCP control-scope tools (publish_now, pause/resume, metrics poll) and their guards."""
from datetime import datetime, timezone

import pytest

import smo.mcp_server.server as server_module
from smo.content.models import SourceItem
from smo.mcp_server.auth import Identity
from smo.mcp_server.server import build_server
from smo.memory.sqlite_store import SQLiteStore
from smo.services.content_service import ContentService
from smo.services.platform_service import PlatformService
from smo.services.publishing_service import PublishResult
from smo.services.scheduling_service import SchedulingService

NOW = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
ADMIN = Identity("agent:admin", frozenset({"read", "content", "control", "admin"}))


class FakePublishing:
    calls = []

    def __init__(self, content_service=None, **_):
        self.content = content_service

    async def publish(self, content_id, account_id, **kwargs):
        FakePublishing.calls.append((content_id, account_id))
        return PublishResult(content_id=content_id, status="dry_run", dry_run_path="/tmp/payload.json")


@pytest.fixture
def server(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    FakePublishing.calls = []
    monkeypatch.setattr(server_module, "PublishingService", FakePublishing)
    return build_server(ADMIN)


def tool(server, name):
    return server._tool_manager.get_tool(name).fn


def approved(tmp_db, link):
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    record = content.ingest_item(
        SourceItem(title="A title", summary="Useful update", link=link, source="rss:test")
    ).record
    return content, content.approve(record.id)


def test_control_tools_hidden_from_content_scope(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    names = {t.name for t in build_server(Identity("agent:hermes", frozenset({"read", "content"})))._tool_manager.list_tools()}

    assert not {"publish_now", "pause_platform", "resume_platform", "trigger_metrics_poll"} & names


@pytest.mark.asyncio
async def test_publish_now_cancels_queued_job_and_respects_dry_run(server, tmp_db):
    content, record = approved(tmp_db, "https://example.com/now")
    store = SQLiteStore(tmp_db)
    job = SchedulingService(content, store).schedule(record.id, "acc", strategy="fixed_slots", slots=["09:00"], now=NOW)

    result = await tool(server, "publish_now")(content_id=record.id, account_id="acc")

    assert result["status"] == "dry_run"
    assert FakePublishing.calls == [(record.id, "acc")]
    assert SchedulingService(content, store).list_jobs(status="cancelled")[0].job_id == job.job_id


@pytest.mark.asyncio
async def test_paused_platform_blocks_publish_now(server, tmp_db):
    content, record = approved(tmp_db, "https://example.com/paused-now")
    tool(server, "pause_platform")(platform="linkedin", reason="maintenance")

    with pytest.raises(ValueError, match="paused"):
        await tool(server, "publish_now")(content_id=record.id, account_id="acc")
    assert FakePublishing.calls == []

    statuses = tool(server, "resume_platform")(platform="linkedin")["platforms"]
    assert statuses["linkedin"] == "active"


def test_pause_then_health_reports_it(server, tmp_db):
    tool(server, "pause_platform")(platform="linkedin")

    health = tool(server, "get_health")()

    assert health["platforms"]["linkedin"] == "paused"
    assert PlatformService(SQLiteStore(tmp_db)).is_open("linkedin") is True


@pytest.mark.asyncio
async def test_trigger_metrics_poll_reports_counts(server, monkeypatch):
    from smo.services.metrics_service import PollResult

    async def fake_poll(self, now=None):
        return PollResult(polled=2, stored=1, errors=1)

    monkeypatch.setattr(server_module.MetricsService, "poll", fake_poll)

    result = await tool(server, "trigger_metrics_poll")()

    assert result == {"polled": 2, "stored": 1, "errors": 1}
