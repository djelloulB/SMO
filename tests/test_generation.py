"""Draft generation (FR-07) and its MCP tool (FR-14)."""
import pytest

from smo.config.settings import settings
from smo.content.generator import GenerationError, build_prompt, get_generator
from smo.mcp_server.auth import Identity
from smo.mcp_server.server import build_server
from smo.memory.sqlite_store import SQLiteStore
from smo.services.content_service import ContentService
from smo.services.generation_service import generate_drafts


class FakeGenerator:
    def __init__(self, text="A short update about open source.", error=None):
        self.text = text
        self.error = error
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.text


def make_content(tmp_db):
    return ContentService(SQLiteStore(tmp_db))


def call_tool(server, name, **kwargs):
    return server._tool_manager.get_tool(name).fn(**kwargs)


def test_prompt_carries_topic_limit_and_banned_topics(monkeypatch):
    monkeypatch.setattr(settings, "brand_voice_banned_topics", ["politics"])

    prompt = build_prompt("open source AI", "linkedin")

    assert "open source AI" in prompt
    assert "3000 characters" in prompt
    assert "politics" in prompt


def test_generated_text_is_stored_as_unapproved_draft(tmp_db):
    content = make_content(tmp_db)
    fake = FakeGenerator()

    results = generate_drafts("open source", ["LinkedIn"], content, fake)

    assert len(results) == 1
    assert results[0].record.status == "draft"
    assert results[0].record.variant_text == fake.text
    assert results[0].record.platform == "linkedin"
    assert content.get(results[0].record.id).status == "draft"


def test_generated_banned_content_is_rejected(tmp_db, monkeypatch):
    monkeypatch.setattr(settings, "brand_voice_banned_topics", ["politics"])
    content = make_content(tmp_db)

    results = generate_drafts("news", ["linkedin"], content, FakeGenerator(text="Hot take on politics"))

    assert results[0].record.status == "rejected"
    assert results[0].moderation.reasons


def test_each_generation_creates_a_new_item(tmp_db):
    content = make_content(tmp_db)
    first = generate_drafts("same topic", ["linkedin"], content, FakeGenerator())[0]
    second = generate_drafts("same topic", ["linkedin"], content, FakeGenerator())[0]

    assert first.record.id != second.record.id


@pytest.mark.parametrize(
    ("topic", "platforms", "message"),
    [
        ("   ", ["linkedin"], "topic must not be empty"),
        ("ok", [], "at least one platform"),
        ("ok", ["a", "b", "c", "d", "e", "f"], "at most"),
    ],
)
def test_invalid_requests_are_rejected(tmp_db, topic, platforms, message):
    with pytest.raises(ValueError, match=message):
        generate_drafts(topic, platforms, make_content(tmp_db), FakeGenerator())


def test_empty_generation_is_an_error(tmp_db):
    with pytest.raises(GenerationError):
        generate_drafts("ok", ["linkedin"], make_content(tmp_db), FakeGenerator(text="   "))


def test_unwired_provider_fails_clearly(monkeypatch):
    monkeypatch.setattr(settings, "llm_default", "openai/gpt-4o")

    with pytest.raises(GenerationError, match="not wired"):
        get_generator()


def test_generate_content_tool_is_exposed_to_content_scope(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    fake = FakeGenerator()
    server = build_server(Identity("agent:hermes", frozenset({"read", "content"})), generator=fake)

    result = call_tool(server, "generate_content", topic="open source", platforms=["linkedin"])

    assert result["drafts"][0]["status"] == "draft"
    assert result["drafts"][0]["text"] == fake.text


def test_generate_content_tool_is_hidden_from_read_scope(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    server = build_server(Identity("agent:reader", frozenset({"read"})))

    assert "generate_content" not in {t.name for t in server._tool_manager.list_tools()}
