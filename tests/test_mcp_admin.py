"""MCP admin-scope tools: set_config (REC-002) and rotate_tokens."""
import time

import pytest

import smo.mcp_server.server as server_module
from smo.connectors.linkedin import LinkedInToken
from smo.mcp_server.auth import Identity
from smo.mcp_server.server import build_server
from smo.memory.sqlite_store import SQLiteStore
from smo.services.config_service import ConfigService

ADMIN = Identity("agent:admin", frozenset({"read", "content", "control", "admin"}))


class FakeGenerator:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return "A post about open source."


@pytest.fixture
def server(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    return build_server(ADMIN, generator=FakeGenerator())


def tool(server, name):
    return server._tool_manager.get_tool(name).fn


@pytest.mark.parametrize("key", ["dry_run", "human_gate", "budget_per_day_usd", "mcp_scopes_admin"])
def test_immutable_controls_are_refused(server, tmp_db, key):
    with pytest.raises(ValueError, match="immutable"):
        tool(server, "set_config")(key=key, value=False)

    assert ConfigService(SQLiteStore(tmp_db)).get(key) is None


def test_wrong_value_type_is_refused(server, tmp_db):
    with pytest.raises(ValueError):
        tool(server, "set_config")(key="brand_voice", value="not a dict")

    assert ConfigService(SQLiteStore(tmp_db)).get("brand_voice") is None


def test_brand_voice_change_reaches_generated_prompt(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    generator = FakeGenerator()
    server = build_server(ADMIN, generator=generator)
    tool(server, "set_config")(key="brand_voice", value={"tone": "playful, short", "banned_topics": ["crypto"]})

    result = tool(server, "generate_content")(topic="open source", platforms=["linkedin"])

    assert result["drafts"][0]["status"] == "draft"
    assert "Tone: playful, short" in generator.prompts[0]
    assert "crypto" in generator.prompts[0]


def test_admin_tools_hidden_from_control_scope(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    names = {t.name for t in build_server(Identity("agent:ops", frozenset({"read", "control"})))._tool_manager.list_tools()}

    assert not {"set_config", "rotate_tokens"} & names


class FakeConnector:
    def __init__(self):
        self.saved = []

    def load_token(self, account_id):
        return LinkedInToken("old-access", "refresh-1", int(time.time()) - 10)

    async def refresh_access_token(self, refresh_token):
        assert refresh_token == "refresh-1"
        return LinkedInToken("new-access", "refresh-2", int(time.time()) + 3600)

    def save_token(self, account_id, token):
        self.saved.append((account_id, token.access_token))


@pytest.mark.asyncio
async def test_rotate_tokens_refreshes_and_saves(server, monkeypatch):
    connector = FakeConnector()
    monkeypatch.setattr(server_module, "get_linkedin_connector", lambda: connector)

    result = await tool(server, "rotate_tokens")(platform="linkedin", account_id="urn:li:person:abc")

    assert result["refreshed"] is True
    assert connector.saved == [("urn:li:person:abc", "new-access")]


@pytest.mark.asyncio
async def test_rotate_tokens_rejects_unknown_platform(server):
    with pytest.raises(ValueError, match="not implemented"):
        await tool(server, "rotate_tokens")(platform="tiktok", account_id="acc")


@pytest.mark.asyncio
async def test_rotate_tokens_without_linkedin_config(server, monkeypatch):
    monkeypatch.setattr(server_module, "get_linkedin_connector", lambda: None)

    with pytest.raises(ValueError, match="not configured"):
        await tool(server, "rotate_tokens")(platform="linkedin", account_id="acc")
