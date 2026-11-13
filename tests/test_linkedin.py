"""LinkedIn connector tests (OAuth PKCE, token storage, publishing)."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest

import smo.connectors.linkedin as linkedin_module
from smo.connectors.linkedin import (
    LinkedInConnector,
    LinkedInToken,
    get_linkedin_connector,
)

REDIRECT_URI = "http://localhost:8023/oauth/linkedin/callback"


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    last_call = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        FakeAsyncClient.last_call = ("GET", url, kwargs)
        return FakeResponse(
            200,
            {
                "elements": [
                    {
                        "organizationalEntity": "urn:li:organization:123",
                        "role": "ADMINISTRATOR",
                        "state": "APPROVED",
                    }
                ]
            },
        )

    async def post(self, url, **kwargs):
        FakeAsyncClient.last_call = ("POST", url, kwargs)
        return FakeResponse(201, {"id": "urn:li:share:1"})


def make_connector():
    return LinkedInConnector("client-id", "client-secret", REDIRECT_URI)


def test_generate_pkce_challenge_matches_verifier():
    verifier, challenge = make_connector().generate_pkce()

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected
    assert "=" not in verifier


def test_authorization_url_contains_pkce_and_scopes():
    connector = make_connector()

    url = connector.get_authorization_url("state-123", "challenge-123")
    query = parse_qs(urlparse(url).query)

    assert query["state"] == ["state-123"]
    assert query["code_challenge"] == ["challenge-123"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    scopes = query["scope"][0]
    for scope in ("openid", "w_member_social", "w_organization_social", "r_organization_admin"):
        assert scope in scopes


@pytest.mark.parametrize(
    ("author_urn", "organization_id", "expected"),
    [
        ("urn:li:person:abc", None, "urn:li:person:abc"),
        (None, "123", "urn:li:organization:123"),
        (None, "urn:li:organization:456", "urn:li:organization:456"),
        (None, None, None),
    ],
)
def test_resolve_author_urn(author_urn, organization_id, expected):
    assert LinkedInConnector._resolve_author_urn(author_urn, organization_id) == expected


@pytest.mark.asyncio
async def test_publish_post_as_organization_forces_public(monkeypatch):
    monkeypatch.setattr(linkedin_module.httpx, "AsyncClient", FakeAsyncClient)
    token = LinkedInToken("access", None, int(time.time()) + 3600)

    result = await make_connector().publish_post(
        token, "Hello", visibility="CONNECTIONS", organization_id="123"
    )

    method, url, kwargs = FakeAsyncClient.last_call
    assert method == "POST"
    assert url.endswith("/ugcPosts")
    assert kwargs["json"]["author"] == "urn:li:organization:123"
    assert kwargs["json"]["visibility"] == {
        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
    }
    assert result == {"id": "urn:li:share:1"}


@pytest.mark.asyncio
async def test_publish_post_as_member_keeps_visibility(monkeypatch):
    monkeypatch.setattr(linkedin_module.httpx, "AsyncClient", FakeAsyncClient)
    token = LinkedInToken("access", None, int(time.time()) + 3600)

    await make_connector().publish_post(
        token, "Hello", visibility="CONNECTIONS", author_urn="urn:li:person:abc"
    )

    _, _, kwargs = FakeAsyncClient.last_call
    assert kwargs["json"]["author"] == "urn:li:person:abc"
    assert kwargs["json"]["visibility"] == {
        "com.linkedin.ugc.MemberNetworkVisibility": "CONNECTIONS"
    }


@pytest.mark.asyncio
async def test_get_administered_organizations(monkeypatch):
    monkeypatch.setattr(linkedin_module.httpx, "AsyncClient", FakeAsyncClient)
    token = LinkedInToken("access", None, int(time.time()) + 3600)

    organizations = await make_connector().get_administered_organizations(token)

    method, url, kwargs = FakeAsyncClient.last_call
    assert method == "GET"
    assert url.endswith("/organizationalEntityAcls")
    assert kwargs["params"] == {"q": "roleAssignee"}
    assert organizations == [
        {
            "urn": "urn:li:organization:123",
            "id": "123",
            "role": "ADMINISTRATOR",
            "state": "APPROVED",
        }
    ]


def test_token_roundtrip_encrypted(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    connector = make_connector()
    token = LinkedInToken("access-token", "refresh-token", int(time.time()) + 3600)

    connector.save_token("account-1", token)
    loaded = connector.load_token("account-1")

    assert loaded is not None
    assert loaded.access_token == "access-token"
    assert loaded.refresh_token == "refresh-token"

    connector.delete_token("account-1")
    assert connector.load_token("account-1") is None


def test_factory_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("LINKEDIN_PRIMARY_CLIENT_SECRET", raising=False)

    assert get_linkedin_connector() is None


def test_factory_falls_back_to_public_callback_url(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKEDIN_REDIRECT_URI", "https://www.linkedin.com/developers/tools/oauth/redirect")
    monkeypatch.setattr(linkedin_module.settings, "public_base_url", "http://localhost:8023")

    connector = get_linkedin_connector()

    assert connector is not None
    assert connector.redirect_uri == REDIRECT_URI
