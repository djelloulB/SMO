"""Attached media: agent-supplied images and their publication (FR-07).

WordPress uploads to the media library and sets the post's featured image.
LinkedIn uploads through /rest/images and references the image URN.
X image publishing is covered in ``test_x.py`` (upload then media_ids).
"""
import base64
import json
import time
from pathlib import Path

import httpx
import pytest

import smo.connectors.linkedin as linkedin_module
import smo.connectors.wordpress as wordpress_module
import smo.services.publishing_service as publishing_module
from smo.config.security import SecurityLayer
from smo.connectors.linkedin import LinkedInConnector, LinkedInToken
from smo.content.models import SourceItem
from smo.mcp_server.auth import Identity
from smo.mcp_server.server import build_server
from smo.memory.sqlite_store import SQLiteStore
from smo.services.content_service import ContentError, ContentService
from smo.services.media_service import MAX_MEDIA_BYTES, MediaError, MediaService
from smo.services.publishing_service import PublishingService
from smo.utils.safeio import resolve_media_ref

PNG = b"\x89PNG\r\n\x1a\n" + b"fake png body"

SECURITY = dict(
    human_gate=True,
    budget_per_day_usd=1.5,
    mcp_scopes_read=frozenset({"read"}),
    mcp_scopes_content=frozenset({"content"}),
    mcp_scopes_control=frozenset(),
    mcp_scopes_admin=frozenset(),
)


def _draft(tmp_db, link, platform="linkedin"):
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    record = content.ingest_item(
        SourceItem(title="Title", summary="Useful update for readers", link=link, source="rss:m"),
        platform=platform,
    ).record
    content.approve(record.id)
    return store, content, record


def _png_for(tmp_path, name="input.png"):
    path = tmp_path / name
    path.write_bytes(PNG)
    return path


# --- MediaService ---

def test_attach_from_base64_stores_file_and_links_draft(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m1")
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")

    receipt = service.attach(record.id, data_base64=base64.b64encode(PNG).decode())

    assert receipt.bytes_written == len(PNG)
    assert receipt.extension == ".png"
    assert Path(receipt.path).is_file()
    assert content.get(record.id).media_ref == receipt.path


def test_attach_from_path_inside_inputs(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m2")
    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / "photo.png").write_bytes(PNG)
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=inputs)

    receipt = service.attach(record.id, path="photo.png")

    assert receipt.extension == ".png"
    assert content.get(record.id).media_ref == receipt.path


