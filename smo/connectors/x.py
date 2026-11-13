"""X (Twitter) connector: OAuth 2.0 with PKCE and the v2 posts endpoint (FR-05).

Off by default: X's write quota on the free tier is small and paid tiers cost money,
so publishing requires ``X_ENABLED=true``.

Refresh tokens rotate: each refresh invalidates the previous one. Tokens are therefore
renewed under a database lease with a version check (compare-and-swap), so several
processes (web server, scheduler, MCP stdio) never spend the same refresh token twice.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from ..config.settings import load_master_key, settings
from ..memory.sqlite_store import OAuthPending, PlatformToken, SQLiteStore

logger = logging.getLogger(__name__)

X_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_TWEETS_URL = "https://api.x.com/2/tweets"
X_MEDIA_UPLOAD_URL = "https://api.x.com/2/media/upload"
X_ME_URL = "https://api.x.com/2/users/me"
X_SCOPES = "tweet.read tweet.write users.read media.write offline.access"
X_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # X's limit for tweet_image uploads
PLATFORM = "x"
REQUEST_TIMEOUT = 15.0
DEFAULT_EXPIRES_IN = 7200  # X access tokens last two hours
OAUTH_PENDING_TTL_SECONDS = 15 * 60


class XAuthError(Exception):
    """The stored X authorization is missing or no longer accepted: the user must reconnect."""


def x_enabled() -> bool:
    return os.getenv("X_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class XToken:
    access_token: str
    refresh_token: Optional[str]
    expires_at: int
    scope: str = X_SCOPES

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        return time.time() >= self.expires_at - buffer_seconds

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "XToken":
        return cls(**data)


class XConnector:
    def __init__(self, client_id: str, client_secret: Optional[str], redirect_uri: str):
        if not client_id:
            raise XAuthError("X_CLIENT_ID is not configured")
        self.client_id = client_id
        self.client_secret = client_secret or None
        self.redirect_uri = redirect_uri

    @classmethod
    def from_environment(cls) -> Optional["XConnector"]:
        client_id = os.getenv("X_CLIENT_ID", "").strip()
        if not client_id:
            return None
        redirect = os.getenv("X_REDIRECT_URI", "").strip() or (
            f"{settings.public_base_url.rstrip('/')}/oauth/x/callback"
        )
        return cls(client_id, os.getenv("X_CLIENT_SECRET", "").strip(), redirect)

    def get_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": X_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{X_AUTHORIZE_URL}?{urlencode(params)}"

    async def _token_request(self, data: dict) -> XToken:
        form = dict(data)
        auth = None
        if self.client_secret:
            auth = (self.client_id, self.client_secret)
        else:
            form["client_id"] = self.client_id
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                X_TOKEN_URL,
                data=form,
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        body = response.json()
        return XToken(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=int(time.time()) + int(body.get("expires_in", DEFAULT_EXPIRES_IN)),
            scope=body.get("scope", X_SCOPES),
        )

    async def exchange_code(self, code: str, code_verifier: str) -> XToken:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> XToken:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def get_me(self, access_token: str) -> dict:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(X_ME_URL, headers={"Authorization": f"Bearer {access_token}"})
        response.raise_for_status()
        return response.json().get("data", {})

    async def upload_media(self, access_token: str, data: bytes, mime_type: str) -> str:
        """Upload one image in one request and return its media id (needs ``media.write``)."""
        if len(data) > X_MAX_IMAGE_BYTES:
            raise ValueError(f"X accepts images up to {X_MAX_IMAGE_BYTES} bytes")
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                X_MEDIA_UPLOAD_URL,
                files={"media": ("image", data, mime_type)},
                data={"media_category": "tweet_image"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code in (401, 403):
            # Tokens stored before media.write was requested cannot upload.
            raise XAuthError(
                "X refused the media upload; reconnect the X account "
                "so the media.write scope is granted"
            )
        # Raises httpx.HTTPStatusError, so 429 and 5xx are retried by the scheduler.
        response.raise_for_status()
        media_id = (response.json().get("data") or {}).get("id")
        if not media_id:
            raise ValueError("X returned no media id")
        return str(media_id)

    async def publish_post(
        self, access_token: str, text: str, media_ids: Optional[list[str]] = None
    ) -> dict:
        payload: dict = {"text": text}
        if media_ids:
            payload["media"] = {"media_ids": media_ids}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                X_TWEETS_URL,
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        # Raises httpx.HTTPStatusError, so 429 and 5xx are retried by the scheduler.
        response.raise_for_status()
        return {"id": str(response.json().get("data", {}).get("id", ""))}


def start_authorization(connector: XConnector, store: Optional[SQLiteStore] = None) -> str:
    """Create a one-time state and PKCE verifier, store them, and return the authorization URL."""
    state = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce()
    store = store or SQLiteStore()
    with store.get_session() as session:
        session.add(
            OAuthPending(state=state, platform=PLATFORM, code_verifier=verifier, created_at=_utc_now())
        )
        session.commit()
    return connector.get_authorization_url(state, challenge)


def consume_authorization_state(state: str, store: Optional[SQLiteStore] = None) -> str:
    """Return the verifier for a valid, unexpired state and delete it so it cannot be replayed."""
    store = store or SQLiteStore()
    with store.get_session() as session:
        row = session.get(OAuthPending, state)
        if row is None or row.platform != PLATFORM:
            raise XAuthError("invalid or already used OAuth state")
        verifier = row.code_verifier
        created = row.created_at
        session.delete(row)
        session.commit()
    if (_utc_now() - created).total_seconds() > OAUTH_PENDING_TTL_SECONDS:
        raise XAuthError("OAuth state expired; start the connection again")
    return verifier


class XTokenService:
    """Encrypted X tokens with a database lease for refresh (safe across processes)."""

    LEASE_SECONDS = 30  # longer than the token request timeout, so a live refresh never loses its lease
    LEASE_POLL_SECONDS = 1.0
    LEASE_WAIT_SECONDS = 45

    def __init__(self, store: Optional[SQLiteStore] = None):
        self.store = store or SQLiteStore()
        self._fernet = Fernet(base64.urlsafe_b64encode(load_master_key()))

    def _encrypt(self, token: XToken) -> str:
        return self._fernet.encrypt(json.dumps(token.to_dict()).encode("utf-8")).decode("ascii")

    def _decrypt(self, ciphertext: str) -> XToken:
        return XToken.from_dict(json.loads(self._fernet.decrypt(ciphertext.encode("ascii"))))

    def save(self, account_id: str, token: XToken) -> None:
        """Store a token for a newly connected account, or replace the stored one (new version)."""
        with self.store.get_session() as session:
            row = session.get(PlatformToken, (PLATFORM, account_id))
            if row is None:
                session.add(
                    PlatformToken(
                        platform=PLATFORM,
                        account_id=account_id,
                        encrypted_token=self._encrypt(token),
                        version=0,
                    )
                )
            else:
                row.encrypted_token = self._encrypt(token)
                row.version = (row.version or 0) + 1
                row.refresh_lease_until = None
                row.updated_at = _utc_now()
            session.commit()

    def load(self, account_id: str) -> Optional[XToken]:
        snapshot = self._snapshot(account_id)
        return snapshot[0] if snapshot else None

    def _snapshot(self, account_id: str) -> Optional[tuple[XToken, int, Optional[datetime]]]:
        with self.store.get_session() as session:
            row = session.get(PlatformToken, (PLATFORM, account_id))
            if row is None:
                return None
            return self._decrypt(row.encrypted_token), row.version or 0, row.refresh_lease_until

    def _claim_lease(self, account_id: str, version: int) -> bool:
        """Take the refresh lease only if nobody refreshed since ``version`` was read."""
        now = _utc_now()
        with self.store.get_session() as session:
            claimed = (
                session.query(PlatformToken)
                .filter(
                    PlatformToken.platform == PLATFORM,
                    PlatformToken.account_id == account_id,
                    PlatformToken.version == version,
                    (PlatformToken.refresh_lease_until.is_(None)) | (PlatformToken.refresh_lease_until < now),
                )
                .update(
                    {"refresh_lease_until": now + timedelta(seconds=self.LEASE_SECONDS)},
                    synchronize_session=False,
                )
            )
            session.commit()
            return claimed == 1

    def _release_lease(self, account_id: str) -> None:
        with self.store.get_session() as session:
            session.query(PlatformToken).filter(
                PlatformToken.platform == PLATFORM, PlatformToken.account_id == account_id
            ).update({"refresh_lease_until": None}, synchronize_session=False)
            session.commit()

    def _commit_refresh(self, account_id: str, token: XToken, expected_version: int) -> bool:
        with self.store.get_session() as session:
            written = (
                session.query(PlatformToken)
                .filter(
                    PlatformToken.platform == PLATFORM,
                    PlatformToken.account_id == account_id,
                    PlatformToken.version == expected_version,
                )
                .update(
                    {
                        "encrypted_token": self._encrypt(token),
                        "version": expected_version + 1,
                        "refresh_lease_until": None,
                        "updated_at": _utc_now(),
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return written == 1

    async def ensure_fresh(self, connector: XConnector, account_id: str, force: bool = False) -> XToken:
        """Return a usable access token, renewing it once across all processes when needed."""
        waited = 0.0
        while True:
            snapshot = self._snapshot(account_id)
            if snapshot is None:
                raise XAuthError("no X account connected for this id")
            current, version, lease_until = snapshot
            if not force and not current.is_expired():
                return current
            if not current.refresh_token:
                raise XAuthError("no refresh token stored; reconnect the X account")

            if lease_until is not None and lease_until > _utc_now():
                if waited >= self.LEASE_WAIT_SECONDS:
                    raise XAuthError("X token refresh is still in progress; try again shortly")
                await asyncio.sleep(self.LEASE_POLL_SECONDS)
                waited += self.LEASE_POLL_SECONDS
                force = False
                continue

            if not self._claim_lease(account_id, version):
                force = False
                continue

            try:
                refreshed = await connector.refresh(current.refresh_token)
            except httpx.HTTPStatusError as exc:
                self._release_lease(account_id)
                if exc.response.status_code in (400, 401, 403):
                    raise XAuthError("X authorization was revoked or expired; reconnect the account") from exc
                raise
            except Exception:
                self._release_lease(account_id)
                raise

            if refreshed.refresh_token is None:
                refreshed.refresh_token = current.refresh_token
            if not self._commit_refresh(account_id, refreshed, version):
                logger.error("X token refresh for account %s was not stored (version changed)", account_id)
                raise XAuthError("X token changed during refresh; try again")
            logger.info("Refreshed X token for account %s", account_id)
            return refreshed
