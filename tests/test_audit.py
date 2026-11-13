"""Append-only audit inventory + hash chain tests (REC-008)."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from smo.security.audit import AuditEntry, AuditLogger


def make_logger(tmp_path):
    return AuditLogger(str(tmp_path / "audit.db"))


def test_entries_are_chained(tmp_path):
    logger = make_logger(tmp_path)

    first = logger.log("actor", "tool_a", "hash-a")
    second = logger.log("actor", "tool_b", "hash-b")

    assert first > 0
    assert second > first
    head = logger.get_chain_head()
    assert head.payload_hash == "hash-b"
    assert logger.verify_chain() is True


def test_chain_links_to_explicit_previous_hash(tmp_path):
    logger = make_logger(tmp_path)

    logger.log("actor", "tool", "hash-a", previous_hash=None)
    logger.log("actor", "tool", "hash-b", previous_hash="hash-a")

    assert logger.verify_chain() is True


def test_chain_detects_tampering(tmp_path):
    logger = make_logger(tmp_path)
    logger.log("actor", "tool", "hash-a")

    with logger.SessionLocal() as session:
        session.add(
            AuditEntry(
                actor_id="attacker",
                tool="tamper",
                payload_hash="hash-b",
                previous_hash="not-the-real-hash",
                result="success",
            )
        )
        session.commit()

    assert logger.verify_chain() is False


def test_legacy_entries_are_tolerated_before_chain_start(tmp_path):
    logger = make_logger(tmp_path)

    with logger.SessionLocal() as session:
        session.add(AuditEntry(actor_id="legacy", tool="old", payload_hash="old-hash", result="success"))
        session.add(AuditEntry(actor_id="legacy", tool="old2", payload_hash="old-hash-2", result="success"))
        session.commit()

    logger.log("actor", "new", "new-hash")

    assert logger.verify_chain() is True


def test_audit_table_is_append_only(tmp_path):
    logger = make_logger(tmp_path)
    logger.log("actor", "tool", "hash-a")

    with logger.engine.begin() as conn:
        with pytest.raises(DatabaseError):
            conn.execute(text("UPDATE audit_actions SET result = 'tampered'"))

    with logger.engine.begin() as conn:
        with pytest.raises(DatabaseError):
            conn.execute(text("DELETE FROM audit_actions"))
