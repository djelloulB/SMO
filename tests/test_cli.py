"""CLI smoke tests."""
from typer.testing import CliRunner

from smo.cli import app_cli

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app_cli, ["version"])

    assert result.exit_code == 0
    assert "SMO" in result.stdout


def test_check_command_reports_security_layer():
    result = runner.invoke(app_cli, ["check"])

    assert result.exit_code == 0
    assert "dry_run=True" in result.stdout
    assert "human_gate=True" in result.stdout


def test_daemon_runs_scheduler_until_interrupted(monkeypatch):
    import smo.cli as cli_module
    import smo.orchestrator.scheduler as scheduler_module

    events = []

    class InterruptingClock:
        @staticmethod
        def sleep(_seconds):
            raise KeyboardInterrupt

    monkeypatch.setattr(scheduler_module, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(scheduler_module, "stop_scheduler", lambda: events.append("stop"))
    monkeypatch.setattr(cli_module, "time", InterruptingClock)

    result = runner.invoke(app_cli, ["daemon"])

    assert result.exit_code == 0
    assert events == ["start", "stop"]
    assert "Press Ctrl+C" in result.stdout


def _write_local_input(tmp_path, name="post.md", text="# Hello\n\nBody text here."):
    inputs = tmp_path / "data" / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / name).write_text(text, encoding="utf-8")


def test_content_workflow_cli(tmp_path, monkeypatch, tmp_db):
    monkeypatch.chdir(tmp_path)
    _write_local_input(tmp_path)

    result = runner.invoke(app_cli, ["ingest", "--local", "post.md"])
    assert result.exit_code == 0, result.stdout
    assert "Ingested: 1 draft(s)" in result.stdout

    result = runner.invoke(app_cli, ["drafts"])
    assert result.exit_code == 0
    assert "draft" in result.stdout

    result = runner.invoke(app_cli, ["show", "1"])
    assert result.exit_code == 0
    assert "Hello" in result.stdout

    result = runner.invoke(app_cli, ["approve", "1"])
    assert result.exit_code == 0
    assert "Approved content 1" in result.stdout

    result = runner.invoke(
        app_cli,
        ["publish", "1", "--account-id", "test-account", "--organization-id", "123"],
    )
    assert result.exit_code == 0, result.stdout
    assert "DRY_RUN enabled" in result.stdout
    assert "urn:li:organization:123" in result.stdout
    assert list((tmp_path / "data" / "dryrun").glob("content-1-*.json"))


def test_publish_requires_approval_cli(tmp_path, monkeypatch, tmp_db):
    monkeypatch.chdir(tmp_path)
    _write_local_input(tmp_path)

    result = runner.invoke(app_cli, ["ingest", "--local", "post.md"])
    assert result.exit_code == 0, result.stdout

    result = runner.invoke(app_cli, ["publish", "1", "--account-id", "test-account"])

    assert result.exit_code == 1
    assert "approve it before publishing" in result.stdout
