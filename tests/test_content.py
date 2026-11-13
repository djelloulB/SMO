"""Moderation + content pipeline tests (FR-07, FR-09)."""
from smo.content.models import SourceItem
from smo.content.moderation import moderate
from smo.content.pipeline import build_content_piece, render_text


def test_moderation_allows_clean_content():
    result = moderate("Shipping a small update today.", banned_topics=())

    assert result.allowed is True
    assert result.reasons == ()


def test_moderation_blocks_empty_content():
    result = moderate("   ", banned_topics=())

    assert result.allowed is False
    assert any("empty" in reason for reason in result.reasons)


def test_moderation_blocks_banned_topics():
    result = moderate("A post about politics and elections", banned_topics=["politics"])

    assert result.allowed is False
    assert any("banned topic" in reason for reason in result.reasons)


def test_moderation_detects_email_and_phone():
    email = moderate("Contact me at jane.doe@example.com", banned_topics=())
    phone = moderate("Call +33 6 12 34 56 78", banned_topics=())

    assert email.allowed is False
    assert phone.allowed is False


def test_moderation_respects_platform_length():
    result = moderate("x" * 300, platform="x", banned_topics=())

    assert result.allowed is False
    assert any("limit" in reason for reason in result.reasons)


def test_render_text_contains_title_summary_and_link():
    item = SourceItem(title="Title", summary="Summary", link="https://example.com/post")

    text = render_text(item)

    assert "Title" in text
    assert "Summary" in text
    assert "https://example.com/post" in text


def test_render_text_trims_to_platform_limit_and_keeps_link():
    item = SourceItem(
        title="A very long title",
        summary="lorem ipsum " * 500,
        link="https://example.com/post",
    )

    text = render_text(item, platform="x")

    assert len(text) <= 280
    assert "https://example.com/post" in text


def test_build_content_piece_creates_one_variant():
    item = SourceItem(title="Title", summary="Summary", source="rss:test")

    piece = build_content_piece(item)

    assert piece.source == "rss:test"
    assert piece.status == "draft"
    assert len(piece.variants) == 1
    assert piece.variants[0].platform == "linkedin"
