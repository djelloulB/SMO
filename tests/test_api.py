"""Dashboard API + OAuth route tests."""
import pytest
from fastapi.testclient import TestClient

import smo.server.api as api_module
import smo.server.oauth as oauth_module
from smo.config.settings import settings
from smo.content.models import SourceItem
from smo.memory.sqlite_store import SQLiteStore
from smo.security.audit import AuditEntry, audit_logger
from smo.server.app import app
from smo.services.content_service import ContentService

client = TestClient(app)


def draft(tmp_db, text="Pending approval post"):
    service = ContentService(SQLiteStore(tmp_db))
    return service.ingest_item(
        SourceItem(title=text, summary="A summary", link=f"https://example.com/{text}", source="test")
    ).record


def audit_count(tool):
    with audit_logger.SessionLocal() as session:
        return session.query(AuditEntry).filter_by(actor_id="dashboard", tool=tool).count()


def test_healthz():
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dry_run"] is True
    assert body["human_gate"] is True


def test_status_reflects_immutable_controls():
    body = client.get("/api/v1/status").json()

    assert body["dry_run"] is True
    assert body["human_gate"] is True
    assert body["budget_per_day_usd"] > 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/posts",
        "/api/v1/metrics",
        "/api/v1/reports/summary",
        "/api/v1/recommendations",
    ],
)
def test_dashboard_endpoints_return_200(path):
    assert client.get(path).status_code == 200


def test_posts_lists_real_content_and_filters_status(tmp_db):
    record = draft(tmp_db)

    body = client.get("/api/v1/posts").json()
    assert [post["id"] for post in body["posts"]] == [record.id]
    assert body["posts"][0]["status"] == "draft"
    assert body["posts"][0]["title"] == "Pending approval post"

    assert client.get("/api/v1/posts", params={"status": "approved"}).json()["count"] == 0


def test_approve_is_disabled_without_a_configured_token(tmp_db, monkeypatch):
    record = draft(tmp_db)
    monkeypatch.setattr(settings, "smo_dashboard_token", None)

    response = client.post(f"/api/v1/posts/{record.id}/approve")

    assert response.status_code == 503
    assert ContentService(SQLiteStore(tmp_db)).get(record.id).status == "draft"


def test_approve_requires_the_right_token(tmp_db, monkeypatch):
    record = draft(tmp_db)
    monkeypatch.setattr(settings, "smo_dashboard_token", "s3cret")

    assert client.post(f"/api/v1/posts/{record.id}/approve").status_code == 401
    assert (
        client.post(
            f"/api/v1/posts/{record.id}/approve", headers={"X-Dashboard-Token": "wrong"}
        ).status_code
        == 401
    )
    assert ContentService(SQLiteStore(tmp_db)).get(record.id).status == "draft"


