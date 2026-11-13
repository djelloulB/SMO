"""X connector (FR-05): OAuth 2.0 PKCE, encrypted token storage, refresh rotation and publishing."""
import base64
import hashlib
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

import smo.connectors.x as x_module
import smo.services.publishing_service as publishing_module
from smo.config.security import SecurityLayer
from smo.connectors.x import (
    XAuthError,
    XConnector,
    XToken,
    XTokenService,
    consume_authorization_state,
    generate_pkce,
    start_authorization,
    x_enabled,
)
from smo.content.models import SourceItem
from smo.memory.sqlite_store import OAuthPending, SQLiteStore
from smo.server.app import create_app
from smo.services.content_service import ContentError, ContentService
from smo.services.media_service import MediaService
from smo.services.publishing_service import PublishingService

REDIRECT = "https://smo.example.com/oauth/x/callback"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake png body"


@pytest.fixture
def x_env(monkeypatch, tmp_db):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    monkeypatch.setenv("X_ENABLED", "true")
    monkeypatch.setenv("X_CLIENT_ID", "x-client")
    monkeypatch.setenv("X_CLIENT_SECRET", "x-secret")
    monkeypatch.setenv("X_REDIRECT_URI", REDIRECT)
    return tmp_db


def live_layer(monkeypatch):
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


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = generate_pkce()

    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in verifier


def test_authorization_url_requests_posting_scopes(x_env):
    url = XConnector("x-client", None, REDIRECT).get_authorization_url("state-1", "challenge-1")
    query = parse_qs(urlparse(url).query)

    assert urlparse(url).netloc == "x.com"
    assert query["scope"] == ["tweet.read tweet.write users.read media.write offline.access"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [REDIRECT]


def test_x_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("X_ENABLED", raising=False)

    assert x_enabled() is False


class FakeTokenClient:
    calls = []
    body = {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 7200, "scope": "tweet.write"}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, data=None, auth=None, headers=None):
        FakeTokenClient.calls.append({"url": url, "data": data, "auth": auth})
        return httpx.Response(200, json=FakeTokenClient.body, request=httpx.Request("POST", url))


@pytest.mark.asyncio
async def test_confidential_client_uses_basic_auth(monkeypatch):
    FakeTokenClient.calls = []
    monkeypatch.setattr(x_module.httpx, "AsyncClient", FakeTokenClient)

    token = await XConnector("x-client", "x-secret", REDIRECT).exchange_code("code-1", "verifier-1")

    call = FakeTokenClient.calls[0]
    assert call["auth"] == ("x-client", "x-secret")
    assert "client_id" not in call["data"]
    assert call["data"]["code_verifier"] == "verifier-1"
    assert token.access_token == "at-1" and token.refresh_token == "rt-1"


@pytest.mark.asyncio
async def test_public_client_sends_client_id_in_body(monkeypatch):
    FakeTokenClient.calls = []
    monkeypatch.setattr(x_module.httpx, "AsyncClient", FakeTokenClient)

    await XConnector("x-client", None, REDIRECT).refresh("rt-old")

    call = FakeTokenClient.calls[0]
    assert call["auth"] is None
    assert call["data"]["client_id"] == "x-client"
    assert call["data"]["grant_type"] == "refresh_token"


def test_oauth_state_is_single_use(x_env):
    connector = XConnector("x-client", "x-secret", REDIRECT)
    url = start_authorization(connector)
    state = parse_qs(urlparse(url).query)["state"][0]

    verifier = consume_authorization_state(state)

    assert verifier
    with pytest.raises(XAuthError):
        consume_authorization_state(state)


def test_expired_oauth_state_is_refused(x_env):
    url = start_authorization(XConnector("x-client", "x-secret", REDIRECT))
    state = parse_qs(urlparse(url).query)["state"][0]
    store = SQLiteStore(x_env)
    with store.get_session() as session:
        session.get(OAuthPending, state).created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        session.commit()

    with pytest.raises(XAuthError, match="expired"):
        consume_authorization_state(state)


def test_tokens_are_encrypted_at_rest(x_env):
    service = XTokenService(SQLiteStore(x_env))
    service.save("123", XToken("secret-access-token", "secret-refresh-token", int(time.time()) + 3600))

    assert service.load("123").access_token == "secret-access-token"
    with SQLiteStore(x_env).get_session() as session:
        from smo.memory.sqlite_store import PlatformToken

        raw = session.get(PlatformToken, ("x", "123")).encrypted_token
    assert "secret-access-token" not in raw
    assert "secret-refresh-token" not in raw


