"""Shared test configuration.

Environment is configured before any ``smo`` module is imported so that the
module-level singletons (settings, audit logger) use an isolated test database
and deterministic credentials.
"""
import base64
import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="smo-tests-"))

os.environ["SMO_DB_PATH"] = str(_TMP_DIR / "smo-test.db")
os.environ["SMO_MASTER_KEY"] = base64.urlsafe_b64encode(b"t" * 32).decode()
os.environ["PUBLIC_BASE_URL"] = "http://localhost:8023"
os.environ["LINKEDIN_CLIENT_ID"] = "test-client-id"
os.environ["LINKEDIN_CLIENT_SECRET"] = "test-client-secret"
os.environ["LINKEDIN_REDIRECT_URI"] = "http://localhost:8023/oauth/linkedin/callback"

import pytest  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point the stores at an isolated SQLite file for the duration of a test."""
    db_path = tmp_path / "smo.db"
    monkeypatch.setenv("SMO_DB_PATH", str(db_path))
    return str(db_path)
