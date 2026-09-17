"""
SQLite store for persistence (REC-008 audit).
"""
import logging
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

    content = relationship("ContentItem")


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

    def __init__(self, db_path: str = "data/smoomo.db"):
        """
        Initialize SQLite store.

        Args:
            db_path: Path to SQLite database (WAL mode)
        """
        self.db_path = Path(db_path)
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