@pytest.mark.asyncio
async def test_fresh_token_is_not_refreshed(x_env):
    service = XTokenService(SQLiteStore(x_env))
    service.save("9", XToken("still-valid", "rt", int(time.time()) + 3600))
    connector = XConnector("x-client", "x-secret", REDIRECT)

    async def must_not_refresh(_):
        raise AssertionError("refresh should not be called")

    connector.refresh = must_not_refresh

    assert (await service.ensure_fresh(connector, "9")).access_token == "still-valid"


@pytest.mark.asyncio
async def test_expired_token_refresh_saves_rotated_pair(x_env):
    service = XTokenService(SQLiteStore(x_env))
    service.save("9", XToken("old", "rt-old", int(time.time()) - 10))
    connector = XConnector("x-client", "x-secret", REDIRECT)

    async def rotate(refresh_token):
        assert refresh_token == "rt-old"
        return XToken("new", "rt-new", int(time.time()) + 7200)

    connector.refresh = rotate

    await service.ensure_fresh(connector, "9")

    stored = service.load("9")
    assert stored.access_token == "new"
    assert stored.refresh_token == "rt-new"


@pytest.mark.asyncio
async def test_revoked_grant_requires_reconnect(x_env):
    service = XTokenService(SQLiteStore(x_env))
    service.save("9", XToken("old", "rt-revoked", int(time.time()) - 10))
    connector = XConnector("x-client", "x-secret", REDIRECT)

    async def revoked(_):
        request = httpx.Request("POST", x_module.X_TOKEN_URL)
        raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(400, request=request))

    connector.refresh = revoked

    with pytest.raises(XAuthError, match="reconnect"):
        await service.ensure_fresh(connector, "9")


