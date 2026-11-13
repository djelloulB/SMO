"""LinkedIn OAuth 2.0 + PKCE connector (MVP)."""
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet

from ..config.security import security_layer
from ..config.settings import load_master_key, settings
from ..memory.sqlite_store import SQLiteStore
from ..utils.http import SSRFGuard

logger = logging.getLogger(__name__)


# --- Constants ---
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_USERINFO_URL = f"{LINKEDIN_API_BASE}/userinfo"
LINKEDIN_IMAGES_URL = f"{LINKEDIN_API_BASE}/rest/images"
LINKEDIN_SCOPES = (
    "openid profile email w_member_social w_organization_social r_organization_admin"
)
# Versioned APIs (rest/images) require a LinkedIn-Version header. LinkedIn keeps a
# version available for at least a year, so pin one and allow an env override.
DEFAULT_LINKEDIN_API_VERSION = "202601"
# Upload URLs come from an authenticated LinkedIn response; only LinkedIn hosts are accepted.
LINKEDIN_UPLOAD_HOST_SUFFIXES = (".licdn.com",)
LINKEDIN_UPLOAD_HOSTS = {"api.linkedin.com", "www.linkedin.com"}


@dataclass
class LinkedInToken:
    access_token: str
    refresh_token: Optional[str]
    expires_at: int  # Unix timestamp
    token_type: str = "Bearer"
    scope: str = LINKEDIN_SCOPES

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - buffer_seconds)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LinkedInToken":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data["expires_at"],
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope", LINKEDIN_SCOPES),
        )


