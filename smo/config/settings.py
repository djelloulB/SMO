"""Configuration management with security enforcement (REC-001, REC-002)."""
import base64
import os
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ so that modules using os.getenv() (connectors, stores)
# see the same configuration as pydantic-settings. Existing env vars win.
load_dotenv()


class Settings(BaseSettings):
    app_name: str = "SMO"
    app_version: str = Field(default="0.1.0")
    public_base_url: str = Field(default="http://localhost:8023")
    llm_default: Literal["ollama/llama3.1", "openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"] = "ollama/llama3.1"
    llm_providers: dict = Field(default_factory=dict)
    dry_run: bool = True
    human_gate: bool = True
    budget_per_day_usd: float = Field(default=1.5, ge=0, le=100)
    brand_voice: dict = Field(default_factory=dict)
    brand_voice_tone: Optional[str] = None
    brand_voice_emojis: bool = False
    brand_voice_banned_topics: list[str] = Field(default_factory=list)
    accounts: dict = Field(default_factory=dict)
    sources: list = Field(default_factory=list)
    comment_access: bool = False
    mcp_enabled: bool = True
    mcp_transport: list[str] = Field(default_factory=lambda: ["stdio", "http"])
    mcp_http_requires_key: bool = True
    mcp_trusted_proxy: Optional[str] = None
    smo_master_key: Optional[str] = None
    smo_master_key_path: Optional[str] = None
    smo_dashboard_token: Optional[str] = Field(
        default=None,
        description="Token required for dashboard mutations (approve/reject). Unset disables them.",
    )
    oauth_state_secret: str = Field(
        default_factory=lambda: os.urandom(32).hex(),
        description="Secret for CSRF state validation (REC-004)"
    )
    rate_limit_token_bucket_capacity: int = Field(default=100, ge=1)
    rate_limit_token_bucket_rate: float = Field(default=1.0, ge=0.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def uses_key_file(self) -> bool:
        return bool(self.smo_master_key_path and not self.smo_master_key)


settings = Settings()


def load_master_key() -> bytes:
    """Load the Fernet master key (from env or file)."""
    key_env = os.getenv("SMO_MASTER_KEY")
    if key_env:
        # Decode from base64
        key = base64.urlsafe_b64decode(key_env)
    elif settings.uses_key_file:
        key_path = settings.smo_master_key_path
        try:
            with open(key_path, "rb") as f:
                key = f.read()
        except FileNotFoundError:
            raise RuntimeError(f"Master key file not found: {key_path}")
    else:
        raise RuntimeError("SMO_MASTER_KEY env var not set and smo_master_key_path not configured.")

    if len(key) != 32:
        raise RuntimeError("Master key must be 32 bytes (base64-encoded)")

    return key
