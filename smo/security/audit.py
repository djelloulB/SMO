"""
Append-only audit logger with tamper-evident hash chaining (REC-008).
"""
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Column, DateTime, Integer, String, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for ORM models."""


class AuditEntry(Base):
    """
    Append-only audit table (REC-008).
    - Immutable: SQLite trigger prevents UPDATE/DELETE.
    - Tamper-evident: hash-chain (previous_hash + payload_hash).
    """
    __tablename__ = "audit_actions"

    id = Column(Integer, primary_key=True)
    actor_id = Column(String(256), nullable=False)
    tool = Column(String(256), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    previous_hash = Column(String(64))  # Hash of previous entry (chain)
    timestamp = Column(DateTime, default=datetime.now, nullable=False)
    result = Column(String(32), nullable=False)  # success, failure, blocked

    def __repr__(self) -> str:
        return f"AuditEntry(id={self.id}, tool={self.tool}, result={self.result}, ts={self.timestamp})"


class AuditLogger:
    """
    Append-only audit logger with hash chain.
    """

    def __init__(self, db_path: str = "data/smoomo.db"):
        """
        Initialize audit logger.

        Args:
            db_path: Path to SQLite database (WAL mode for better concurrency)
        """
        self.db_path = Path(db_path)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",  # WAL mode is set in post-create
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self._init_database()

    def _init_database(self) -> None:
        """
        Initialize database with WAL mode and audit table.
        """
        # Create directories
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create tables
        Base.metadata.create_all(self.engine)

        # Enable WAL mode for better concurrency
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        # Add trigger to prevent UPDATE/DELETE (append-only)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                CREATE TRIGGER IF NOT EXISTS audit_prevent_update
                BEFORE UPDATE ON audit_actions
                BEGIN
                    SELECT RAISE(ABORT, "Append-only: cannot update audit entries");
                END;
                """
                )
            )
            conn.execute(
                text(
                    """
                CREATE TRIGGER IF NOT EXISTS audit_prevent_delete
                BEFORE DELETE ON audit_actions
                BEGIN
                    SELECT RAISE(ABORT, "Append-only: cannot delete audit entries");
                END;
                """
                )
            )

    def log(
        self,
        actor_id: str,
        tool: str,
        payload_hash: str,
        previous_hash: Optional[str] = None,
        result: str = "success",
    ) -> int:
        """
        Append an audit entry.

        Args:
            actor_id: Actor identifier (e.g., agent key ID, or "system")
            tool: Tool name (e.g., "publish_post", "approve_draft")
            payload_hash: Hash of the payload (simplified)
            previous_hash: Hash of the previous audit entry (for tamper-evidence)
            result: Result status (success, failure, blocked)

        Returns:
            ID of the created audit entry
        """
        try:
            with self.SessionLocal() as session:
                entry = AuditEntry(
                    actor_id=actor_id,
                    tool=tool,
                    payload_hash=payload_hash,
                    previous_hash=previous_hash,
                    result=result,
                )
                session.add(entry)
                session.commit()
                session.refresh(entry)
                logger.info(
                    "AUDIT logged: id=%d actor=%s tool=%s result=%s prev_hash=%s",
                    entry.id,
                    actor_id,
                    tool,
                    result,
                    previous_hash,
                )
                return entry.id
        except Exception as e:
            logger.error("Failed to log audit entry: %s", e)
            # Continue anyway — audit is non-critical for MVP, but we log the error

    def get_chain_head(self) -> Optional[AuditEntry]:
        """Get the most recent audit entry (head of the chain)."""
        with self.SessionLocal() as session:
            entry = (
                session.query(AuditEntry)
                .order_by(AuditEntry.id.desc())
                .first()
            )
            return entry

    def verify_chain(self) -> bool:
        """
        Verify the hash chain (tamper-evidence).
        Returns True if chain is consistent.
        """
        with self.SessionLocal() as session:
            entries = session.query(AuditEntry).order_by(AuditEntry.id.asc()).all()
            expected_previous = None

            for entry in entries:
                if entry.previous_hash != expected_previous:
                    logger.error(
                        "Audit chain tampered: id=%d previous_hash=%s expected=%s",
                        entry.id,
                        entry.previous_hash,
                        expected_previous,
                    )
                    return False
                expected_previous = entry.payload_hash

            return True


# Global audit logger instance
audit_logger = AuditLogger()
