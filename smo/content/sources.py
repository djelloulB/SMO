"""Unified source providers (FR-06): RSS feeds and local files.

All outbound fetches go through the SSRF guard (REC-005); local files are
confined to ``data/inputs`` (REC-006).
"""
import html
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import feedparser

from ..utils.http import SSRFGuard
from ..utils.safeio import BASE_INPUT_DIR, get_safe_path
from .models import SourceItem

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str) -> str:
    return html.unescape(_TAG_RE.sub("", value or "")).strip()


def parse_feed_content(content: bytes, source: str = "rss", limit: int = 20) -> list[SourceItem]:
    """Parse RSS/Atom bytes into source items (no network)."""
    parsed = feedparser.parse(content)
    items: list[SourceItem] = []
    for entry in parsed.entries[:limit]:
        title = _clean_text(getattr(entry, "title", ""))
        summary = _clean_text(
            getattr(entry, "summary", "") or getattr(entry, "description", "")
        )
        if not title and not summary:
            continue
        items.append(
            SourceItem(
                title=title or summary[:80],
                summary=summary,
                link=getattr(entry, "link", None),
                source=source,
                published=getattr(entry, "published", None)
                or getattr(entry, "updated", None),
            )
        )
    return items


class SourceProvider(ABC):
    """A provider able to fetch content items."""

    name: str = "source"

    @abstractmethod
    async def fetch(self) -> list[SourceItem]:
        """Fetch and return content items."""


class RSSSourceProvider(SourceProvider):
    """Fetch and parse an RSS/Atom feed."""

    def __init__(self, url: str, limit: int = 20):
        self.url = url
        self.limit = limit
        self.name = f"rss:{url}"

    async def fetch(self) -> list[SourceItem]:
        logger.info("Fetching RSS source %s", self.url)
        async with SSRFGuard() as guard:
            response = await guard.get(self.url)
            response.raise_for_status()
            content = response.content
        return parse_feed_content(content, source=self.name, limit=self.limit)


class LocalFileSourceProvider(SourceProvider):
    """Read a local text/markdown file confined to the input directory."""

    def __init__(
        self,
        path: str,
        base_dir: Optional[Path] = None,
        limit: int = 1,
    ):
        self.path = path
        self.base_dir = Path(base_dir) if base_dir else BASE_INPUT_DIR
        self.limit = limit
        self.name = f"file:{path}"

    async def fetch(self) -> list[SourceItem]:
        safe_path = get_safe_path(
            self.base_dir, self.path, allowed_extensions=[".txt", ".md"]
        )
        text = safe_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines()]
        non_empty = [line for line in lines if line]
        if not non_empty:
            return []

        title = non_empty[0]
        summary = "\n".join(non_empty[1:]).strip()
        return [
            SourceItem(
                title=title[:200],
                summary=summary,
                link=None,
                source=f"file:{safe_path.name}",
            )
        ]


async def fetch_all(providers: list[SourceProvider]) -> list[SourceItem]:
    """Fetch items from all providers, tolerating individual failures."""
    items: list[SourceItem] = []
    for provider in providers:
        try:
            items.extend(await provider.fetch())
        except Exception as exc:  # noqa: BLE001 - one bad source must not block others
            logger.warning("Source %s failed: %s", provider.name, exc)
    return items
