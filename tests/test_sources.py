"""Source provider tests (FR-06, REC-005/006)."""
import pytest

from smo.content.sources import (
    LocalFileSourceProvider,
    SourceProvider,
    fetch_all,
    parse_feed_content,
)

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test feed</title>
    <item>
      <title>First post</title>
      <description>Hello &amp; welcome</description>
      <link>https://example.com/1</link>
      <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second post</title>
      <description>More news</description>
      <link>https://example.com/2</link>
    </item>
  </channel>
</rss>
"""

ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom feed</title>
  <entry>
    <title>Atom entry</title>
    <summary>Atom summary</summary>
    <link href="https://example.com/atom"/>
    <updated>2024-01-01T10:00:00Z</updated>
  </entry>
</feed>
"""


def test_parse_rss_feed():
    items = parse_feed_content(RSS_XML, source="rss:test")

    assert len(items) == 2
    assert items[0].title == "First post"
    assert items[0].summary == "Hello & welcome"
    assert items[0].link == "https://example.com/1"
    assert items[0].source == "rss:test"


def test_parse_atom_feed():
    items = parse_feed_content(ATOM_XML)

    assert len(items) == 1
    assert items[0].title == "Atom entry"
    assert items[0].link == "https://example.com/atom"


def test_parse_feed_respects_limit():
    assert len(parse_feed_content(RSS_XML, limit=1)) == 1


@pytest.mark.asyncio
async def test_local_file_provider(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "post.md").write_text("# Hello\n\nFirst paragraph.\nSecond line.", encoding="utf-8")
    provider = LocalFileSourceProvider("post.md", base_dir=input_dir)

    items = await provider.fetch()

    assert len(items) == 1
    assert items[0].title == "# Hello"
    assert "First paragraph." in items[0].summary
    assert items[0].source == "file:post.md"


@pytest.mark.asyncio
async def test_local_file_provider_rejects_traversal(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    provider = LocalFileSourceProvider("../secret.txt", base_dir=input_dir)

    with pytest.raises(ValueError, match="outside base directory"):
        await provider.fetch()


@pytest.mark.asyncio
async def test_local_file_provider_rejects_extension(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "script.exe").write_bytes(b"MZ")
    provider = LocalFileSourceProvider("script.exe", base_dir=input_dir)

    with pytest.raises(ValueError, match="extension"):
        await provider.fetch()


@pytest.mark.asyncio
async def test_fetch_all_tolerates_failures():
    class OkProvider(SourceProvider):
        name = "ok"

        async def fetch(self):
            from smo.content.models import SourceItem

            return [SourceItem(title="ok", source="ok")]

    class FailingProvider(SourceProvider):
        name = "broken"

        async def fetch(self):
            raise RuntimeError("boom")

    items = await fetch_all([FailingProvider(), OkProvider()])

    assert [item.title for item in items] == ["ok"]
