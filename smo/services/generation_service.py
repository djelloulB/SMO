"""Use-case: generate platform drafts from a topic (FR-07, FR-09).

Generated text always goes through the moderation gate and stays a draft until
a human approves it (human_gate).
"""
from typing import Iterable, Optional

from ..content.generator import TextGenerator, GenerationError, build_prompt, get_generator
from .config_service import ConfigService
from .content_service import ContentService, IngestResult

MAX_PLATFORMS = 5


def generate_drafts(
    topic: str,
    platforms: Iterable[str],
    content: ContentService,
    generator: Optional[TextGenerator] = None,
) -> list[IngestResult]:
    if not topic or not topic.strip():
        raise ValueError("topic must not be empty")
    targets = list(dict.fromkeys(p.strip().lower() for p in platforms if p and p.strip()))
    if not targets:
        raise ValueError("at least one platform is required")
    if len(targets) > MAX_PLATFORMS:
        raise ValueError(f"at most {MAX_PLATFORMS} platforms per request")

    engine = generator or get_generator()
    voice = ConfigService(content.store).get("brand_voice", {}) or {}
    results: list[IngestResult] = []
    for platform in targets:
        prompt = build_prompt(topic, platform, tone=voice.get("tone"), banned_topics=voice.get("banned_topics"))
        text = engine.generate(prompt).strip()
        if not text:
            raise GenerationError(f"empty generation for {platform}")
        results.append(content.add_generated(text, platform))
    return results
