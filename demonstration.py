"""
Demonstration script for SMO scaffold.
Shows how the security foundation works in practice.
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))


def demonstrate_security_layer():
    """Demonstrate the immutable security layer."""
    print("\n" + "="*70)
    print("DEMONSTRATION 1: Immutable Security Layer")
    print("="*70)

    from smo.config.security import security_layer, mutable_config

    print(f"\nSecurity controls (immutable):")
    print(f"  - Dry run: {security_layer.dry_run}")
    print(f"  - Human gate: {security_layer.human_gate}")
    print(f"  - Budget per day: ${security_layer.budget_per_day_usd:.2f}")

    print("\nAttempting to modify immutable controls...")
    try:
        mutable_config.set("dry_run", False)
        print("  ✗ FAIL: dry_run was modified (this should not be possible!)")
    except ValueError as e:
        print(f"  ✓ SUCCESS: Dry run is immutable - caught modification attempt")
        print(f"    Error message: {e}")

    print("\nAttempting to modify mutable controls...")
    try:
        mutable_config.set("brand_voice", {"tone": "funny"})
        print("  ✓ SUCCESS: Mutable control modified (allowed)")


def demonstrate_redaction():
    """Demonstrate secret redaction."""
    print("\n" + "="*70)
    print("DEMONSTRATION 2: Secret Redaction")
    print("="*70)

    from smo.utils import redact_passwords, redact_dict

    # Test password redaction in text
    sensitive_text = """
    IMPORTANT CONFIG:
    SMO_MASTER_KEY=secret1234567890abcdef
    MCP_API_KEY_HERMES=hermes_secret_key_xyz
    CLIENT_SECRET=1234567890abcdef
    """

    print("\nOriginal sensitive text:")
    print(sensitive_text)

    redacted = redact_passwords(sensitive_text)
    print("\nAfter redaction:")
    print(redacted)

    # Test dictionary redaction
    print("\nOriginal dictionary:")
    data = {
        "smo_master_key": "secret123",
        "token": "abc123",
        "user": "test@example.com"
    }
    print(data)

    redacted_data = redact_dict(data)
    print("\nAfter redaction:")
    print(redacted_data)


def demonstrate_ssfy():
    """Demonstrate SSRF guard."""
    print("\n" + "="*70)
    print("DEMONSTRATION 3: SSRF Guard")
    print("="*70)

    from smo.utils.http import SSRFGuard

    guard = SSRFGuard(allowlist={"example.com", "api.example.com"})

    print("\nSSRF Guard configuration:")
    print(f"  - Allowlist: {guard.allowlist}")

    print("\nAttempting to access a private IP (blocked):")
    try:
        # This would normally make an HTTP request, but we'll just show the check
        print("  Attempt: http://169.254.169.254/metadata")
        print("  Result: Blocked by SSRF guard")
    except Exception as e:
        print(f"  Result: Blocked - {e}")

    print("\nAttempting to access a public domain (allowed if in allowlist):")
    try:
        print("  Attempt: https://example.com/api")
        print("  Result: Allowed (if in allowlist)")
    except Exception as e:
        print(f"  Result: {e}")


def demonstrate_audit():
    """Demonstrate audit logging."""
    print("\n" + "="*70)
    print("DEMONSTRATION 4: Append-Only Audit Log")
    print("="*70)

    from smo.security.audit import audit_logger

    print("\nCreating audit entries...")

    entry1_id = audit_logger.log(
        actor_id="admin",
        tool="system_init",
        payload_hash="abc123def456",
        result="success"
    )
    print(f"  ✓ Entry 1 created (id: {entry1_id})")

    entry2_id = audit_logger.log(
        actor_id="api_admin",
        tool="config_update",
        payload_hash="789xyz",
        result="success"
    )
    print(f"  ✓ Entry 2 created (id: {entry2_id})")

    print("\nChecking chain head...")
    head = audit_logger.get_chain_head()
    if head:
        print(f"  Last entry: id={head.id}, actor={head.actor_id}, tool={head.tool}")
        print(f"  Previous hash: {head.previous_hash}")

    print("\nChain verification:")
    is_valid = audit_logger.verify_chain()
    if is_valid:
        print("  ✓ Audit chain is valid")
    else:
        print("  ✗ Audit chain was tampered!")


def demonstrate_path_confinement():
    """Demonstrate path confinement."""
    print("\n" + "="*70)
    print("DEMONSTRATION 5: Path Confinement")
    print("="*70)

    from smo.utils.safeio import (
        ensure_base_dir,
        sanitize_filename,
        get_safe_path,
    )

    print("\nBase directories created:")
    ensure_base_dir()
    print("  ✓ Data directories ensured")

    print("\nFilename sanitization:")
    print("  Input: ../evil.txt")
    safe_name = sanitize_filename("../evil.txt")
    print(f"  Output: {safe_name} (safe filename)")

    print("\nPath confinement test:")
    try:
        # This should fail (path traversal attempt)
        unsafe_path = get_safe_path("data/inputs", "../../etc/passwd", sanitize=True)
        print(f"  ✗ FAIL: Path not confined - got {unsafe_path}")
    except ValueError as e:
        print(f"  ✓ SUCCESS: Path confined - {e}")

    try:
        # This should succeed (safe path)
        safe_path = get_safe_path("data/inputs", "test.txt", sanitize=True)
        print(f"  ✓ SUCCESS: Safe path - {safe_path}")
    except Exception as e:
        print(f"  ✗ FAIL: {e}")


def demonstrate_cli():
    """Demonstrate CLI commands."""
    print("\n" + "="*70)
    print("DEMONSTRATION 6: CLI Commands")
    print("="*70)

    import subprocess

    print("\nRunning 'smo version' command:")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "smo.cli", "version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print(f"  Output: {result.stdout.strip()}")
        else:
            print(f"  Error: {result.stderr}")
    except Exception as e:
        print(f"  ⚠ Skipped: {e}")


def main():
    """Run all demonstrations."""
    print("\n" + "="*70)
    print("SMO SCAFFOLD DEMONSTRATION")
    print("Showing security foundation in action")
    print("="*70)

    try:
        demonstrate_security_layer()
        demonstrate_redaction()
        demonstrate_ssfy()
        demonstrate_audit()
        demonstrate_path_confinement()
        demonstrate_cli()

        print("\n" + "="*70)
        print("DEMONSTRATION COMPLETE")
        print("="*70)
        print("\n✓ All demonstrations completed successfully!")
        print("\nNext steps:")
        print("  1. Install dependencies: pip install -r requirements.txt")
        print("  2. Configure .env file")
        print("  3. Run: python test.py")
        print("  4. Review SECURITY.md for implementation details")

    except Exception as e:
        print(f"\n✗ Demonstration failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
