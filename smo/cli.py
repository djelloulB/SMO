"""CLI entrypoint for SMO."""
import hashlib
import logging
import uuid

import typer

from smo.config import settings
from smo.config.settings import load_master_key
from smo.connectors.linkedin import get_linkedin_connector
from smo.security.audit import audit_logger
from smo.memory.sqlite_store import SQLiteStore, LinkedInTokenStore

logger = logging.getLogger(__name__)
app_cli = typer.Typer(name="smo", help="Social Media Orchestrator — autonomous social media agent.")


@app_cli.command()
def version() -> None:
    """Show version and security layer."""
    typer.echo(f"SMO {settings.app_version}")
    typer.echo(f"Security layer: dry_run={settings.security_controls.dry_run}, human_gate={settings.security_controls.human_gate}")


@app_cli.command()
def auth(platform: str = typer.Option(..., "--platform", "-p", help="Platform to authenticate (linkedin, x, meta, tiktok, youtube)")) -> None:
    """Start OAuth flow for a platform (placeholder for Phase 1)."""
    state = str(uuid.uuid4())
    redirect_uri = f"{settings.public_base_url}/oauth/{platform}/callback"
    typer.echo(f"Starting OAuth flow for {platform}...")
    typer.echo(f"Redirect URI: {redirect_uri}")
    typer.echo(f"State: {state}")

    payload = {"platform": platform, "redirect_uri": redirect_uri, "state": state}
    audit_logger.log(actor_id="cli", tool="auth_start", payload_hash=hashlib.sha256(str(payload).encode()).hexdigest()[:32], result="success")
    typer.echo(f"Navigate to: {redirect_uri}")
    typer.echo("Note: platform-specific OAuth URLs are placeholders in MVP.")


@app_cli.command()
def check() -> None:
    """Check system status."""
    typer.echo(f"Version: {settings.app_version}")
    typer.echo(f"Security layer: dry_run={settings.security_controls.dry_run}, human_gate={settings.security_controls.human_gate}")
    typer.echo(f"Budget per day: ${settings.security_controls.budget_per_day_usd:.2f}")

    db = SQLiteStore()
    try:
        with db.get_session() as session:
            from sqlalchemy import text
            count = session.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        typer.echo(f"Database: ok (accounts: {count})")
    except Exception:
        typer.echo("Database: not initialized (first run)")


@app_cli.command()
def daemon() -> None:
    """Start the SMO daemon (placeholder for Phase 2)."""
    from ..security.audit import audit_logger
    import hashlib as _h
    audit_logger.log(actor_id="daemon", tool="daemon_start", payload_hash=_h.sha256(b"placeholder").hexdigest()[:32], result="success")
    typer.echo("Run 'smo check' to verify the server is running.")


# --- LinkedIn commands ---
@app_cli.command(name="linkedin-connect")
def linkedin_connect() -> None:
    """Start LinkedIn OAuth 2.0 PKCE flow."""
    connector = get_linkedin_connector()
    if not connector:
        typer.echo("LinkedIn not configured (set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET)")
        raise typer.Exit(1)

    import secrets
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = connector.generate_pkce()

    auth_url = connector.get_authorization_url(state, code_challenge)
    typer.echo("LinkedIn OAuth 2.0 PKCE flow started")
    typer.echo(f"State: {state}")
    typer.echo(f"Code verifier (save this): {code_verifier}")
    typer.echo(f"Authorization URL: {auth_url}")
    typer.echo("Open the URL in a browser, authorize, then run 'smo linkedin-callback' with the code.")


@app_cli.command(name="linkedin-callback")
def linkedin_callback(
    code: str = typer.Argument(..., help="Authorization code from LinkedIn callback"),
    state: str = typer.Argument(..., help="State from OAuth start"),
    code_verifier: str = typer.Argument(..., help="Code verifier from OAuth start"),
) -> None:
    """Complete LinkedIn OAuth flow with authorization code."""
    import asyncio

    connector = get_linkedin_connector()
    if not connector:
        typer.echo("LinkedIn not configured")
        raise typer.Exit(1)

    async def _exchange():
        token = await connector.exchange_code_for_token(code, code_verifier)
        profile = await connector.get_profile(token)
        account_id = profile["id"]
        connector.save_token(account_id, token)
        typer.echo(f"Connected LinkedIn account: {account_id}")
        typer.echo(f"Profile: {profile.get('localizedFirstName', '')} {profile.get('localizedLastName', '')}")

    asyncio.run(_exchange())


@app_cli.command(name="linkedin-disconnect")
def linkedin_disconnect(account_id: str = typer.Argument(..., help="LinkedIn account ID (person URN)")) -> None:
    """Disconnect a LinkedIn account."""
    connector = get_linkedin_connector()
    if not connector:
        typer.echo("LinkedIn not configured")
        raise typer.Exit(1)

    connector.delete_token(account_id)
    typer.echo(f"Disconnected account: {account_id}")


@app_cli.command(name="linkedin-publish")
def linkedin_publish(
    account_id: str = typer.Argument(..., help="LinkedIn account ID (person URN)"),
    text: str = typer.Argument(..., help="Post text"),
    visibility: str = typer.Option("PUBLIC", "--visibility", "-v", help="Visibility: PUBLIC or CONNECTIONS"),
) -> None:
    """Publish a post to LinkedIn."""
    import asyncio

    connector = get_linkedin_connector()
    if not connector:
        typer.echo("LinkedIn not configured")
        raise typer.Exit(1)

    token = connector.load_token(account_id)
    if not token:
        typer.echo(f"No token found for account: {account_id}")
        raise typer.Exit(1)

    async def _publish():
        if token.is_expired():
            if not token.refresh_token:
                typer.echo("Token expired and no refresh token available")
                raise typer.Exit(1)
            token = await connector.refresh_access_token(token.refresh_token)
            connector.save_token(account_id, token)

        result = await connector.publish_post(token, text, visibility)
        typer.echo(f"Published successfully: {result}")

    asyncio.run(_publish())


@app_cli.command(name="linkedin-status")
def linkedin_status() -> None:
    """Show LinkedIn connector status."""
    connector = get_linkedin_connector()
    if not connector:
        typer.echo("LinkedIn not configured")
        return

    db = SQLiteStore()
    with db.get_session() as session:
        accounts = session.query(LinkedInTokenStore).all()
        if accounts:
            for a in accounts:
                typer.echo(f"  - {a.account_id} (updated: {a.updated_at})")
        else:
            typer.echo("No connected accounts")


def main() -> None:
    app_cli()


if __name__ == "__main__":
    main()