def test_attach_rejects_traversal_outside_inputs(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m3")
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")

    with pytest.raises(MediaError):
        service.attach(record.id, path="../escape.png")


def test_attach_rejects_non_image_bytes(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m4")
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")

    with pytest.raises(MediaError, match="unsupported image format"):
        service.attach(record.id, data_base64=base64.b64encode(b"<svg>not an image</svg>").decode())


def test_attach_requires_exactly_one_source(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m5")
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")

    with pytest.raises(MediaError, match="exactly one"):
        service.attach(record.id)


def test_attach_rejects_oversized_payload_before_decoding(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m6")
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")
    oversized = "A" * (MAX_MEDIA_BYTES * 2)

    with pytest.raises(MediaError, match="larger than"):
        service.attach(record.id, data_base64=oversized)


def test_attach_unknown_content_is_refused(tmp_db, tmp_path):
    _, content, _ = _draft(tmp_db, "https://example.com/m7")
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")

    with pytest.raises(ContentError, match="not found"):
        service.attach(999, data_base64=base64.b64encode(PNG).decode())


def test_attach_refuses_already_published_content(tmp_db, tmp_path):
    _, content, record = _draft(tmp_db, "https://example.com/m8")
    content.mark_published(record.id)
    service = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")

    with pytest.raises(MediaError, match="already published"):
        service.attach(record.id, data_base64=base64.b64encode(PNG).decode())


def test_media_ref_outside_media_dir_is_refused(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")

    with pytest.raises(ValueError, match="outside the media directory"):
        resolve_media_ref(str(outside), tmp_path / "media")


# --- WordPress ---

class FakeWPGuard:
    instances = []
    responses = []

    def __init__(self, allowlist=None):
        self.allowlist = allowlist
        self.calls = []
        FakeWPGuard.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json=None, headers=None, content=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "content": content})
        status, body = FakeWPGuard.responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


@pytest.fixture
def wp(monkeypatch):
    FakeWPGuard.instances = []
    monkeypatch.setattr(wordpress_module, "SSRFGuard", FakeWPGuard)
    monkeypatch.setenv("ACCOUNTS_WORDPRESS_SITE", "https://blog.example.com")
    monkeypatch.setenv("WORDPRESS_APP_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", "abcd efgh ijkl")
    return FakeWPGuard


@pytest.mark.asyncio
async def test_wordpress_upload_then_publish_with_featured_media(tmp_db, tmp_path, wp, monkeypatch):
    monkeypatch.setattr(publishing_module, "security_layer", SecurityLayer(dry_run=False, **SECURITY))
    wp.responses = [
        (201, {"id": 77, "source_url": "https://blog.example.com/img.png"}),
        (201, {"id": 42, "link": "https://blog.example.com/?p=42"}),
    ]
    _, content, record = _draft(tmp_db, "https://example.com/wp-media", platform="wordpress")
    media = MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in")
    media.attach(record.id, data_base64=base64.b64encode(PNG).decode())

    result = await PublishingService(
        content_service=content, dryrun_dir=tmp_path / "dry", media_dir=tmp_path / "media"
    ).publish(record.id, "wordpress")

    assert result.status == "published"
    # upload_media and publish_post each open their own guard.
    upload, post = [call for instance in wp.instances for call in instance.calls]
    assert upload["url"].endswith("/wp-json/wp/v2/media")
    assert upload["content"] == PNG
    assert upload["headers"]["Content-Type"] == "image/png"
    assert upload["headers"]["Content-Disposition"].startswith("attachment; filename=")
    assert post["json"]["featured_media"] == 77
    assert content.get(record.id).media_ref


@pytest.mark.asyncio
async def test_wordpress_dry_run_reports_media_without_uploading(tmp_db, tmp_path, wp, monkeypatch):
    monkeypatch.setattr(publishing_module, "security_layer", SecurityLayer(dry_run=True, **SECURITY))
    _, content, record = _draft(tmp_db, "https://example.com/wp-dry-media", platform="wordpress")
    MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in").attach(
        record.id, data_base64=base64.b64encode(PNG).decode()
    )

    result = await PublishingService(
        content_service=content, dryrun_dir=tmp_path / "dry", media_dir=tmp_path / "media"
    ).publish(record.id, "wordpress")

    written = json.loads(open(result.dry_run_path, encoding="utf-8").read())
    assert written["featured_media"] == "DRY_RUN"
    assert written["media_filename"].endswith(".png")
    assert wp.instances == []


# --- LinkedIn ---

class FakeLinkedInClient:
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        FakeLinkedInClient.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        status, body = FakeLinkedInClient.responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


class FakeUploadGuard:
    calls = []

    def __init__(self, allowlist=None):
        self.allowlist = allowlist

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def put(self, url, content=None, headers=None):
        FakeUploadGuard.calls.append({"url": url, "content": content, "headers": headers})
        return httpx.Response(201, request=httpx.Request("PUT", url))


def _linkedin(monkeypatch):
    FakeLinkedInClient.calls = []
    FakeLinkedInClient.responses = [
        (
            201,
            {"value": {"uploadUrl": "https://media.licdn.com/upload/1", "image": "urn:li:image:abc"}},
        ),
        (201, {"id": "urn:li:share:9"}),
    ]
    FakeUploadGuard.calls = []
    monkeypatch.setattr(linkedin_module, "SSRFGuard", FakeUploadGuard)
    monkeypatch.setattr(linkedin_module.httpx, "AsyncClient", FakeLinkedInClient)


@pytest.mark.asyncio
async def test_linkedin_image_is_registered_uploaded_and_referenced(monkeypatch):
    _linkedin(monkeypatch)
    connector = LinkedInConnector("id", "secret", "https://example.com/callback")
    token = LinkedInToken("access", None, int(time.time()) + 3600)

    result = await connector.publish_post(
        token, "Hello", author_urn="urn:li:person:1", image_data=PNG
    )

    assert result["id"] == "urn:li:share:9"
    init, publish = FakeLinkedInClient.calls
    assert init["url"].endswith("/rest/images?action=initializeUpload")
    assert init["headers"]["LinkedIn-Version"] == linkedin_module.DEFAULT_LINKEDIN_API_VERSION
    assert init["json"] == {"initializeUploadRequest": {"owner": "urn:li:person:1"}}
    assert FakeUploadGuard.calls[0]["content"] == PNG
    share = publish["json"]["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert share["shareMediaCategory"] == "IMAGE"
    assert share["media"] == [{"status": "READY", "media": "urn:li:image:abc"}]


@pytest.mark.asyncio
async def test_linkedin_refuses_unexpected_upload_host(monkeypatch):
    _linkedin(monkeypatch)
    FakeLinkedInClient.responses = [
        (201, {"value": {"uploadUrl": "https://evil.example.com/upload", "image": "urn:li:image:abc"}}),
    ]
    connector = LinkedInConnector("id", "secret", "https://example.com/callback")
    token = LinkedInToken("access", None, int(time.time()) + 3600)

    with pytest.raises(ValueError, match="unexpected upload host"):
        await connector.publish_post(
            token, "Hello", author_urn="urn:li:person:1", image_data=PNG
        )


def test_text_only_linkedin_payload_is_unchanged():
    payload = LinkedInConnector.build_post_payload("urn:li:person:1", "Hi")

    share = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert share["shareMediaCategory"] == "NONE"
    assert "media" not in share


# --- X ---

# X image upload and publication are tested in test_x.py (upload_media + media_ids).

# --- MCP ---

def test_attach_image_is_content_scope_and_reports_media(tmp_db, tmp_path, monkeypatch):
    import smo.services.media_service as media_module

    monkeypatch.setattr(media_module, "BASE_OUTPUT_DIR", tmp_path / "media")
    monkeypatch.setattr(media_module, "BASE_INPUT_DIR", tmp_path / "in")
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    record = content.ingest_item(
        SourceItem(title="T", summary="S", link="https://example.com/mcp-media", source="rss:m")
    ).record

    server = build_server(Identity("agent:hermes", frozenset({"read", "content"})))
    read_only = build_server(Identity("agent:reader", frozenset({"read"})))

    assert "attach_image" in {t.name for t in server._tool_manager.list_tools()}
    assert "attach_image" not in {t.name for t in read_only._tool_manager.list_tools()}

    tool = server._tool_manager.get_tool("attach_image")
    result = tool.fn(
        content_id=record.id, data_base64=base64.b64encode(PNG).decode()
    )
    assert result["bytes"] == len(PNG)
    assert content.get(record.id).media_ref == result["path"]