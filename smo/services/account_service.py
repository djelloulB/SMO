"""Account registry: several accounts per platform (FR-01..FR-04, Phase 3).

Tokens are already keyed by an external account id (LinkedIn person URN, X user
id, WordPress site). This service stores the metadata the rest of SMO needs — a
human label, the platform profile id and an active/paused status — and lets
publishing and scheduling refuse unknown or paused accounts once a platform has
a registry. Accounts are registered automatically by the OAuth callbacks and can
be added manually with ``smo accounts-add`` (WordPress, legacy connections).
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..config.security import mutable_config
from ..config.settings import settings
from ..memory.sqlite_store import (
    Account,
    LinkedInTokenStore,
    PlatformToken,
    SQLiteStore,
)
from .config_service import ConfigService
from .content_service import ContentError

logger = logging.getLogger(__name__)

ACCOUNT_STATUSES = frozenset({"active", "paused", "failed"})


class AccountError(ContentError):
    """Unknown or paused account."""


@dataclass(frozen=True)
class AccountInfo:
    platform: str
    account_id: str
    label: Optional[str] = None
    profile_id: Optional[str] = None
    status: str = "active"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _to_info(row: Account) -> AccountInfo:
    return AccountInfo(
        platform=row.platform,
        account_id=row.handle,
        label=row.label,
        profile_id=row.profile_id,
        status=row.status or "active",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def scheduling_defaults(platform: str, store: Optional[SQLiteStore] = None) -> dict:
    """Per-platform scheduling defaults from the mutable ``accounts`` config.

    The runtime override (``set_config("accounts", ...)``) wins over ``.env``,
    and ``.env`` wins over nothing. Example (``ACCOUNTS`` JSON in ``.env``)::

        {"linkedin": {"scheduling": {"strategy": "weighted_by_engagement",
                                     "timezone": "Europe/Paris"}}}
    """
    config = ConfigService(store).get("accounts") if store is not None else None
    if not isinstance(config, dict):
        config = getattr(mutable_config, "accounts", None) or settings.accounts or {}
    platform_config = config.get(platform) or {}
    if not isinstance(platform_config, dict):
        return {}
    scheduling = platform_config.get("scheduling") or {}
    return dict(scheduling) if isinstance(scheduling, dict) else {}


class AccountService:
    """Registry of platform accounts, backed by the ``accounts`` table."""

    def __init__(self, store: Optional[SQLiteStore] = None):
        self.store = store or SQLiteStore()

    def register(
        self,
        platform: str,
        account_id: str,
        label: Optional[str] = None,
        profile_id: Optional[str] = None,
        status: str = "active",
    ) -> AccountInfo:
        """Create or update an account (idempotent, keyed by platform + account id)."""
        platform = (platform or "").strip().lower()
        account_id = (account_id or "").strip()
        if not platform:
            raise AccountError("platform is required")
        if not account_id:
            raise AccountError("account id is required")
        if status not in ACCOUNT_STATUSES:
            raise AccountError(f"unknown account status '{status}'")

        with self.store.get_session() as session:
            row = (
                session.query(Account)
                .filter_by(platform=platform, handle=account_id)
                .first()
            )
            if row is None:
                row = Account(platform=platform, handle=account_id)
                session.add(row)
            if label is not None:
                row.label = label
            if profile_id is not None:
                row.profile_id = profile_id
            row.status = status
            session.commit()
            session.refresh(row)
            info = _to_info(row)

        logger.info("Registered %s account %s (status=%s)", platform, account_id, status)
        return info

    def get(self, platform: str, account_id: str) -> Optional[AccountInfo]:
        with self.store.get_session() as session:
            row = (
                session.query(Account)
                .filter_by(platform=platform, handle=account_id)
                .first()
            )
            return _to_info(row) if row is not None else None

    def list(self, platform: Optional[str] = None) -> list[AccountInfo]:
        with self.store.get_session() as session:
            query = session.query(Account)
            if platform:
                query = query.filter(Account.platform == platform)
            rows = query.order_by(Account.platform, Account.handle).all()
            return [_to_info(row) for row in rows]

    def set_status(self, platform: str, account_id: str, status: str) -> AccountInfo:
        if status not in ACCOUNT_STATUSES:
            raise AccountError(f"unknown account status '{status}'")
        with self.store.get_session() as session:
            row = (
                session.query(Account)
                .filter_by(platform=platform, handle=account_id)
                .first()
            )
            if row is None:
                raise AccountError(f"unknown {platform} account '{account_id}'")
            row.status = status
            session.commit()
            session.refresh(row)
            info = _to_info(row)
        logger.info("Account %s/%s is now %s", platform, account_id, status)
        return info

    def require_active(self, platform: str, account_id: str) -> Optional[AccountInfo]:
        """Refuse paused accounts, and unknown ids once the platform has a registry.

        A platform with no registered account keeps working as before (legacy
        connections and dry-run tests do not need to register anything).
        """
        info = self.get(platform, account_id)
        if info is None:
            if self.list(platform):
                raise AccountError(
                    f"unknown {platform} account '{account_id}'; "
                    "list registered accounts first (list_accounts / smo accounts)"
                )
            return None
        if info.status != "active":
            raise AccountError(
                f"{platform} account '{account_id}' is {info.status}; "
                "resume it before publishing or scheduling"
            )
        return info

    def connected(self, platform: str, account_id: str) -> bool:
        """Whether usable credentials exist for this account."""
        if platform == "linkedin":
            with self.store.get_session() as session:
                return (
                    session.query(LinkedInTokenStore)
                    .filter_by(account_id=account_id)
                    .first()
                    is not None
                )
        if platform == "x":
            with self.store.get_session() as session:
                return (
                    session.query(PlatformToken)
                    .filter_by(platform="x", account_id=account_id)
                    .first()
                    is not None
                )
        if platform == "wordpress":
            from ..connectors.wordpress import WordPressConnector

            return WordPressConnector.from_environment() is not None
        return False
