"""Content use-cases: ingest, list, approve, reject (shared CLI/API/MCP)."""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ..content.models import ContentRecord, SourceItem
from ..content.moderation import ModerationResult, moderate
from ..content.pipeline import build_content_piece
from ..memory.sqlite_store import ContentItem, SQLiteStore, Variant

logger = logging.getLogger(__name__)


class ContentError(Exception):
    """Base class for content workflow errors."""


class DuplicateContentError(ContentError):
    """The source item has already been ingested."""


class InvalidTransition(ContentError):
    """The requested status transition is not allowed."""


class ModerationError(ContentError):
    """The content failed the moderation gate."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class IngestResult:
    record: Optional[ContentRecord]
    moderation: ModerationResult
    duplicate: bool = False


def source_key(item: SourceItem) -> str:
    """Stable dedupe key for a source item."""
    if item.link and item.link.strip():
        return item.link.strip()[:256]
    return f"{item.source}#{item.title}".strip()[:256]


def _to_record(item: ContentItem, variant: Optional[Variant]) -> ContentRecord:
    return ContentRecord(
        id=item.id,
        source=item.source,
        text=item.canonical_draft,
        status=item.status,
        created_at=item.created_at,
        platform=variant.platform if variant else None,
        variant_text=variant.text if variant else None,
        media_ref=variant.media_ref if variant else None,
        published_at=item.published_at,
    )


class ContentService:
    """CRUD + workflow state machine for content items."""

    def __init__(self, store: Optional[SQLiteStore] = None):
        self.store = store or SQLiteStore()

    # --- Queries ---
    def get(self, content_id: int) -> Optional[ContentRecord]:
        with self.store.get_session() as session:
            row = self._row(session, content_id)
            if row is None:
                return None
            return _to_record(*row)

    def list(self, status: Optional[str] = None, limit: int = 50) -> list[ContentRecord]:
        with self.store.get_session() as session:
            query = session.query(ContentItem, Variant).outerjoin(
                Variant, Variant.content_id == ContentItem.id
            )
            if status:
                query = query.filter(ContentItem.status == status)
            rows = (
                query.order_by(ContentItem.id.desc())
                .limit(limit)
                .all()
            )
            return [_to_record(item, variant) for item, variant in rows]

    # --- Ingest ---
    def ingest_item(self, item: SourceItem, platform: str = "linkedin") -> IngestResult:
        key = source_key(item)
        text = build_content_piece(item, platform).canonical_draft
        moderation = moderate(text, platform)

        with self.store.get_session() as session:
            existing = (
                session.query(ContentItem)
                .filter(ContentItem.source == key)
                .first()
            )
            if existing is not None:
                return IngestResult(record=None, moderation=moderation, duplicate=True)

            status = "draft" if moderation.allowed else "rejected"
            content = ContentItem(source=key, canonical_draft=text, status=status)
            session.add(content)
            session.flush()

            variant = Variant(content_id=content.id, platform=platform, text=text)
            session.add(variant)
            session.commit()

            record = _to_record(content, variant)

        logger.info(
            "Ingested content id=%s source=%s status=%s", record.id, key, record.status
        )
        return IngestResult(record=record, moderation=moderation)

    def create_draft(self, text: str, source: Optional[str] = None, platform: str = "linkedin") -> IngestResult:
        item = SourceItem(title=text, source=source or "manual", link=source)
        return self.ingest_item(item, platform)

    def add_generated(self, text: str, platform: str = "linkedin") -> IngestResult:
        """Store generated text as a draft (moderated like any ingested item). Each call is a new item."""
        moderation = moderate(text, platform)
        key = f"generated:{uuid.uuid4().hex}"
        status = "draft" if moderation.allowed else "rejected"
        with self.store.get_session() as session:
            content = ContentItem(source=key, canonical_draft=text, status=status)
            session.add(content)
            session.flush()
            variant = Variant(content_id=content.id, platform=platform, text=text)
            session.add(variant)
            session.commit()
            record = _to_record(content, variant)
        logger.info("Generated content id=%s platform=%s status=%s", record.id, platform, status)
        return IngestResult(record=record, moderation=moderation)

    # --- Workflow ---
    def approve(self, content_id: int) -> ContentRecord:
        with self.store.get_session() as session:
            row = self._row(session, content_id)
            if row is None:
                raise ContentError(f"content {content_id} not found")
            item, variant = row

            if item.status != "draft":
                raise InvalidTransition(
                    f"content {content_id} is '{item.status}', expected 'draft'"
                )

            moderation = moderate(item.canonical_draft, variant.platform if variant else "linkedin")
            if not moderation.allowed:
                raise ModerationError(moderation.reasons)

            item.status = "approved"
            session.commit()
            record = _to_record(item, variant)

        logger.info("Approved content id=%s", content_id)
        return record

    def reject(self, content_id: int) -> ContentRecord:
        with self.store.get_session() as session:
            row = self._row(session, content_id)
            if row is None:
                raise ContentError(f"content {content_id} not found")
            item, variant = row
            if item.status in {"published"}:
                raise InvalidTransition(
                    f"content {content_id} is already '{item.status}'"
                )
            item.status = "rejected"
            session.commit()
            record = _to_record(item, variant)

        logger.info("Rejected content id=%s", content_id)
        return record

    def mark_scheduled(self, content_id: int) -> None:
        with self.store.get_session() as session:
            item = session.get(ContentItem, content_id)
            if item is None:
                raise ContentError(f"content {content_id} not found")
            if item.status != "approved":
                raise InvalidTransition(
                    f"content {content_id} is '{item.status}', expected 'approved'"
                )
            item.status = "scheduled"
            session.commit()

    def mark_published(self, content_id: int) -> None:
        with self.store.get_session() as session:
            item = session.get(ContentItem, content_id)
            if item is None:
                raise ContentError(f"content {content_id} not found")
            item.status = "published"
            item.published_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()

    def set_media_ref(self, content_id: int, media_ref: str) -> None:
        with self.store.get_session() as session:
            variant = session.query(Variant).filter(Variant.content_id == content_id).first()
            if variant is None:
                raise ContentError(f"content {content_id} has no variant")
            variant.media_ref = media_ref[:512]
            session.commit()

    def set_external_id(self, content_id: int, external_id: Optional[str]) -> None:
        if not external_id:
            return
        with self.store.get_session() as session:
            variant = session.query(Variant).filter(Variant.content_id == content_id).first()
            if variant is not None:
                variant.external_id = external_id
                session.commit()

    def list_published(self, limit: int = 100) -> List[Tuple[int, str, str]]:
        """(content_id, platform, external_id) for published items that have a platform post id."""
        with self.store.get_session() as session:
            rows = (
                session.query(ContentItem.id, Variant.platform, Variant.external_id)
                .join(Variant, Variant.content_id == ContentItem.id)
                .filter(ContentItem.status == "published", Variant.external_id.isnot(None))
                .order_by(ContentItem.id.desc())
                .limit(limit)
                .all()
            )
            return [(row[0], row[1], row[2]) for row in rows]

    def mark_failed(self, content_id: int) -> None:
        with self.store.get_session() as session:
            item = session.get(ContentItem, content_id)
            if item is None:
                raise ContentError(f"content {content_id} not found")
            item.status = "failed"
            session.commit()

    @staticmethod
    def _row(session, content_id: int):
        return (
            session.query(ContentItem, Variant)
            .outerjoin(Variant, Variant.content_id == ContentItem.id)
            .filter(ContentItem.id == content_id)
            .first()
        )
