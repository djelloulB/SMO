"""CLI entrypoint for SMO."""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

import typer

from smo.config import settings
from smo.config.security import security_layer
from smo.config.settings import load_master_key
from smo.connectors.linkedin import get_linkedin_connector
from smo.content.sources import LocalFileSourceProvider, RSSSourceProvider, fetch_all
from smo.memory.sqlite_store import LinkedInTokenStore, SQLiteStore
from smo.security.audit import audit_logger
from smo.services.content_service import (
    ContentError,
    ContentService,
    InvalidTransition,
    ModerationError,
)
from smo.services.publishing_service import PublishingService

logger = logging.getLogger(__name__)
app_cli = typer.Typer(name="smo", help="Social Media Orchestrator — autonomous social media agent.")


@app_cli.command()
def version() -> None:
    """Show version and security layer."""
    typer.echo(f"SMO {settings.app_version}")
    typer.echo(f"Security layer: dry_run={security_layer.dry_run}, human_gate={security_layer.human_gate}")


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
    typer.echo(f"Security layer: dry_run={security_layer.dry_run}, human_gate={security_layer.human_gate}")
    typer.echo(f"Budget per day: ${security_layer.budget_per_day_usd:.2f}")

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
    """Run the scheduler in the foreground: dispatches queued posts and polls metrics (Ctrl+C to stop)."""
    from smo.orchestrator.scheduler import start_scheduler, stop_scheduler

    audit_logger.log(
        actor_id="daemon",
        tool="daemon_start",
        payload_hash=hashlib.sha256(b"daemon").hexdigest()[:32],
        result="success",
    )
    start_scheduler()
    typer.echo("SMO scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_scheduler()
        typer.echo("SMO scheduler stopped.")


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
    organization_id: Optional[str] = typer.Option(
        None, "--organization-id", "-o", help="Publish as this organization (numeric ID or full URN)"
    ),
    author_urn: Optional[str] = typer.Option(
        None, "--author-urn", help="Explicit author URN (e.g. urn:li:organization:123)"
    ),
) -> None:
    """Publish a post to LinkedIn (member profile or organization page)."""
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

        result = await connector.publish_post(
            token,
            text,
            visibility,
            organization_id=organization_id,
            author_urn=author_urn,
        )
        typer.echo(f"Published successfully: {result}")

    asyncio.run(_publish())


@app_cli.command(name="linkedin-orgs")
def linkedin_orgs(
    account_id: str = typer.Argument(..., help="LinkedIn account ID (person URN)"),
) -> None:
    """List LinkedIn pages you can publish to (requires r_organization_admin)."""
    import asyncio

    connector = get_linkedin_connector()
    if not connector:
        typer.echo("LinkedIn not configured")
        raise typer.Exit(1)

    token = connector.load_token(account_id)
    if not token:
        typer.echo(f"No token found for account: {account_id}")
        raise typer.Exit(1)

    try:
        organizations = asyncio.run(connector.get_administered_organizations(token))
    except Exception as e:
        typer.echo(f"Unable to list organizations: {e}")
        typer.echo("Re-authorize with the organization scopes (run 'smo linkedin-connect').")
        raise typer.Exit(1)

    if not organizations:
        typer.echo("No administered organization found for this account.")
        return

    for org in organizations:
        typer.echo(f"  - {org['urn']} (role: {org.get('role')}, state: {org.get('state')})")


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


# --- Content workflow commands ---
@app_cli.command()
def ingest(
    rss: Optional[list[str]] = typer.Option(
        None, "--rss", "-r", help="RSS/Atom feed URL (repeatable)"
    ),
    local: Optional[list[str]] = typer.Option(
        None, "--local", "-l", help="Local .txt/.md file under data/inputs (repeatable)"
    ),
    limit: int = typer.Option(5, "--limit", "-n", help="Max items per RSS feed"),
) -> None:
    """Ingest content from RSS feeds and/or local files into drafts."""
    import asyncio

    providers = []
    for url in rss or []:
        providers.append(RSSSourceProvider(url, limit=limit))
    for path in local or []:
        providers.append(LocalFileSourceProvider(path))

    if not providers:
        typer.echo("Nothing to ingest: pass --rss URL or --local FILE.")
        raise typer.Exit(1)

    items = asyncio.run(fetch_all(providers))
    if not items:
        typer.echo("No items fetched.")
        return

    service = ContentService()
    created = duplicates = blocked = 0
    for item in items:
        result = service.ingest_item(item)
        if result.duplicate:
            duplicates += 1
            continue
        if not result.moderation.allowed:
            blocked += 1
            typer.echo(
                f"  ! {result.record.id} blocked: {'; '.join(result.moderation.reasons)}"
            )
        else:
            created += 1
            typer.echo(f"  + {result.record.id} [{item.source[:40]}] {item.title[:70]}")

    typer.echo(
        f"Ingested: {created} draft(s), {duplicates} duplicate(s), {blocked} blocked."
    )


