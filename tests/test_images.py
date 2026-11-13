"""Image generation (FR-08) with the daily budget cap (REC-002). No real OpenAI calls."""
import base64
from pathlib import Path

import httpx
import pytest

import smo.content.images.openai_images as openai_module
from smo.content.images import ImageError, get_image_provider
from smo.content.images.base import GeneratedImage
from smo.content.images.openai_images import OpenAIImageProvider
from smo.content.models import SourceItem
from smo.mcp_server.auth import Identity
from smo.mcp_server.server import build_server
from smo.memory.sqlite_store import SQLiteStore
from smo.services.budget_service import BudgetExceeded, BudgetService
from smo.services.content_service import ContentService
from smo.services.image_service import ImageService, build_image_prompt

PNG = b"\x89PNG fake image bytes"


class FakeProvider:
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail
        self.prompts = []

    def estimate_cost(self, size, quality):
        return {"low": 0.006, "medium": 0.053, "high": 0.211}[quality]

    async def generate(self, prompt, size, quality):
        self.prompts.append(prompt)
        if self.fail:
            raise ImageError("provider down")
        return GeneratedImage(data=PNG, extension="png")


def _draft(tmp_db, link="https://example.com/img"):
    store = SQLiteStore(tmp_db)
    content = ContentService(store)
    record = content.ingest_item(
        SourceItem(title="Open source", summary="A post about open source tooling", link=link, source="rss:img"),
    ).record
    return store, content, record


@pytest.mark.asyncio
async def test_image_is_stored_and_linked_to_content(tmp_db, tmp_path):
    store, content, record = _draft(tmp_db)
    service = ImageService(FakeProvider(), BudgetService(store, limit_usd=1.0), content, output_dir=tmp_path)

    receipt = await service.generate_for_content(record.id)

    assert Path(receipt.path).read_bytes() == PNG
    assert receipt.cost_usd == 0.006
    with store.get_session() as session:
        from smo.memory.sqlite_store import Variant

        assert session.query(Variant).filter_by(content_id=record.id).first().media_ref == receipt.path


@pytest.mark.asyncio
async def test_prompt_forbids_text_in_image(tmp_db, tmp_path):
    store, content, record = _draft(tmp_db, "https://example.com/img-prompt")
    provider = FakeProvider()

    await ImageService(provider, BudgetService(store, limit_usd=1.0), content, output_dir=tmp_path).generate_for_content(record.id)

    assert "No text" in provider.prompts[0]
    assert "open source" in provider.prompts[0].lower()


def test_prompt_is_bounded():
    prompt = build_image_prompt("word " * 1000)

    assert len(prompt) < 700


@pytest.mark.asyncio
async def test_budget_blocks_generation_before_any_call(tmp_db, tmp_path):
    store, content, record = _draft(tmp_db, "https://example.com/img-budget")
    provider = FakeProvider()
    service = ImageService(provider, BudgetService(store, limit_usd=0.10), content, output_dir=tmp_path)

    with pytest.raises(BudgetExceeded):
        await service.generate_for_content(record.id, quality="high")  # 0.211 > 0.10

    assert provider.prompts == []
    assert BudgetService(store, limit_usd=0.10).spent_today() == 0


@pytest.mark.asyncio
async def test_spend_accumulates_until_cap(tmp_db, tmp_path):
    store, content, record = _draft(tmp_db, "https://example.com/img-cap")
    budget = BudgetService(store, limit_usd=0.02)
    service = ImageService(FakeProvider(), budget, content, output_dir=tmp_path)

    await service.generate_for_content(record.id, quality="low")  # 0.006
    await service.generate_for_content(record.id, quality="low")  # 0.012
    await service.generate_for_content(record.id, quality="low")  # 0.018
    with pytest.raises(BudgetExceeded):
        await service.generate_for_content(record.id, quality="medium")  # would be 0.071

    assert round(budget.spent_today(), 3) == 0.018