@pytest.mark.asyncio
async def test_publish_post_sends_text_with_bearer(monkeypatch):
    seen = {}

    class TweetClient(FakeTokenClient):
        async def post(self, url, json=None, headers=None, data=None, auth=None):
            seen.update(url=url, json=json, headers=headers)
            return httpx.Response(201, json={"data": {"id": "1900"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(x_module.httpx, "AsyncClient", TweetClient)

    result = await XConnector("x-client", None, REDIRECT).publish_post("at-1", "Hello X")

    assert result == {"id": "1900"}
    assert seen["url"] == x_module.X_TWEETS_URL
    assert seen["json"] == {"text": "Hello X"}
    assert seen["headers"]["Authorization"] == "Bearer at-1"


@pytest.mark.asyncio
async def test_upload_media_sends_multipart_and_returns_id(monkeypatch):
    seen = {}
    png = b"\x89PNG\r\n\x1a\n" + b"body"

    class MediaClient(FakeTokenClient):
        async def post(self, url, headers=None, files=None, data=None, json=None, auth=None):
            seen.update(url=url, headers=headers, files=files, data=data)
            return httpx.Response(
                200, json={"data": {"id": "77", "media_key": "3_77"}}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(x_module.httpx, "AsyncClient", MediaClient)

    media_id = await XConnector("x-client", None, REDIRECT).upload_media("at-1", png, "image/png")

    assert media_id == "77"
    assert seen["url"] == x_module.X_MEDIA_UPLOAD_URL
    assert seen["headers"]["Authorization"] == "Bearer at-1"
    assert seen["files"]["media"] == ("image", png, "image/png")
    assert seen["data"] == {"media_category": "tweet_image"}


@pytest.mark.asyncio
async def test_upload_media_refused_forbidden_scope(monkeypatch):
    class Forbidden(FakeTokenClient):
        async def post(self, url, headers=None, files=None, data=None, json=None, auth=None):
            return httpx.Response(403, json={"detail": "Forbidden"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(x_module.httpx, "AsyncClient", Forbidden)

    with pytest.raises(XAuthError, match="media.write"):
        await XConnector("x-client", None, REDIRECT).upload_media(
            "at-1", b"\x89PNG\r\n\x1a\nbody", "image/png"
        )


@pytest.mark.asyncio
async def test_upload_media_refuses_images_over_x_limit():
    connector = XConnector("x-client", None, REDIRECT)
    oversized = b"\x89PNG\r\n\x1a\n" + b"x" * x_module.X_MAX_IMAGE_BYTES

    with pytest.raises(ValueError, match="up to"):
        await connector.upload_media("at-1", oversized, "image/png")


@pytest.mark.asyncio
async def test_publish_post_references_uploaded_media(monkeypatch):
    seen = {}

    class TweetClient(FakeTokenClient):
        async def post(self, url, json=None, headers=None, data=None, auth=None, files=None):
            seen.update(url=url, json=json)
            return httpx.Response(201, json={"data": {"id": "1901"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(x_module.httpx, "AsyncClient", TweetClient)

    await XConnector("x-client", None, REDIRECT).publish_post("at-1", "Hello X", media_ids=["77"])

    assert seen["json"] == {"text": "Hello X", "media": {"media_ids": ["77"]}}


@pytest.mark.asyncio
async def test_rate_limit_is_retryable(monkeypatch):
    from smo.orchestrator.retry import is_retryable

    class Limited(FakeTokenClient):
        async def post(self, url, json=None, headers=None, data=None, auth=None):
            return httpx.Response(429, json={"title": "Too Many Requests"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(x_module.httpx, "AsyncClient", Limited)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await XConnector("x-client", None, REDIRECT).publish_post("at-1", "Hi")

    assert is_retryable(caught.value) is True


def _x_item(tmp_db, link):
    content = ContentService(SQLiteStore(tmp_db))
    record = content.ingest_item(
        SourceItem(title="Post title", summary="A short update for followers", link=link, source="rss:x"),
        platform="x",
    ).record
    content.approve(record.id)
    return content, record


@pytest.mark.asyncio
async def test_publishing_to_x_is_refused_when_disabled(x_env, tmp_path, monkeypatch):
    monkeypatch.setenv("X_ENABLED", "false")
    content, record = _x_item(x_env, "https://example.com/x-off")

    with pytest.raises(ContentError, match="disabled"):
        await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(record.id, "123")


@pytest.mark.asyncio
async def test_dry_run_writes_x_payload(x_env, tmp_path, monkeypatch):
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
    content, record = _x_item(x_env, "https://example.com/x-dry")

    result = await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(record.id, "123")

    assert result.status == "dry_run"
    assert result.payload["text"]


@pytest.mark.asyncio
async def test_live_publish_marks_content_with_tweet_id(x_env, tmp_path, monkeypatch):
    live_layer(monkeypatch)
    service = XTokenService(SQLiteStore(x_env))
    service.save("123", XToken("at-live", "rt", int(time.time()) + 3600))

    async def fake_publish(self, access_token, text, media_ids=None):
        assert access_token == "at-live"
        assert media_ids is None
        return {"id": "555"}

    monkeypatch.setattr(XConnector, "publish_post", fake_publish)
    content, record = _x_item(x_env, "https://example.com/x-live")

    result = await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(record.id, "123")

    assert result.status == "published"
    assert content.get(record.id).status == "published"
    assert content.list_published()[0] == (record.id, "x", "555")


@pytest.mark.asyncio
async def test_live_publish_uploads_the_attached_image(x_env, tmp_path, monkeypatch):
    live_layer(monkeypatch)
    XTokenService(SQLiteStore(x_env)).save("123", XToken("at-live", "rt", int(time.time()) + 3600))
    content, record = _x_item(x_env, "https://example.com/x-image")
    MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in").attach(
        record.id, data_base64=base64.b64encode(PNG).decode()
    )
    calls = {}

    async def fake_upload(self, access_token, data, mime_type):
        calls["upload"] = (access_token, data, mime_type)
        return "media-77"

    async def fake_publish(self, access_token, text, media_ids=None):
        calls["publish"] = (access_token, text, media_ids)
        return {"id": "556"}

    monkeypatch.setattr(XConnector, "upload_media", fake_upload)
    monkeypatch.setattr(XConnector, "publish_post", fake_publish)

    result = await PublishingService(
        content_service=content, dryrun_dir=tmp_path / "dry", media_dir=tmp_path / "media"
    ).publish(record.id, "123")

    assert result.status == "published"
    assert calls["upload"] == ("at-live", PNG, "image/png")
    assert calls["publish"][2] == ["media-77"]
    assert content.list_published()[0] == (record.id, "x", "556")


@pytest.mark.asyncio
async def test_x_dry_run_reports_media_without_uploading(x_env, tmp_path, monkeypatch):
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
    content, record = _x_item(x_env, "https://example.com/x-dry-image")
    MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in").attach(
        record.id, data_base64=base64.b64encode(PNG).decode()
    )

    async def must_not_upload(*args, **kwargs):
        raise AssertionError("dry-run must not upload media")

    monkeypatch.setattr(XConnector, "upload_media", must_not_upload)

    result = await PublishingService(
        content_service=content, dryrun_dir=tmp_path / "dry", media_dir=tmp_path / "media"
    ).publish(record.id, "123")

    assert result.payload["media"] == {"media_ids": ["DRY_RUN"]}
    assert result.payload["media_filename"].endswith(".png")


@pytest.mark.asyncio
async def test_x_refuses_images_over_its_five_megabyte_limit(x_env, tmp_path, monkeypatch):
    live_layer(monkeypatch)
    XTokenService(SQLiteStore(x_env)).save("123", XToken("at-live", "rt", int(time.time()) + 3600))
    content, record = _x_item(x_env, "https://example.com/x-big-image")
    oversized = PNG + b"x" * (5 * 1024 * 1024)
    MediaService(content, output_dir=tmp_path / "media", input_dir=tmp_path / "in").attach(
        record.id, data_base64=base64.b64encode(oversized).decode()
    )

    with pytest.raises(ContentError, match="up to"):
        await PublishingService(
            content_service=content, dryrun_dir=tmp_path / "dry", media_dir=tmp_path / "media"
        ).publish(record.id, "123")


@pytest.mark.asyncio
async def test_missing_authorization_keeps_content_and_marks_failed(x_env, tmp_path, monkeypatch):
    live_layer(monkeypatch)
    content, record = _x_item(x_env, "https://example.com/x-noauth")

    with pytest.raises(ContentError, match="no X account"):
        await PublishingService(content_service=content, dryrun_dir=tmp_path).publish(record.id, "404")

    assert content.get(record.id).status == "failed"


def test_x_routes_are_not_caught_by_generic_platform_route(x_env, monkeypatch):
    monkeypatch.setenv("X_ENABLED", "false")

    with TestClient(create_app()) as client:
        response = client.get("/oauth/x", follow_redirects=False)

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]

@pytest.mark.asyncio
async def test_concurrent_processes_spend_the_refresh_token_once(x_env, monkeypatch):
    import asyncio

    monkeypatch.setattr(XTokenService, "LEASE_POLL_SECONDS", 0.02)
    XTokenService(SQLiteStore(x_env)).save("42", XToken("old", "rt-1", int(time.time()) - 10))
    calls = []
    connector = XConnector("x-client", "x-secret", REDIRECT)

    async def rotate(refresh_token):
        calls.append(refresh_token)
        await asyncio.sleep(0.2)
        return XToken("new", "rt-2", int(time.time()) + 7200)

    connector.refresh = rotate
    process_a = XTokenService(SQLiteStore(x_env))
    process_b = XTokenService(SQLiteStore(x_env))

    first, second = await asyncio.gather(
        process_a.ensure_fresh(connector, "42"),
        process_b.ensure_fresh(connector, "42"),
    )

    assert calls == ["rt-1"]
    assert first.access_token == second.access_token == "new"
    assert XTokenService(SQLiteStore(x_env)).load("42").refresh_token == "rt-2"


@pytest.mark.asyncio
async def test_waiting_process_uses_token_renewed_by_the_lease_holder(x_env, monkeypatch):
    import asyncio

    monkeypatch.setattr(XTokenService, "LEASE_POLL_SECONDS", 0.02)
    holder = XTokenService(SQLiteStore(x_env))
    holder.save("43", XToken("old", "rt-1", int(time.time()) - 10))
    _, version, _ = holder._snapshot("43")
    assert holder._claim_lease("43", version)

    async def finish_refresh_later():
        await asyncio.sleep(0.1)
        holder._commit_refresh("43", XToken("renewed", "rt-2", int(time.time()) + 7200), version)

    async def must_not_refresh(_):
        raise AssertionError("the waiting process must not spend the refresh token")

    connector = XConnector("x-client", "x-secret", REDIRECT)
    connector.refresh = must_not_refresh

    token, _ = await asyncio.gather(
        XTokenService(SQLiteStore(x_env)).ensure_fresh(connector, "43"),
        finish_refresh_later(),
    )

    assert token.access_token == "renewed"


@pytest.mark.asyncio
async def test_failed_refresh_releases_the_lease(x_env):
    service = XTokenService(SQLiteStore(x_env))
    service.save("44", XToken("old", "rt-1", int(time.time()) - 10))
    connector = XConnector("x-client", "x-secret", REDIRECT)

    async def unavailable(_):
        request = httpx.Request("POST", x_module.X_TOKEN_URL)
        raise httpx.HTTPStatusError("down", request=request, response=httpx.Response(503, request=request))

    connector.refresh = unavailable
    with pytest.raises(httpx.HTTPStatusError):
        await service.ensure_fresh(connector, "44")

    _, _, lease = service._snapshot("44")
    assert lease is None
