"""Multi-account registry, account-scoped scheduling and validation (Phase 3)."""
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from smo.cli import app_cli
from smo.config.security import mutable_config
from smo.content.models import SourceItem
from smo.mcp_server.auth import Identity
from smo.mcp_server.server import build_server
from smo.memory.sqlite_store import SQLiteStore
from smo.services.account_service import AccountError, AccountService
from smo.services.content_service import ContentService
from smo.services.publishing_service import PublishingService
from smo.services.scheduling_service import SchedulingService

NOW = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)  # 07:00 in Paris

runner = CliRunner()


def approved(tmp_db, link, platform="linkedin"):
    service = ContentService(SQLiteStore(tmp_db))
    record = service.ingest_item(
        SourceItem(title="A title", summary="Useful update", link=link, source="rss:test"),
        platform=platform,
    ).record
    return service, service.approve(record.id)


def test_register_and_list_accounts(tmp_db):
    service = AccountService(SQLiteStore(tmp_db))

    service.register("linkedin", "urn:li:person:1", label="Alice")
    service.register("x", "42", label="alice", profile_id="42")

    assert [(a.platform, a.account_id) for a in service.list()] == [
        ("linkedin", "urn:li:person:1"),
        ("x", "42"),
    ]
    assert [a.account_id for a in service.list("x")] == ["42"]
    assert service.get("linkedin", "urn:li:person:1").label == "Alice"
    assert service.get("linkedin", "missing") is None


def test_register_is_an_upsert(tmp_db):
    service = AccountService(SQLiteStore(tmp_db))

    service.register("linkedin", "acc", label="Old label")
    service.register("linkedin", "acc", label="New label", profile_id="urn:li:person:9")

    rows = service.list("linkedin")
    assert len(rows) == 1
    assert rows[0].label == "New label"
    assert rows[0].profile_id == "urn:li:person:9"


def test_pause_and_resume_an_account(tmp_db):
    service = AccountService(SQLiteStore(tmp_db))
    service.register("x", "42")

    assert service.set_status("x", "42", "paused").status == "paused"
    assert service.set_status("x", "42", "active").status == "active"
    with pytest.raises(AccountError, match="unknown account status"):
        service.set_status("x", "42", "zombie")
    with pytest.raises(AccountError, match="unknown x account"):
        service.set_status("x", "missing", "paused")


def test_register_requires_identifiers(tmp_db):
    service = AccountService(SQLiteStore(tmp_db))

    with pytest.raises(AccountError, match="platform"):
        service.register("", "acc")
    with pytest.raises(AccountError, match="account id"):
        service.register("linkedin", "  ")


def test_unregistered_account_is_allowed_without_a_registry(tmp_db):
    assert AccountService(SQLiteStore(tmp_db)).require_active("linkedin", "legacy") is None


def test_unknown_account_is_refused_once_the_platform_has_a_registry(tmp_db):
    service = AccountService(SQLiteStore(tmp_db))
    service.register("linkedin", "known")

    with pytest.raises(AccountError, match="unknown linkedin account 'other'"):
        service.require_active("linkedin", "other")

    # Another platform is still in legacy mode.
    assert service.require_active("x", "42") is None


def test_paused_account_is_refused(tmp_db):
    service = AccountService(SQLiteStore(tmp_db))
    service.register("x", "42")
    service.set_status("x", "42", "paused")

    with pytest.raises(AccountError, match="is paused"):
        service.require_active("x", "42")


@pytest.mark.asyncio
async def test_publish_refuses_a_paused_account(tmp_db):
    content, record = approved(tmp_db, "https://example.com/paused-account")
    accounts = AccountService(SQLiteStore(tmp_db))
    accounts.register("linkedin", "acc")
    accounts.set_status("linkedin", "acc", "paused")

    with pytest.raises(AccountError, match="is paused"):
        await PublishingService(content_service=content).publish(record.id, "acc")


@pytest.mark.asyncio
async def test_publish_refuses_an_unknown_account_when_registered(tmp_db):
    content, record = approved(tmp_db, "https://example.com/unknown-account")
    AccountService(SQLiteStore(tmp_db)).register("linkedin", "known")

    with pytest.raises(AccountError, match="unknown linkedin account"):
        await PublishingService(content_service=content).publish(record.id, "typo")


def test_schedule_refuses_an_unknown_account_when_registered(tmp_db):
    _, record = approved(tmp_db, "https://example.com/schedule-unknown")
    AccountService(SQLiteStore(tmp_db)).register("linkedin", "known")

    with pytest.raises(AccountError, match="unknown linkedin account"):
        SchedulingService(store=SQLiteStore(tmp_db)).schedule(record.id, "typo", now=NOW)


def test_schedule_uses_the_account_config_defaults(tmp_db, monkeypatch):
    _, record = approved(tmp_db, "https://example.com/account-defaults")
    monkeypatch.setattr(
        mutable_config,
        "accounts",
        {"linkedin": {"scheduling": {"strategy": "fixed_slots", "timezone": "Europe/Paris"}}},
    )

    job = SchedulingService(store=SQLiteStore(tmp_db)).schedule(
        record.id, "acc", slots=["09:00"], now=NOW
    )

    # 09:00 Paris (winter) is 08:00 UTC.
    assert job.run_at == datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


def test_explicit_scheduling_arguments_win_over_the_account_config(tmp_db, monkeypatch):
    _, record = approved(tmp_db, "https://example.com/account-override")
    monkeypatch.setattr(
        mutable_config,
        "accounts",
        {"linkedin": {"scheduling": {"timezone": "Europe/Paris"}}},
    )

    job = SchedulingService(store=SQLiteStore(tmp_db)).schedule(
        record.id, "acc", slots=["09:00"], timezone_name="UTC", now=NOW
    )

    assert job.run_at == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def test_list_accounts_mcp_tool(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    AccountService(SQLiteStore(tmp_db)).register("linkedin", "urn:li:person:1", label="Alice")
    server = build_server(Identity("agent:hermes", frozenset({"read", "content"})))

    body = server._tool_manager.get_tool("list_accounts").fn()

    assert body["accounts"][0]["account_id"] == "urn:li:person:1"
    assert body["accounts"][0]["label"] == "Alice"
    assert body["accounts"][0]["connected"] is False


def test_list_accounts_is_not_exposed_to_agents_without_read_scope(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)
    server = build_server(Identity("agent:control", frozenset({"control"})))

    names = {tool.name for tool in server._tool_manager.list_tools()}

    assert "list_accounts" not in names


def test_cli_accounts_add_list_and_pause(tmp_db, monkeypatch):
    monkeypatch.setenv("SMO_DB_PATH", tmp_db)

    added = runner.invoke(
        app_cli,
        ["accounts-add", "--platform", "wordpress", "--account-id", "https://blog.example.com"],
    )
    assert added.exit_code == 0, added.output

    listed = runner.invoke(app_cli, ["accounts"])
    assert "wordpress" in listed.output
    assert "https://blog.example.com" in listed.output

    paused = runner.invoke(
        app_cli,
        [
            "accounts-status",
            "--platform",
            "wordpress",
            "--account-id",
            "https://blog.example.com",
            "--status",
            "paused",
        ],
    )
    assert paused.exit_code == 0, paused.output
    assert AccountService(SQLiteStore(tmp_db)).get(
        "wordpress", "https://blog.example.com"
    ).status == "paused"