@pytest.mark.asyncio
async def test_failed_generation_releases_reservation(tmp_db, tmp_path):
    store, content, record = _draft(tmp_db, "https://example.com/img-fail")
    budget = BudgetService(store, limit_usd=1.0)
    service = ImageService(FakeProvider(fail=True), budget, content, output_dir=tmp_path)

    with pytest.raises(ImageError):
        await service.generate_for_content(record.id)

    assert budget.spent_today() == 0


@pytest.mark.asyncio
async def test_unsupported_size_and_quality_are_refused(tmp_db, tmp_path):
    store, content, record = _draft(tmp_db, "https://example.com/img-args")
    service = ImageService(FakeProvider(), BudgetService(store, limit_usd=1.0), content, output_dir=tmp_path)

    with pytest.raises(ImageError, match="size"):
        await service.generate_for_content(record.id, size="1792x1024")
    with pytest.raises(ImageError, match="quality"):
        await service.generate_for_content(record.id, quality="hd")


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeOpenAIClient:
    seen = []
    response = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json=None, headers=None):
        FakeOpenAIClient.seen.append({"url": url, "json": json, "headers": headers})
        return FakeOpenAIClient.response


@pytest.mark.asyncio
async def test_openai_provider_decodes_b64_json(monkeypatch):
    FakeOpenAIClient.seen = []
    FakeOpenAIClient.response = FakeResponse(200, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    monkeypatch.setattr(openai_module.httpx, "AsyncClient", FakeOpenAIClient)

    image = await OpenAIImageProvider("sk-test").generate("a prompt", "1024x1024", "low")

    assert image.data == PNG
    call = FakeOpenAIClient.seen[0]
    assert call["url"].endswith("/images/generations")
    assert call["json"]["model"] == "gpt-image-2"
    assert "response_format" not in call["json"]  # GPT image models reject it
    assert call["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_openai_error_does_not_echo_provider_body(monkeypatch):
    FakeOpenAIClient.seen = []
    FakeOpenAIClient.response = FakeResponse(400, {"error": {"message": "secret echo sk-live-123"}})
    monkeypatch.setattr(openai_module.httpx, "AsyncClient", FakeOpenAIClient)

    with pytest.raises(ImageError) as caught:
        await OpenAIImageProvider("sk-test").generate("p", "1024x1024", "low")

    assert "HTTP 400" in str(caught.value)
    assert "sk-live" not in str(caught.value)


@pytest.mark.asyncio
async def test_openai_rejects_malformed_image_data(monkeypatch):
    FakeOpenAIClient.seen = []
    FakeOpenAIClient.response = FakeResponse(200, {"data": [{"b64_json": "%%%not base64"}]})
    monkeypatch.setattr(openai_module.httpx, "AsyncClient", FakeOpenAIClient)

    with pytest.raises(ImageError, match="usable image data"):
        await OpenAIImageProvider("sk-test").generate("p", "1024x1024", "low")


def test_provider_requires_api_key():
    with pytest.raises(ImageError, match="OPENAI_API_KEY"):
        OpenAIImageProvider("")


def test_retired_model_has_no_price(monkeypatch):
    provider = OpenAIImageProvider("sk-test", model="dall-e-3")

    with pytest.raises(ImageError, match="no price"):
        provider.estimate_cost("1024x1024", "low")


def test_only_openai_provider_is_wired(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "stability")

    with pytest.raises(ImageError, match="not wired"):
        get_image_provider()


def test_generate_image_tool_is_content_scope(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    names = {t.name for t in build_server(Identity("agent:hermes", frozenset({"read", "content"})))._tool_manager.list_tools()}
    read_only = {t.name for t in build_server(Identity("agent:reader", frozenset({"read"})))._tool_manager.list_tools()}

    assert "generate_image" in names
    assert "generate_image" not in read_only


def test_budget_is_reported_in_health(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    server = build_server(Identity("agent:reader", frozenset({"read"})))

    health = server._tool_manager.get_tool("get_health").fn()

    assert "remaining_today_usd" in health["budget"]
