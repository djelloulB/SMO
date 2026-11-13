"""Draft text generation behind an interface (FR-07).

Only Ollama (local) is wired. Cloud providers are configured in settings but
have no client yet, so they fail with a clear error instead of guessing.
"""
import os
from typing import Optional, Protocol

from ..config.settings import settings
from .moderation import PLATFORM_MAX_LENGTH

DEFAULT_MAX_LENGTH = 1000


class GenerationError(Exception):
    """The text generator is unavailable or returned nothing usable."""


class TextGenerator(Protocol):
    def generate(self, prompt: str) -> str: ...


def build_prompt(
    topic: str,
    platform: str,
    tone: Optional[str] = None,
    banned_topics: Optional[list[str]] = None,
) -> str:
    limit = PLATFORM_MAX_LENGTH.get(platform, DEFAULT_MAX_LENGTH)
    lines = [
        f"Write one {platform} post about: {topic.strip()}",
        f"Tone: {tone or settings.brand_voice_tone or 'professional, concise'}",
        f"Stay under {limit} characters.",
        "Return only the post text, without a preamble.",
    ]
    banned = banned_topics if banned_topics is not None else settings.brand_voice_banned_topics
    if banned:
        lines.append("Do not mention these topics: " + ", ".join(banned))
    return "\n".join(lines)


class OllamaGenerator:
    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None):
        _, _, default_model = settings.llm_default.partition("/")
        self.model = model or default_model or "llama3.1"
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    def generate(self, prompt: str) -> str:
        from langchain_ollama import ChatOllama

        try:
            message = ChatOllama(model=self.model, base_url=self.base_url).invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the agent
            raise GenerationError(f"Ollama unavailable at {self.base_url}: {type(exc).__name__}") from exc
        return str(message.content).strip()


def get_generator() -> TextGenerator:
    provider = settings.llm_default.split("/", 1)[0]
    if provider != "ollama":
        raise GenerationError(f"LLM provider '{provider}' is not wired yet; use the ollama provider")
    return OllamaGenerator()
