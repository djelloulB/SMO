"""Deterministic moderation gate (FR-09).

Checks run before a draft can be approved/scheduled: banned topics, obvious
PII (emails/phone numbers) and per-platform length limits.
"""
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from ..config.settings import settings

PLATFORM_MAX_LENGTH = {
    "linkedin": 3000,
    "x": 280,
    "wordpress": 100000,
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)\+?\(?\d[\d\s().-]{7,}\d(?!\d)")


def _contains_phone_number(text: str) -> bool:
    for match in _PHONE_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 10 <= len(digits) <= 15:
            return True
    return False

# Default banned topics (overridable via BRAND_VOICE_BANNED_TOPICS).
DEFAULT_BANNED_TOPICS = ("politics", "religion")


def _default_banned_topics() -> tuple[str, ...]:
    configured = list(settings.brand_voice_banned_topics)
    if not configured and isinstance(settings.brand_voice, dict):
        configured = list(settings.brand_voice.get("banned_topics", []))
    return tuple(topic.strip().lower() for topic in configured if topic.strip())


@dataclass(frozen=True)
class ModerationResult:
    allowed: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:  # convenience: ``if result:`` means allowed
        return self.allowed


def moderate(
    text: str,
    platform: str = "linkedin",
    banned_topics: Optional[Iterable[str]] = None,
    max_length: Optional[int] = None,
) -> ModerationResult:
    """Run deterministic moderation rules on a draft."""
    reasons: list[str] = []
    stripped = (text or "").strip()

    if not stripped:
        reasons.append("content is empty")

    limit = max_length or PLATFORM_MAX_LENGTH.get(platform, 3000)
    if len(stripped) > limit:
        reasons.append(f"content exceeds {platform} limit ({limit} chars)")

    topics = tuple(t.lower() for t in banned_topics) if banned_topics is not None else _default_banned_topics()
    lowered = stripped.lower()
    for topic in topics:
        if topic and re.search(rf"\b{re.escape(topic)}\b", lowered):
            reasons.append(f"banned topic detected: {topic}")

    if _EMAIL_RE.search(stripped):
        reasons.append("possible email address (PII)")
    if _contains_phone_number(stripped):
        reasons.append("possible phone number (PII)")

    return ModerationResult(allowed=not reasons, reasons=tuple(reasons))
