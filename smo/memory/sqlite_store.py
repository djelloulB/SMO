"""
SQLite store for persistence (REC-008 audit).
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session, sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for ORM models."""


class Account(Base):
    """Account metadata."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    platform = Column(String(64), nullable=False)
    handle = Column(String(256), nullable=False)
    label = Column(String(256), nullable=True)
    profile_id = Column(String(256), nullable=True)
    status = Column(String(32), default="active")  # active, paused, failed
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OAuthToken(Base):
    """
    Encrypted OAuth tokens (REC-001).
    """
    __tablename__ = "oauth_tokens"

    id = Column(Integer, primary_key=True)
    platform = Column(String(64), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    encrypted_token = Column(String(1024), nullable=False)  # Fernet ciphertext
    scopes = Column(String(256), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    refresh_metadata = Column(String(512), nullable=True)  # JSON of refresh config
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    account = relationship("Account")


class ContentItem(Base):
    """Content items."""
    __tablename__ = "content_items"

    id = Column(Integer, primary_key=True)
    source = Column(String(256), nullable=True)
    canonical_draft = Column(String(10000), nullable=False)  # Store full text
    status = Column(String(32), default="draft")  # draft, approved, scheduled, published, failed
    created_at = Column(DateTime, default=datetime.now)
    approved_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)


class Variant(Base):
    """Platform variants."""
    __tablename__ = "variants"

    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("content_items.id"), nullable=False)
    platform = Column(String(64), nullable=False)
    text = Column(String(10000), nullable=False)
    media_ref = Column(String(512), nullable=True)
    external_id = Column(String(256), nullable=True)  # platform post id, set on publication
    created_at = Column(DateTime, default=datetime.now)

    content = relationship("ContentItem")


class Job(Base):
    """Scheduled jobs."""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("content_items.id"), nullable=False)
    platform = Column(String(64), nullable=False)
    status = Column(String(32), default="pending")  # pending, running, completed, failed, dead_letter
    retry_count = Column(Integer, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    dead_letter = Column(Integer, default=0)  # 0 = not dead letter, 1 = dead letter
    created_at = Column(DateTime, default=datetime.now)
    scheduled_at = Column(DateTime, nullable=True)
    run_at = Column(DateTime, nullable=True)

    account_id = Column(String(256), nullable=True)
    options = Column(String(1024), nullable=True)  # JSON: organization_id, author_urn, visibility
    last_error = Column(String(256), nullable=True)  # error type only, never a message that may hold secrets

    content = relationship("ContentItem")


class PlatformControl(Base):
    """Per-platform pause state and circuit breaker (FR-11)."""
    __tablename__ = "platform_controls"

    platform = Column(String(64), primary_key=True)
    status = Column(String(32), default="active", nullable=False)  # active, paused (manual)
    consecutive_failures = Column(Integer, default=0, nullable=False)
    paused_until = Column(DateTime, nullable=True)  # circuit breaker cooldown end (naive UTC)
    reason = Column(String(256), nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class PlatformToken(Base):
    """Encrypted OAuth tokens for platforms that store them in SQLite (Fernet, REC-001)."""
    __tablename__ = "platform_tokens"

    platform = Column(String(32), primary_key=True)
    account_id = Column(String(256), primary_key=True)
    encrypted_token = Column(String(4096), nullable=False)
    version = Column(Integer, nullable=False, default=0)  # incremented on each write (compare-and-swap)
    refresh_lease_until = Column(DateTime, nullable=True)  # set while one process refreshes the token
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class OAuthPending(Base):
    """Authorization requests waiting for their callback (one-time state, REC-004)."""
    __tablename__ = "oauth_pending"

    state = Column(String(128), primary_key=True)
    platform = Column(String(32), nullable=False)
    code_verifier = Column(String(256), nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class SpendEntry(Base):
    """Paid operations counted against budget_per_day_usd (REC-002). One row per paid call."""
    __tablename__ = "spend_ledger"

    id = Column(Integer, primary_key=True)
    day = Column(String(10), nullable=False, index=True)  # UTC date, YYYY-MM-DD
    kind = Column(String(32), nullable=False)  # image, ...
    provider = Column(String(64), nullable=False)
    amount_usd = Column(Float, nullable=False)
    content_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class RuntimeConfig(Base):
    """Mutable settings changed at runtime through set_config (JSON values, REC-002 applies)."""
    __tablename__ = "runtime_config"

    key = Column(String(64), primary_key=True)
    value = Column(String(8192), nullable=False)  # JSON
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class MetricSnapshot(Base):
    """Metrics snapshots (read-only)."""
    __tablename__ = "metric_snapshots"

    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("content_items.id"), nullable=False)
    platform = Column(String(64), nullable=False)
    likes = Column(Integer, default=0)
    views = Column(Integer, default=0)
    reach = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.now)

    content = relationship("ContentItem")


class FeedbackEvent(Base):
    """Feedback events for few-shot learning."""
    __tablename__ = "feedback_events"

    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("content_items.id"), nullable=False)
    platform = Column(String(64), nullable=False)
    outcome = Column(String(32), nullable=False)  # high_engagement, low_engagement
    created_at = Column(DateTime, default=datetime.now)

    content = relationship("ContentItem")


class LinkedInTokenStore(Base):
    """LinkedIn OAuth tokens (separate table for PKCE flow)."""
    __tablename__ = "linkedin_tokens"

    id = Column(Integer, primary_key=True)
    account_id = Column(String(64), nullable=False, unique=True)  # LinkedIn person URN
    encrypted_token = Column(String(2048), nullable=False)  # Fernet ciphertext
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SQLiteStore:
    """
    SQLite persistence layer.
    Includes audit table (already in security/audit.py for simplicity).
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize SQLite store.

        Args:
            db_path: Path to SQLite database (WAL mode). Defaults to the
                ``SMO_DB_PATH`` environment variable, then ``data/smoomo.db``.
        """
        self.db_path = Path(db_path or os.getenv("SMO_DB_PATH", "data/smoomo.db"))
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self._init_database()

    def _init_database(self) -> None:
        """Create tables and enable WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)
        self._add_missing_columns(
            "jobs",
            {
                "account_id": "VARCHAR(256)",
                "options": "VARCHAR(1024)",
                "last_error": "VARCHAR(256)",
            },
        )
        self._add_missing_columns("variants", {"external_id": "VARCHAR(256)"})
        self._add_missing_columns("accounts", {"label": "VARCHAR(256)"})
        self._add_missing_columns(
            "platform_tokens",
            {"version": "INTEGER NOT NULL DEFAULT 0", "refresh_lease_until": "DATETIME"},
        )

        # Enable WAL mode for better concurrency
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def get_session(self) -> Session:
        """Get a database session."""
        return self.SessionLocal()

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        """Add columns introduced after a table was first created (create_all does not alter tables)."""
        with self.engine.begin() as conn:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    # Convenience methods
    def create_account(self, platform: str, handle: str) -> Account:
        """Create a new account."""
        with self.get_session() as session:
            account = Account(platform=platform, handle=handle, status="active")
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    def get_account(self, platform: str, handle: str) -> Optional[Account]:
        """Get account by platform and handle."""
        with self.get_session() as session:
            return (
                session.query(Account)
                .filter_by(platform=platform, handle=handle)
                .first()
            )