class LinkedInConnector:
    """LinkedIn API connector with OAuth 2.0 PKCE support."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._fernet = self._get_fernet()

    def _get_fernet(self) -> Fernet:
        """Get Fernet cipher from master key."""
        master_key = load_master_key()
        # Derive a 32-byte key from master key
        key = hashlib.sha256(master_key).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    def _encrypt(self, data: str) -> str:
        return self._fernet.encrypt(data.encode()).decode()

    def _decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()

    # --- PKCE ---
    def generate_pkce(self) -> tuple[str, str]:
        """Generate code_verifier and code_challenge (S256)."""
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")
        return code_verifier, code_challenge

    # --- OAuth URLs ---
    def get_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": LINKEDIN_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"

    # --- Token exchange ---
    async def exchange_code_for_token(self, code: str, code_verifier: str) -> LinkedInToken:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": code_verifier,
            }
            response = await client.post(LINKEDIN_TOKEN_URL, data=data)
            response.raise_for_status()
            token_data = response.json()

        expires_at = int(time.time()) + token_data.get("expires_in", 5184000)  # 60 days default
        return LinkedInToken(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            token_type=token_data.get("token_type", "Bearer"),
            scope=token_data.get("scope", LINKEDIN_SCOPES),
        )

    async def refresh_access_token(self, refresh_token: str) -> LinkedInToken:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            response = await client.post(LINKEDIN_TOKEN_URL, data=data)
            response.raise_for_status()
            token_data = response.json()

        expires_at = int(time.time()) + token_data.get("expires_in", 5184000)
        return LinkedInToken(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", refresh_token),
            expires_at=expires_at,
            token_type=token_data.get("token_type", "Bearer"),
            scope=token_data.get("scope", LINKEDIN_SCOPES),
        )

    # --- API calls ---
    async def get_profile(self, token: LinkedInToken) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"{token.token_type} {token.access_token}"}
            response = await client.get(LINKEDIN_USERINFO_URL, headers=headers)
            response.raise_for_status()
            profile = response.json()
            if "id" not in profile and "sub" in profile:
                profile["id"] = profile["sub"]
            return profile

    async def get_administered_organizations(self, token: LinkedInToken) -> list[dict]:
        """List LinkedIn pages the authenticated member can publish to.

        Requires the ``r_organization_admin`` scope (Community Management API).
        Returns a list of ``{"urn": "urn:li:organization:...", "id": "..."}``.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {
                "Authorization": f"{token.token_type} {token.access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            }
            response = await client.get(
                f"{LINKEDIN_API_BASE}/organizationalEntityAcls",
                params={"q": "roleAssignee"},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        organizations: list[dict] = []
        for element in data.get("elements", []):
            urn = element.get("organizationalEntity")
            if not urn:
                continue
            organizations.append(
                {
                    "urn": urn,
                    "id": urn.rsplit(":", 1)[-1],
                    "role": element.get("role"),
                    "state": element.get("state"),
                }
            )
        return organizations

    @staticmethod
    def resolve_author_urn(
        author_urn: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> Optional[str]:
        """Build the author URN for person or organization posts."""
        if author_urn:
            return author_urn
        if organization_id:
            if organization_id.startswith("urn:li:"):
                return organization_id
            return f"urn:li:organization:{organization_id}"
        return None

    _resolve_author_urn = resolve_author_urn

    @property
    def api_version(self) -> str:
        return os.getenv("LINKEDIN_API_VERSION", "").strip() or DEFAULT_LINKEDIN_API_VERSION

    def _rest_headers(self, token: LinkedInToken, content_type: Optional[str] = "application/json") -> dict:
        headers = {
            "Authorization": f"{token.token_type} {token.access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": self.api_version,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def upload_image(self, token: LinkedInToken, author_urn: str, data: bytes) -> str:
        """Register an image and upload its bytes; returns the image URN for a post."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{LINKEDIN_IMAGES_URL}?action=initializeUpload",
                headers=self._rest_headers(token),
                json={"initializeUploadRequest": {"owner": author_urn}},
            )
            response.raise_for_status()
            value = (response.json() or {}).get("value") or {}

        upload_url = value.get("uploadUrl")
        image_urn = value.get("image")
        if not upload_url or not image_urn:
            raise ValueError("LinkedIn returned no upload URL or image URN")

        host = urlparse(upload_url).hostname or ""
        if host not in LINKEDIN_UPLOAD_HOSTS and not host.endswith(LINKEDIN_UPLOAD_HOST_SUFFIXES):
            raise ValueError(f"LinkedIn returned an unexpected upload host '{host}'")

        async with SSRFGuard(allowlist={host}) as guard:
            uploaded = await guard.put(
                upload_url,
                content=data,
                headers={
                    "Authorization": f"{token.token_type} {token.access_token}",
                    "Content-Type": "application/octet-stream",
                },
            )
        uploaded.raise_for_status()
        return image_urn

    @staticmethod
    def build_post_payload(
        author_urn: str,
        text: str,
        visibility: str = "PUBLIC",
        image_urn: Optional[str] = None,
    ) -> dict:
        """Build the exact LinkedIn UGC Post payload (used for publish/dry-run)."""
        if author_urn.startswith("urn:li:organization:"):
            visibility = "PUBLIC"
        share = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "NONE",
        }
        if image_urn:
            share["shareMediaCategory"] = "IMAGE"
            share["media"] = [{"status": "READY", "media": image_urn}]
        return {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
        }

    async def publish_post(
        self,
        token: LinkedInToken,
        text: str,
        visibility: str = "PUBLIC",
        organization_id: Optional[str] = None,
        author_urn: Optional[str] = None,
        image_data: Optional[bytes] = None,
    ) -> dict:
        """Publish a post to LinkedIn (UGC Post API), optionally with one image.

        Without ``organization_id``/``author_urn`` the post is published on the
        authenticated member's profile. With either of them it is published on
        behalf of that organization (the member must be a page admin and the
        token must include ``w_organization_social``).
        """
        resolved_author = self.resolve_author_urn(author_urn, organization_id)
        if not resolved_author:
            profile = await self.get_profile(token)
            resolved_author = f"urn:li:person:{profile['id']}"

        image_urn = None
        if image_data:
            image_urn = await self.upload_image(token, resolved_author, image_data)

        payload = self.build_post_payload(resolved_author, text, visibility, image_urn)

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"{token.token_type} {token.access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            }
            response = await client.post(
                f"{LINKEDIN_API_BASE}/ugcPosts", headers=headers, json=payload
            )
            response.raise_for_status()
            # LinkedIn usually answers 201 with an empty body and the id in a header.
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not body.get("id"):
                body["id"] = response.headers.get("x-restli-id")
            return body

    async def fetch_metrics(self, token: LinkedInToken, post_urn: str) -> dict:
        """Read engagement for one post (read-only, FR-13).

        Only likes are available through the social actions endpoint. Views and
        reach need organization analytics (``r_organization_social``), so they
        are reported as 0 until that access is granted.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {
                "Authorization": f"{token.token_type} {token.access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            }
            response = await client.get(
                f"{LINKEDIN_API_BASE}/socialActions/{quote(post_urn, safe='')}",
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        likes = int((data.get("likesSummary") or {}).get("totalLikes", 0))
        return {"likes": likes, "views": 0, "reach": 0}

    # --- Token persistence ---
    def save_token(self, account_id: str, token: LinkedInToken) -> None:
        """Save encrypted token to database."""
        db = SQLiteStore()
        with db.get_session() as session:
            from sqlalchemy import text
            encrypted = self._encrypt(json.dumps(token.to_dict()))
            session.execute(
                text("""
                    INSERT OR REPLACE INTO linkedin_tokens (account_id, encrypted_token, updated_at)
                    VALUES (:account_id, :encrypted_token, :updated_at)
                """),
                {"account_id": account_id, "encrypted_token": encrypted, "updated_at": datetime.now(timezone.utc).isoformat()},
            )
            session.commit()

    def load_token(self, account_id: str) -> Optional[LinkedInToken]:
        """Load and decrypt token from database."""
        db = SQLiteStore()
        with db.get_session() as session:
            from sqlalchemy import text
            row = session.execute(
                text("SELECT encrypted_token FROM linkedin_tokens WHERE account_id = :account_id"),
                {"account_id": account_id},
            ).fetchone()
            if not row:
                return None
            decrypted = self._decrypt(row[0])
            return LinkedInToken.from_dict(json.loads(decrypted))

    def delete_token(self, account_id: str) -> None:
        db = SQLiteStore()
        with db.get_session() as session:
            from sqlalchemy import text
            session.execute(text("DELETE FROM linkedin_tokens WHERE account_id = :account_id"), {"account_id": account_id})
            session.commit()


def get_linkedin_connector() -> Optional[LinkedInConnector]:
    """Factory to create connector from env config."""
    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET") or os.getenv("LINKEDIN_PRIMARY_CLIENT_SECRET")
    default_redirect = f"{settings.public_base_url.rstrip('/')}/oauth/linkedin/callback"
    redirect_uri = os.getenv("LINKEDIN_REDIRECT_URI") or default_redirect
    if not redirect_uri.endswith("/callback"):
        logger.warning(
            "LINKEDIN_REDIRECT_URI does not look like an OAuth callback, using %s",
            default_redirect,
        )
        redirect_uri = default_redirect
    if not client_id or not client_secret:
        return None
    return LinkedInConnector(client_id, client_secret, redirect_uri)