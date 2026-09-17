"""LinkedIn OAuth 2.0 + PKCE connector (MVP)."""
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from ..config.security import security_layer
from ..config.settings import load_master_key
from ..memory.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


# --- Constants ---
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_SCOPES = "r_liteprofile r_emailaddress w_member_social"


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
            response = await client.get(f"{LINKEDIN_API_BASE}/me", headers=headers)
            response.raise_for_status()
            return response.json()

    async def publish_post(self, token: LinkedInToken, text: str, visibility: str = "PUBLIC") -> dict:
        """Publish a text post to LinkedIn (UGC Post API)."""
        # First get the author URN
        profile = await self.get_profile(token)
        author_urn = f"urn:li:person:{profile['id']}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"{token.token_type} {token.access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            }
            payload = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
            }
            response = await client.post(
                f"{LINKEDIN_API_BASE}/ugcPosts", headers=headers, json=payload
            )
            response.raise_for_status()
            return response.json()

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
                {"account_id": account_id, "encrypted_token": encrypted, "updated_at": datetime.utcnow().isoformat()},
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
    redirect_uri = os.getenv("LINKEDIN_REDIRECT_URI", "http://172.24.131.28:8023/oauth/linkedin/callback")
    if not client_id or not client_secret:
        return None
    return LinkedInConnector(client_id, client_secret, redirect_uri)