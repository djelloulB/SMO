"""Content workflow + publishing service tests (FR-09, FR-12, human gate)."""
import json
import time

import pytest

import smo.services.publishing_service as publishing_module
from smo.config.security import SecurityLayer
from smo.config.settings import settings
from smo.connectors.linkedin import LinkedInToken
from smo.content.models import SourceItem
from smo.services.content_service import (
    ContentService,
    InvalidTransition,
    ModerationError,
)
from smo.services.publishing_service import PublishingService


class FakeConnector:
    def __init__(self, profile=None, fail=False):
        self.profile = profile or {"id": "member-1"}
        self.fail = fail
        self.published = None
        self.saved_tokens = 0

    def load_token(self, account_id):
        return LinkedInToken("access", None, int(time.time()) + 3600)

    async def get_profile(self, token):
        return self.profile

    async def publish_post(self, token, text, visibility="PUBLIC", organization_id=None, author_urn=None, image_data=None):
        if self.fail:
            raise RuntimeError("linkedin down")
        self.published = {
            "text": text,
            "visibility": visibility,
            "organization_id": organization_id,
            "author_urn": author_urn,
        }
        return {"id": "urn:li:share:1"}

    def save_token(self, account_id, token):
        self.saved_tokens += 1


def make_service(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    return ContentService()


def ingest(service, link="https://example.com/1", summary="Useful update"):
    return service.ingest_item(
        SourceItem(
            title="A title",
            summary=summary,
            link=link,
            source="rss:test",
        )
    )


def test_ingest_and_dedupe(tmp_db, monkeypatch):
    service = make_service(tmp_db, monkeypatch)

    first = ingest(service)
    second = ingest(service)

    assert first.duplicate is False
    assert first.record.status == "draft"
    assert second.duplicate is True


def test_approve_then_invalid_transition(tmp_db, monkeypatch):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record

    approved = service.approve(record.id)

    assert approved.status == "approved"
    with pytest.raises(InvalidTransition):
        service.approve(record.id)


def test_reject_draft(tmp_db, monkeypatch):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record

    rejected = service.reject(record.id)

    assert rejected.status == "rejected"


def test_ingest_blocked_by_moderation(tmp_db, monkeypatch):
    service = make_service(tmp_db, monkeypatch)
    monkeypatch.setattr(settings, "brand_voice_banned_topics", ["politics"])

    result = ingest(service, summary="A post about politics")

    assert result.record.status == "rejected"
    with pytest.raises(InvalidTransition):
        service.approve(result.record.id)


def test_approve_rechecks_moderation(tmp_db, monkeypatch):
    monkeypatch.setattr(settings, "brand_voice_banned_topics", [])
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service, summary="A post about politics").record
    assert record.status == "draft"

    monkeypatch.setattr(settings, "brand_voice_banned_topics", ["politics"])
    with pytest.raises(ModerationError):
        service.approve(record.id)


def test_list_and_get(tmp_db, monkeypatch):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record

    assert service.get(record.id).text == record.text
    assert [item.id for item in service.list()] == [record.id]
    assert service.list(status="approved") == []


@pytest.mark.asyncio
async def test_publish_requires_approval(tmp_db, monkeypatch, tmp_path):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record
    publishing = PublishingService(
        connector=FakeConnector(),
        content_service=service,
        dryrun_dir=tmp_path / "dryrun",
    )

    with pytest.raises(InvalidTransition):
        await publishing.publish(record.id, "account-1")


@pytest.mark.asyncio
async def test_dry_run_writes_exact_payload(tmp_db, monkeypatch, tmp_path):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record
    service.approve(record.id)
    publishing = PublishingService(
        connector=FakeConnector(),
        content_service=service,
        dryrun_dir=tmp_path / "dryrun",
    )

    result = await publishing.publish(record.id, "account-1", organization_id="123")

    assert result.status == "dry_run"
    payload = json.loads((tmp_path / "dryrun" / result.dry_run_path.split("\\")[-1].split("/")[-1]).read_text())
    assert payload["author"] == "urn:li:organization:123"
    assert payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"] == record.text


@pytest.mark.asyncio
async def test_dry_run_resolves_member_author(tmp_db, monkeypatch, tmp_path):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record
    service.approve(record.id)
    publishing = PublishingService(
        connector=FakeConnector(profile={"id": "member-42"}),
        content_service=service,
        dryrun_dir=tmp_path / "dryrun",
    )

    result = await publishing.publish(record.id, "account-1")

    assert result.payload["author"] == "urn:li:person:member-42"


@pytest.mark.asyncio
async def test_live_publish_marks_published(tmp_db, monkeypatch, tmp_path):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record
    service.approve(record.id)
    connector = FakeConnector()
    publishing = PublishingService(
        connector=connector,
        content_service=service,
        dryrun_dir=tmp_path / "dryrun",
    )
    monkeypatch.setattr(
        publishing_module,
        "security_layer",
        SecurityLayer(
            dry_run=False,
            human_gate=True,
            budget_per_day_usd=1.5,
            mcp_scopes_read=frozenset({"read"}),
            mcp_scopes_content=frozenset({"content"}),
            mcp_scopes_control=frozenset(),
            mcp_scopes_admin=frozenset(),
        ),
    )

    result = await publishing.publish(record.id, "account-1", organization_id="123")

    assert result.status == "published"
    assert connector.published["organization_id"] == "123"
    assert service.get(record.id).status == "published"


@pytest.mark.asyncio
async def test_live_publish_failure_marks_failed(tmp_db, monkeypatch, tmp_path):
    service = make_service(tmp_db, monkeypatch)
    record = ingest(service).record
    service.approve(record.id)
    publishing = PublishingService(
        connector=FakeConnector(fail=True),
        content_service=service,
        dryrun_dir=tmp_path / "dryrun",
    )
    monkeypatch.setattr(
        publishing_module,
        "security_layer",
        SecurityLayer(
            dry_run=False,
            human_gate=True,
            budget_per_day_usd=1.5,
            mcp_scopes_read=frozenset({"read"}),
            mcp_scopes_content=frozenset({"content"}),
            mcp_scopes_control=frozenset(),
            mcp_scopes_admin=frozenset(),
        ),
    )

    with pytest.raises(RuntimeError, match="linkedin down"):
        await publishing.publish(record.id, "account-1")

    assert service.get(record.id).status == "failed"