def test_approve_moves_draft_to_approved_and_audits(tmp_db, monkeypatch):
    record = draft(tmp_db)
    monkeypatch.setattr(settings, "smo_dashboard_token", "s3cret")
    before = audit_count("approve_draft")

    response = client.post(
        f"/api/v1/posts/{record.id}/approve", headers={"X-Dashboard-Token": "s3cret"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert ContentService(SQLiteStore(tmp_db)).get(record.id).status == "approved"
    assert audit_count("approve_draft") == before + 1


def test_approve_twice_is_a_conflict(tmp_db, monkeypatch):
    record = draft(tmp_db)
    monkeypatch.setattr(settings, "smo_dashboard_token", "s3cret")
    headers = {"X-Dashboard-Token": "s3cret"}

    assert client.post(f"/api/v1/posts/{record.id}/approve", headers=headers).status_code == 200
    assert client.post(f"/api/v1/posts/{record.id}/approve", headers=headers).status_code == 409


def test_reject_moves_draft_to_rejected(tmp_db, monkeypatch):
    record = draft(tmp_db)
    monkeypatch.setattr(settings, "smo_dashboard_token", "s3cret")
    before = audit_count("reject_draft")

    response = client.post(
        f"/api/v1/posts/{record.id}/reject",
        json={"reason": "off-brand"},
        headers={"X-Dashboard-Token": "s3cret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert audit_count("reject_draft") == before + 1


def test_approve_unknown_content_returns_404(tmp_db, monkeypatch):
    monkeypatch.setattr(settings, "smo_dashboard_token", "s3cret")

    response = client.post(
        "/api/v1/posts/999999/approve", headers={"X-Dashboard-Token": "s3cret"}
    )

    assert response.status_code == 404


def test_linkedin_callback_registers_the_account(tmp_db, monkeypatch):
    import time

    from smo.connectors.linkedin import LinkedInToken
    from smo.services.account_service import AccountService

    class FakeConnector:
        async def exchange_code_for_token(self, code, verifier):
            return LinkedInToken("access", None, int(time.time()) + 3600)

        async def get_profile(self, token):
            return {"id": "urn:li:person:abc", "sub": "urn:li:person:abc", "name": "Alice"}

        def save_token(self, account_id, token):
            pass

    monkeypatch.setattr(oauth_module, "get_linkedin_connector", lambda: FakeConnector())
    oauth_module._state_store["state-register"] = {"code_verifier": "verifier"}

    response = client.get(
        "/oauth/linkedin/callback", params={"code": "abc", "state": "state-register"}
    )

    assert response.status_code == 200
    accounts = AccountService(SQLiteStore(tmp_db)).list("linkedin")
    assert [account.account_id for account in accounts] == ["urn:li:person:abc"]
    assert accounts[0].label == "Alice"


def test_publish_returns_503_when_not_configured(monkeypatch):
    monkeypatch.setattr(api_module, "get_linkedin_connector", lambda: None)

    response = client.post(
        "/api/v1/linkedin/publish",
        json={"account_id": "abc", "text": "Hello"},
    )

    assert response.status_code == 503


def test_publish_returns_404_for_unknown_account(monkeypatch):
    class FakeConnector:
        def load_token(self, account_id):
            return None

    monkeypatch.setattr(api_module, "get_linkedin_connector", lambda: FakeConnector())

    response = client.post(
        "/api/v1/linkedin/publish",
        json={"account_id": "missing", "text": "Hello"},
    )

    assert response.status_code == 404


def test_oauth_status_when_not_configured(monkeypatch):
    monkeypatch.setattr(oauth_module, "get_linkedin_connector", lambda: None)

    body = client.get("/oauth/linkedin/status").json()

    assert body["configured"] is False


def test_oauth_start_rejects_unexpected_redirect_uri(monkeypatch):
    monkeypatch.setattr(oauth_module.settings, "public_base_url", "http://localhost:8023")

    response = client.get(
        "/oauth/linkedin",
        params={"state": "abc", "redirect_uri": "https://evil.example/callback"},
    )

    assert response.status_code == 400


def test_oauth_start_stores_state(monkeypatch):
    class FakeConnector:
        def generate_pkce(self):
            return "verifier", "challenge"

        def get_authorization_url(self, state, code_challenge):
            return f"https://www.linkedin.com/oauth/v2/authorization?state={state}"

    monkeypatch.setattr(oauth_module, "get_linkedin_connector", lambda: FakeConnector())
    monkeypatch.setattr(oauth_module.settings, "public_base_url", "http://localhost:8023")

    response = client.get(
        "/oauth/linkedin",
        params={
            "state": "state-csrf",
            "redirect_uri": "http://localhost:8023/oauth/linkedin/callback",
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "state-csrf"
    assert oauth_module._state_store["state-csrf"]["code_verifier"] == "verifier"


@pytest.mark.parametrize("params", [{}, {"code": "abc"}, {"code": "abc", "state": "unknown"}])
def test_oauth_callback_rejects_invalid_state(params):
    response = client.get("/oauth/linkedin/callback", params=params)

    assert response.status_code == 400
