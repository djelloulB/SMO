"""Deterministic content pipeline: ingest item -> canonical draft -> variant."""
from .models import ContentPiece, PlatformVariant, SourceItem
from .moderation import PLATFORM_MAX_LENGTH


def render_text(item: SourceItem, platform: str = "linkedin") -> str:
    """Build a platform-ready text from a source item (deterministic, no LLM)."""
    title = item.title.strip()
    summary = item.summary.strip()
    link = (item.link or "").strip()
    limit = PLATFORM_MAX_LENGTH.get(platform, 3000)

    parts = [part for part in (title, summary, link) if part]
    text = "\n\n".join(parts)

    if len(text) <= limit:
        return text

    suffix = f"\n\n{link}" if link else ""
    budget = limit - len(title) - len(suffix) - len("\n\n...")
    if budget <= 0:
        return title[:limit]

    trimmed = summary[:budget].rstrip()
    return f"{title}\n\n{trimmed}...{suffix}"[:limit]


def build_content_piece(item: SourceItem, platform: str = "linkedin") -> ContentPiece:
    """Create a content piece with one platform variant from a source item."""
    text = render_text(item, platform)
    variant = PlatformVariant(platform=platform, text=text)
    return ContentPiece(
        source=item.source or None,
        canonical_draft=text,
        status="draft",
        variants=[variant],
    )
