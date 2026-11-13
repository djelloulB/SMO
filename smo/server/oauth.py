"""OAuth handler with CSRF and redirect validation (REC-004)."""
import hashlib
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..connectors.linkedin import LinkedInConnector, get_linkedin_connector
from ..config.settings import settings
from ..memory.sqlite_store import LinkedInTokenStore, SQLiteStore
from ..security.audit import audit_logger
from ..services.account_service import AccountService

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory state store (production: Redis or DB)
_state_store: dict[str, dict] = {}


@router.get("/linkedin")
async def linkedin_oauth_start(
    state: str = Query(..., description="CSRF state nonce"),
    redirect_uri: str = Query(..., description="Redirect URI"),
):
    """Start LinkedIn OAuth 2.0 PKCE flow."""
    connector = get_linkedin_connector()
    if not connector:
        raise HTTPException(status_code=503, detail="LinkedIn not configured")

    base = settings.public_base_url.rstrip("/")
    expected_redirect = f"{base}/oauth/linkedin/callback"
    if redirect_uri != expected_redirect:
        logger.warning("redirect_uri mismatch: %s vs %s", redirect_uri, expected_redirect)
        raise HTTPException(status_code=400, detail="redirect_uri must match expected value")

    # Generate PKCE
    code_verifier, code_challenge = connector.generate_pkce()

    # Store state with PKCE verifier
    _state_store[state] = {
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "platform": "linkedin",
    }

    auth_url = connector.get_authorization_url(state, code_challenge)
    logger.info("LinkedIn OAuth flow started with state %s", state)

    return {
        "status": "redirect_ready",
        "authorization_url": auth_url,
        "redirect_uri": redirect_uri,
        "state": state,
    }


@router.get("/linkedin/callback")
async def linkedin_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None, description="Authorization code"),
    state: Optional[str] = Query(None, description="CSRF state nonce"),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """LinkedIn OAuth callback handler with PKCE token exchange."""
    if error:
        logger.error("LinkedIn OAuth error: %s - %s", error, error_description)
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    if not state or state not in _state_store:
        logger.warning("Invalid OAuth state: %s", state)
        raise HTTPException(status_code=400, detail="Invalid state")

    stored = _state_store.pop(state)
    code_verifier = stored["code_verifier"]

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code provided")

    connector = get_linkedin_connector()
    if not connector:
        raise HTTPException(status_code=503, detail="LinkedIn not configured")

    try:
        # Exchange code for token
        token = await connector.exchange_code_for_token(code, code_verifier)

        # Get profile to identify account
        profile = await connector.get_profile(token)
        account_id = profile["id"]

        # Save token
        connector.save_token(account_id, token)

        label = profile.get("name") or " ".join(
            part for part in (profile.get("given_name"), profile.get("family_name")) if part
        )
        try:
            AccountService().register(
                "linkedin",
                account_id,
                label=label or None,
                profile_id=profile.get("sub") or account_id,
            )
        except Exception:  # noqa: BLE001 - registration must not break OAuth
            logger.exception("Unable to register LinkedIn account %s", account_id)

        logger.info("LinkedIn OAuth completed for account %s", account_id)

        payload = {"platform": "linkedin", "account_id": account_id}
        payload_hash = hashlib.sha256(str(payload).encode()).hexdigest()[:32]
        audit_logger.log(
            actor_id=f"oauth:{account_id}",
            tool="exchange_token",
            payload_hash=payload_hash,
            result="success",
        )

        return {
            "status": "connected",
            "platform": "linkedin",
            "account_id": account_id,
            "message": "LinkedIn account connected successfully",
        }

    except Exception as e:
        logger.exception("LinkedIn token exchange failed")
        audit_logger.log(
            actor_id="oauth:linkedin",
            tool="exchange_token",
            payload_hash=hashlib.sha256(str(e).encode()).hexdigest()[:32],
            result="failure",
        )
        raise HTTPException(status_code=500, detail=f"Token exchange failed: {e}")


@router.get("/x")
async def x_oauth_start():
    """Start X OAuth 2.0 with PKCE. Disabled unless X_ENABLED=true (paid API tiers)."""
    from fastapi.responses import RedirectResponse

    from ..connectors.x import XConnector, start_authorization, x_enabled

    if not x_enabled():
        raise HTTPException(status_code=403, detail="X is disabled (set X_ENABLED=true)")
    connector = XConnector.from_environment()
    if connector is None:
        raise HTTPException(status_code=503, detail="X is not configured")
    return RedirectResponse(start_authorization(connector))


