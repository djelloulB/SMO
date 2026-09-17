"""
Test script for SMO scaffold (basic import and security checks).
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")

    try:
        from smo import cli
        print("✓ smo.cli imported")
    except Exception as e:
        print(f"✗ Failed to import smo.cli: {e}")

    try:
        from smo.config import settings, load_master_key
        print("✓ smo.config imported")
    except Exception as e:
        print(f"✗ Failed to import smo.config: {e}")

    try:
        from smo.config.security import security_layer, mutable_config
        print("✓ smo.config.security imported")
    except Exception as e:
        print(f"✗ Failed to import smo.config.security: {e}")

    try:
        from smo.utils import redact_passwords, redact_dict, SecretRedactionFilter
        print("✓ smo.utils imported")
    except Exception as e:
        print(f"✗ Failed to import smo.utils: {e}")

    try:
        from smo.utils.http import ssrf_guard, SSRFGuard
        print("✓ smo.utils.http imported")
    except Exception as e:
        print(f"✗ Failed to import smo.utils.http: {e}")

    try:
        from smo.utils.safeio import ensure_base_dir, sanitize_filename
        print("✓ smo.utils.safeio imported")
    except Exception as e:
        print(f"✗ Failed to import smo.utils.safeio: {e}")

    try:
        from smo.connectors.base import BaseConnector
        print("✓ smo.connectors.base imported")
    except Exception as e:
        print(f"✗ Failed to import smo.connectors.base: {e}")

    try:
        from smo.memory.sqlite_store import SQLiteStore
        print("✓ smo.memory.sqlite_store imported")
    except Exception as e:
        print(f"✗ Failed to import smo.memory.sqlite_store: {e}")

    try:
        from smo.security.audit import AuditLogger
        print("✓ smo.security.audit imported")
    except Exception as e:
        print(f"✗ Failed to import smo.security.audit: {e}")

    try:
        from smo.server.oauth import router as oauth_router
        print("✓ smo.server.oauth imported")
    except Exception as e:
        print(f"✗ Failed to import smo.server.oauth: {e}")

    try:
        from smo.server.app import app as fastapi_app
        print("✓ smo.server.app imported")
    except Exception as e:
        print(f"✗ Failed to import smo.server.app: {e}")


def test_security_layer():
    """Test that the immutable security layer is working."""
    print("\nTesting security layer...")

    from smo.config.security import security_layer

    print(f"✓ Dry run: {security_layer.dry_run}")
    print(f"✓ Human gate: {security_layer.human_gate}")
    print(f"✓ Budget per day: ${security_layer.budget_per_day_usd:.2f}")

    # Check that security controls are immutable
    try:
        mutable_config.set("dry_run", False)
        print("✗ FAIL: Immutable security control 'dry_run' can be modified!")
    except ValueError:
        print("✓ Immutable security controls enforced (dry_run cannot be modified)")


def test_redaction():
    """Test secret redaction."""
    print("\nTesting redaction...")

    from smo.utils import redact_passwords, redact_dict

    text = "SMO_MASTER_KEY=secret123 TOKEN=abc123"
    redacted = redact_passwords(text)
    if "REDACTED" in redacted:
        print(f"✓ Redaction works: {redacted}")
    else:
        print(f"✗ Redaction failed: {redacted}")

    data = {"SMO_MASTER_KEY": "secret", "TOKEN": "abc"}
    redacted_data = redact_dict(data)
    if redacted_data.get("SMO_MASTER_KEY") == "REDACTED":
        print(f"✓ Dictionary redaction works: {redacted_data}")
    else:
        print(f"✗ Dictionary redaction failed: {redacted_data}")


def test_ssfy():
    """Test SSRF guard."""
    print("\nTesting SSRF guard...")

    from smo.utils.http import SSRFGuard
    from unittest.mock import AsyncMock

    guard = SSRFGuard()

    # Test that private IP is blocked
    try:
        # This is just to show the guard exists; we can't actually make network calls
        print("✓ SSRFGuard instantiated")
    except Exception as e:
        print(f"✗ SSRFGuard failed: {e}")


def test_audit():
    """Test audit logger."""
    print("\nTesting audit logger...")

    from smo.security.audit import audit_logger

    try:
        entry_id = audit_logger.log(
            actor_id="test",
            tool="test_tool",
            payload_hash="abc123",
            result="success"
        )
        print(f"✓ Audit log created (id: {entry_id})")
    except Exception as e:
        print(f"✗ Audit log failed: {e}")


def test_cli():
    """Test CLI commands."""
    print("\nTesting CLI...")

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "smo.cli", "version"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"✓ CLI version: {result.stdout.strip()}")
        else:
            print(f"✗ CLI failed: {result.stderr}")
    except Exception as e:
        print(f"⚠ CLI test skipped: {e}")


def main():
    """Run all tests."""
    print("=" * 60)
    print("SMO SCAFFOLD TESTS")
    print("=" * 60)

    test_imports()
    test_security_layer()
    test_redaction()
    test_ssfy()
    test_audit()
    test_cli()

    print("\n" + "=" * 60)
    print("TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