@app_cli.command()
def drafts(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows"),
) -> None:
    """List ingested content and its workflow status."""
    records = ContentService().list(status=status, limit=limit)
    if not records:
        typer.echo("No content found.")
        return

    for record in records:
        first_line = (record.text or "").splitlines()[0] if record.text else ""
        typer.echo(
            f"  {record.id:>4}  {record.status:<10}  {record.platform or '-':<9}  {first_line[:60]}"
        )


@app_cli.command()
def show(content_id: int = typer.Argument(..., metavar="ID")) -> None:
    """Show a single content item."""
    record = ContentService().get(content_id)
    if record is None:
        typer.echo(f"Content {content_id} not found.")
        raise typer.Exit(1)

    typer.echo(f"ID:       {record.id}")
    typer.echo(f"Status:   {record.status}")
    typer.echo(f"Platform: {record.platform or '-'}")
    typer.echo(f"Source:   {record.source or '-'}")
    typer.echo("")
    typer.echo(record.text)


@app_cli.command()
def approve(content_id: int = typer.Argument(..., metavar="ID")) -> None:
    """Approve a draft (human gate)."""
    try:
        record = ContentService().approve(content_id)
    except ModerationError as exc:
        typer.echo(f"Moderation blocked content {content_id}: {'; '.join(exc.reasons)}")
        raise typer.Exit(1)
    except (ContentError, InvalidTransition) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)

    typer.echo(f"Approved content {record.id}.")


@app_cli.command()
def reject(content_id: int = typer.Argument(..., metavar="ID")) -> None:
    """Reject a draft."""
    try:
        record = ContentService().reject(content_id)
    except (ContentError, InvalidTransition) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)

    typer.echo(f"Rejected content {record.id}.")


@app_cli.command()
def publish(
    content_id: int = typer.Argument(..., metavar="ID"),
    account_id: str = typer.Option(..., "--account-id", "-a", help="LinkedIn account ID"),
    organization_id: Optional[str] = typer.Option(
        None, "--organization-id", "-o", help="Publish as this organization"
    ),
    author_urn: Optional[str] = typer.Option(None, "--author-urn", help="Explicit author URN"),
    visibility: str = typer.Option(
        "PUBLIC", "--visibility", "-v", help="PUBLIC or CONNECTIONS"
    ),
) -> None:
    """Publish an approved content item to LinkedIn (or write a dry-run payload)."""
    import asyncio

    service = PublishingService()
    try:
        result = asyncio.run(
            service.publish(
                content_id,
                account_id,
                organization_id=organization_id,
                author_urn=author_urn,
                visibility=visibility,
            )
        )
    except (ContentError, InvalidTransition) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)

    if result.status == "dry_run":
        typer.echo(f"DRY_RUN enabled: outbound payload written to {result.dry_run_path}")
        typer.echo(json.dumps(result.payload, indent=2))
    else:
        typer.echo(f"Published content {result.content_id}: {result.api_result}")


@app_cli.command()
def schedule(
    content_id: int = typer.Argument(..., metavar="ID"),
    account_id: str = typer.Option(..., "--account-id", "-a", help="LinkedIn account ID"),
    strategy: str = typer.Option(
        "fixed_slots", "--strategy", "-s", help="fixed_slots, randomized_window or weighted_by_engagement"
    ),
    at: Optional[str] = typer.Option(None, "--at", help="ISO 8601 datetime (overrides the strategy)"),
    timezone_name: str = typer.Option("UTC", "--timezone", "-z", help="IANA timezone, e.g. Europe/Paris"),
    slots: Optional[list[str]] = typer.Option(None, "--slot", help="HH:MM for fixed_slots (repeatable)"),
    window: Optional[str] = typer.Option(None, "--window", help="HH:MM-HH:MM for randomized_window"),
    organization_id: Optional[str] = typer.Option(None, "--organization-id", "-o", help="Publish as this organization"),
) -> None:
    """Queue an approved content item for publication at the strategy's next time."""
    from smo.services.scheduling_service import SchedulingService

    service = SchedulingService()
    try:
        job = service.schedule(
            content_id,
            account_id,
            strategy=strategy,
            at=datetime.fromisoformat(at) if at else None,
            timezone_name=timezone_name,
            slots=slots,
            window=window,
            organization_id=organization_id,
            actor_id="cli",
        )
    except (ContentError, InvalidTransition, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)

    typer.echo(f"Queued content {job.content_id} as job {job.job_id} at {job.run_at.isoformat()}")
    typer.echo("Publication runs while the SMO web server or 'smo daemon' is running.")


@app_cli.command()
def mcp() -> None:
    """Run the MCP server on stdio for an external agent (set SMO_MCP_API_KEY)."""
    from smo.mcp_server.server import run_stdio

    run_stdio()


def main() -> None:
    app_cli()


if __name__ == "__main__":
    main()
