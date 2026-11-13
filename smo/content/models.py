"""Content domain models (canonical draft + per-platform variants)."""
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class SourceItem:
    """A single item fetched from a content source."""

    title: str
    summary: str = ""
    link: Optional[str] = None
    source: str = ""
    published: Optional[str] = None


class PlatformVariant(BaseModel):
    """Rendered text for a specific platform."""

    platform: str
    text: str
    media_ref: Optional[str] = None


class ContentPiece(BaseModel):
    """Canonical content plus its platform variants."""

    source: Optional[str] = None
    canonical_draft: str
    status: str = "draft"
    variants: list[PlatformVariant] = Field(default_factory=list)


@dataclass
class ContentRecord:
    """Detached, session-independent view of a stored content item."""

    id: int
    source: Optional[str]
    text: str
    status: str
    created_at: Optional[object]
    platform: Optional[str] = None
    variant_text: Optional[str] = None
    media_ref: Optional[str] = None
    published_at: Optional[object] = None
    extra: dict = field(default_factory=dict)
