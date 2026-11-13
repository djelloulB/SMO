"""WordPress connector: REST API with Application Passwords (FR-02).

Posts go to ``/wp-json/wp/v2/posts`` over HTTPS with HTTP Basic auth. Outbound
requests pass the SSRF guard (REC-005) and may only reach the configured host.
"""
import base64
import html
import os
from typing import Optional
from urllib.parse import urlparse

from ..utils.http import SSRFGuard

WP_POSTS_PATH = "/wp-json/wp/v2/posts"
WP_MEDIA_PATH = "/wp-json/wp/v2/media"
TITLE_MAX_CHARS = 120


class WordPressError(Exception):
    """The WordPress connector is not configured correctly."""


class WordPressConnector:
    def __init__(self, site_url: str, username: str, app_password: str):
        site = (site_url or "").strip().rstrip("/")
        parsed = urlparse(site)
        if parsed.scheme != "https" or not parsed.hostname:
            raise WordPressError("WordPress site must be an https:// URL")
        if not username or not app_password:
            raise WordPressError("WordPress credentials are not configured")
        self.site_url = site
        self.host = parsed.hostname
        # WordPress shows application passwords with spaces; the API expects them without.
        token = f"{username}:{app_password.replace(' ', '')}".encode("utf-8")
        self._auth = "Basic " + base64.b64encode(token).decode("ascii")

    @classmethod
    def from_environment(cls) -> Optional["WordPressConnector"]:
        site = os.getenv("ACCOUNTS_WORDPRESS_SITE", "").strip()
        username = os.getenv("WORDPRESS_APP_USERNAME", "").strip()
        password = os.getenv("WORDPRESS_APP_PASSWORD", "").strip()
        if not site or not username or not password:
            return None
        return cls(site, username, password)

    @staticmethod
    def build_post(text: str, status: str = "publish", featured_media: Optional[int] = None) -> dict:
        """Turn plain text into a WordPress post payload (escaped, one paragraph per blank line)."""
        body = text.strip()
        first_line = body.splitlines()[0].strip() if body else ""
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()] or [body]
        content = "".join(
            "<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs
        )
        payload = {
            "title": first_line[:TITLE_MAX_CHARS],
            "content": content,
            "status": status,
        }
        if featured_media is not None:
            payload["featured_media"] = featured_media
        return payload

    async def upload_media(self, filename: str, content: bytes, mime_type: str) -> dict:
        """Upload a file to the media library and return its attachment id."""
        async with SSRFGuard(allowlist={self.host}) as guard:
            response = await guard.post(
                f"{self.site_url}{WP_MEDIA_PATH}",
                content=content,
                headers={
                    "Authorization": self._auth,
                    "Content-Type": mime_type,
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
        response.raise_for_status()
        data = response.json()
        return {"id": str(data.get("id", "")), "source_url": data.get("source_url")}

    async def publish_post(
        self,
        text: str,
        status: str = "publish",
        featured_media: Optional[int] = None,
    ) -> dict:
        payload = self.build_post(text, status, featured_media)
        async with SSRFGuard(allowlist={self.host}) as guard:
            response = await guard.post(
                f"{self.site_url}{WP_POSTS_PATH}",
                json=payload,
                headers={"Authorization": self._auth},
            )
        # Raises httpx.HTTPStatusError, so the retry policy can classify 429 and 5xx.
        response.raise_for_status()
        data = response.json()
        return {"id": str(data.get("id", "")), "link": data.get("link")}
