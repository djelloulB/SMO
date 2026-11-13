"""WordPress connector (FR-02) and its publishing path."""
import base64
import json

import httpx
import pytest

import smo.connectors.wordpress as wordpress_module
import smo.services.publishing_service as publishing_module
from smo.config.security import SecurityLayer
from smo.connectors.wordpress import WordPressConnector, WordPressError
from smo.content.models import SourceItem
from smo.memory.sqlite_store import SQLiteStore
from smo.orchestrator.retry import is_retryable
from smo.services.content_service import ContentService
from smo.services.publishing_service import PublishingService

SITE = "https://blog.example.com"


class FakeGuard:
    instances = []
    responses = []

    def __init__(self, allowlist=None):
        self.allowlist = allowlist
        self.calls = []
        FakeGuard.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        status, body = FakeGuard.responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


@pytest.fixture
def guard(monkeypatch):
    FakeGuard.instances = []
    FakeGuard.responses = [(201, {"id": 42, "link": "https://blog.example.com/?p=42"})]
    monkeypatch.setattr(wordpress_module, "SSRFGuard", FakeGuard)
    monkeypatch.setenv("ACCOUNTS_WORDPRESS_SITE", SITE)
    monkeypatch.setenv("WORDPRESS_APP_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "abcd efgh ijkl")
    return FakeGuard


def test_build_post_escapes_html_and_splits_paragraphs():
    payload = WordPressConnector.build_post("First <b>line</b>\n\nSecond & last", status="draft")

    assert payload["status"] == "draft"
    assert payload["title"] == "First <b>line</b>"
    assert payload["content"] == "<p>First &lt;b&gt;line&lt;/b&gt;</p><p>Second &amp; last</p>"


def test_site_must_be_https():
    with pytest.raises(WordPressError, match="https"):
        WordPressConnector("http://blog.example.com", "editor", "secret")


def test_missing_credentials_are_refused():
    with pytest.raises(WordPressError, match="credentials"):
        WordPressConnector(SITE, "", "secret")


def test_application_password_spaces_are_removed_from_auth_header():
    connector = WordPressConnector(SITE, "editor", "abcd efgh ijkl")
    expected = "Basic " + base64.b64encode(b"editor:abcdefghijkl").decode()

    assert connector._auth == expected


@pytest.mark.asyncio
async def test_publish_posts_to_rest_endpoint_through_guard(guard):
    connector = WordPressConnector(SITE, "editor", "abcd efgh ijkl")

    result = await connector.publish_post("Hello blog")

    assert result == {"id": "42", "link": "https://blog.example.com/?p=42"}
    instance = guard.instances[0]
    assert instance.allowlist == {"blog.example.com"}
    url, body, headers = instance.calls[0]
    assert url == "https://blog.example.com/wp-json/wp/v2/posts"
    assert body["content"] == "<p>Hello blog</p>"
    assert headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_server_error_is_retryable(guard):
    guard.responses = [(503, {"message": "down"})]
    connector = WordPressConnector(SITE, "editor", "secret")

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await connector.publish_post("Hello")

    assert is_retryable(caught.value) is True


def test_from_environment_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("WORDPRESS_APP_PASSWORD", raising=False)

    assert WordPressConnector.from_environment() is None


def _approved_wordpress_item(tmp_db, link):
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    record = content.ingest_item(
        SourceItem(title="Blog title", summary="Useful update for readers", link=link, source="rss:blog"),
        platform="wordpress",
    ).record
    content.approve(record.id)
    return content, record


@pytest.mark.asyncio
async def test_dry_run_writes_wordpress_payload(tmp_db, tmp_path, guard, monkeypatch):
    monkeypatch.setattr(
        publishing_module,
        "security_layer",
        SecurityLayer(
            dry_run=True,
            human_gate=True,
            budget_per_day_usd=1.5,
            mcp_scopes_read=frozenset({"read"}),
            mcp_scopes_content=frozenset({"content"}),
            mcp_scopes_control=frozenset(),
            mcp_scopes_admin=frozenset(),
        ),
    )
    content, record = _approved_wordpress_item(tmp_db, "https://example.com/wp-dry")

    result = await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(record.id, "wordpress")

    assert result.status == "dry_run"
    with open(result.dry_run_path, encoding="utf-8") as handle:
        written = json.load(handle)
    assert written["content"].startswith("<p>")
    assert guard.instances == []  # nothing sent in dry-run


@pytest.mark.asyncio
async def test_live_publish_marks_content_and_stores_post_id(tmp_db, tmp_path, guard, monkeypatch):
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
    content, record = _approved_wordpress_item(tmp_db, "https://example.com/wp-live")

    result = await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(record.id, "wordpress")

    assert result.status == "published"
    assert content.get(record.id).status == "published"
    assert content.list_published()[0] == (record.id, "wordpress", "42")


@pytest.mark.asyncio
async def test_failed_live_publish_keeps_content_when_retrying(tmp_db, tmp_path, guard, monkeypatch):
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
    guard.responses = [(503, {"message": "down"})]
    content, record = _approved_wordpress_item(tmp_db, "https://example.com/wp-retry")

    with pytest.raises(httpx.HTTPStatusError):
        await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(
            record.id, "wordpress", mark_failed_on_error=False
        )

    assert content.get(record.id).status == "approved"
