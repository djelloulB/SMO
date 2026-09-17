"""Immutable security layer (REC-002) with enforcement of immutable controls."""
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecurityLayer:
    """Frozen security controls per REC-002."""
    dry_run: bool
    human_gate: bool
    budget_per_day_usd: float
    mcp_scopes_read: frozenset[str]
    mcp_scopes_content: frozenset[str]
    mcp_scopes_control: frozenset[str]
    mcp_scopes_admin: frozenset[str]


@dataclass
class MutableConfig:
    """Mutable settings allowed to change at runtime."""

    def __post_init__(self):
        from .settings import settings
        self.brand_voice = dict(settings.brand_voice)
        self.accounts = dict(settings.accounts)
        self.sources = list(settings.sources)
        self.mcp_enabled = settings.mcp_enabled
        self.mcp_transport = list(settings.mcp_transport)
        self.mcp_http_requires_key = settings.mcp_http_requires_key
        self.mcp_trusted_proxy = settings.mcp_trusted_proxy

    def can_modify(self, key: str) -> bool:
        mutable = {"brand_voice", "accounts", "sources", "mcp_enabled", "mcp_transport", "mcp_http_requires_key", "mcp_trusted_proxy"}
        return key in mutable

    def set(self, key: str, value):
        if not self.can_modify(key):
            raise ValueError(f"Cannot modify immutable control: {key} (REC-002)")

        if key == "brand_voice" and isinstance(value, dict):
            self.brand_voice = value
        elif key == "accounts" and isinstance(value, dict):
            self.accounts = value
        elif key == "sources" and isinstance(value, list):
            self.sources = value
        elif key == "mcp_enabled" and isinstance(value, bool):
            self.mcp_enabled = value
        elif key == "mcp_transport" and isinstance(value, list) and all(t in {"stdio", "http"} for t in value):
            self.mcp_transport = value
        elif key == "mcp_http_requires_key" and isinstance(value, bool):
            self.mcp_http_requires_key = value
        elif key == "mcp_trusted_proxy" and (value is None or isinstance(value, str)):
            self.mcp_trusted_proxy = value
        else:
            raise ValueError(f"Unexpected value type for {key}")
        logger.info("Config %s updated", key)


security_layer = SecurityLayer(
    dry_run=True,
    human_gate=True,
    budget_per_day_usd=1.5,
    mcp_scopes_read=frozenset({"read"}),
    mcp_scopes_content=frozenset({"content"}),
    mcp_scopes_control=frozenset(),
    mcp_scopes_admin=frozenset(),
)

mutable_config = MutableConfig()