@router.get("/x/callback")
async def x_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Finish X OAuth: check the one-time state, exchange the code and store the encrypted token."""
    from ..connectors.x import (
        XAuthError,
        XConnector,
        XTokenService,
        consume_authorization_state,
        x_enabled,
    )

    if not x_enabled():
        raise HTTPException(status_code=403, detail="X is disabled (set X_ENABLED=true)")
    if error or not code or not state:
        raise HTTPException(status_code=400, detail="X authorization was not granted")
    try:
        verifier = consume_authorization_state(state)
    except XAuthError as exc:
        logger.warning("X OAuth callback rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))

    connector = XConnector.from_environment()
    if connector is None:
        raise HTTPException(status_code=503, detail="X is not configured")
    try:
        token = await connector.exchange_code(code, verifier)
        me = await connector.get_me(token.access_token)
    except Exception as exc:  # noqa: BLE001 - never echo provider responses
        audit_logger.log(actor_id="oauth:x", tool="oauth_x_connect", payload_hash="0" * 32, result="failure")
        logger.error("X token exchange failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="X token exchange failed")

    account_id = str(me.get("id", ""))
    if not account_id:
        raise HTTPException(status_code=502, detail="X did not return an account id")
    XTokenService().save(account_id, token)
    try:
        AccountService().register(
            "x", account_id, label=me.get("username"), profile_id=account_id
        )
    except Exception:  # noqa: BLE001 - registration must not break OAuth
        logger.exception("Unable to register X account %s", account_id)
    audit_logger.log(actor_id="oauth:x", tool="oauth_x_connect", payload_hash=hashlib.sha256(account_id.encode()).hexdigest()[:32], result="success")
    return {"status": "connected", "account_id": account_id, "username": me.get("username")}


@router.get("/{platform}")
async def oauth_start(
    platform: str,
    state: str = Query(..., description="CSRF state nonce"),
    redirect_uri: str = Query(..., description="Redirect URI"),
):
    """Start OAuth flow (state + redirect validation) - generic fallback."""
    base = settings.public_base_url.rstrip("/")
    expected_redirect = f"{base}/oauth/{platform}/callback"
    if redirect_uri != expected_redirect:
        logger.warning("redirect_uri mismatch: %s vs %s", redirect_uri, expected_redirect)
        raise HTTPException(status_code=400, detail="redirect_uri must match expected value")

    _state_store[state] = redirect_uri
    logger.info("OAuth flow started for %s with state %s", platform, state)
    return {"status": "redirect_ready", "redirect_uri": redirect_uri, "state": state}


@router.get("/{platform}/callback")
async def oauth_callback(
    platform: str,
    code: Optional[str] = Query(None, description="Authorization code"),
    state: Optional[str] = Query(None, description="CSRF state nonce"),
):
    """OAuth callback handler - generic fallback."""
    if not state or state not in _state_store:
        raise HTTPException(status_code=400, detail="Invalid state")

    _state_store.pop(state)
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code provided")

    payload = {"platform": platform, "code_provided": True}
    payload_hash = hashlib.sha256(str(payload).encode()).hexdigest()[:32]
    audit_logger.log(actor_id="oauth", tool="exchange_token", payload_hash=payload_hash, result="success")
    return {"status": "mock_token_received", "platform": platform, "message": "OAuth callback handled (MVP)"}


@router.post("/linkedin/disconnect")
async def linkedin_disconnect(account_id: str):
    """Disconnect LinkedIn account (revoke tokens)."""
    connector = get_linkedin_connector()
    if not connector:
        raise HTTPException(status_code=503, detail="LinkedIn not configured")

    connector.delete_token(account_id)

    db = SQLiteStore()
    with db.get_session() as session:
        session.query(LinkedInTokenStore).filter_by(account_id=account_id).delete()
        session.commit()

    audit_logger.log(
        actor_id=f"oauth:{account_id}",
        tool="disconnect",
        payload_hash=hashlib.sha256(account_id.encode()).hexdigest()[:32],
        result="success",
    )
    return {"status": "disconnected", "account_id": account_id}


@router.get("/linkedin/status")
async def linkedin_status():
    """Check LinkedIn connector configuration."""
    connector = get_linkedin_connector()
    if not connector:
        return {"configured": False, "message": "LINKEDIN_CLIENT_ID/SECRET not set"}

    db = SQLiteStore()
    with db.get_session() as session:
        accounts = session.query(LinkedInTokenStore).all()
        return {
            "configured": True,
            "connected_accounts": [a.account_id for a in accounts],
        }