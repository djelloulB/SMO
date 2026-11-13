"""Use-case: illustrate a draft with a generated image (FR-08).

Order of operations: check the content, reserve the estimated cost against the
daily budget, generate, store the file under data/media, then link it to the
content item. A failed generation releases its reservation.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..content.images.base import ImageError, ImageProvider
from ..utils.safeio import BASE_OUTPUT_DIR, ensure_safe_write, sanitize_filename
from .budget_service import BudgetService
from .content_service import ContentError, ContentService

logger = logging.getLogger(__name__)

ALLOWED_SIZES = frozenset({"1024x1024"})
ALLOWED_QUALITIES = frozenset({"low", "medium", "high"})
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "low"
PROMPT_TEXT_LIMIT = 500


@dataclass
class ImageReceipt:
    content_id: int
    path: str
    cost_usd: float
    remaining_today_usd: float


def build_image_prompt(post_text: str) -> str:
    topic = " ".join(post_text.split())[:PROMPT_TEXT_LIMIT]
    return (
        "Editorial illustration for a social media post. "
        "No text, letters, logos or watermarks in the image. "
        f"Subject: {topic}"
    )


class ImageService:
    def __init__(
        self,
        provider: ImageProvider,
        budget: BudgetService,
        content: ContentService,
        output_dir: Optional[Path] = None,
    ):
        self.provider = provider
        self.budget = budget
        self.content = content
        self.output_dir = Path(output_dir) if output_dir else BASE_OUTPUT_DIR

    async def generate_for_content(
        self,
        content_id: int,
        size: str = DEFAULT_SIZE,
        quality: str = DEFAULT_QUALITY,
        prompt: Optional[str] = None,
    ) -> ImageReceipt:
        if size not in ALLOWED_SIZES:
            raise ImageError(f"unsupported size '{size}' (allowed: {', '.join(sorted(ALLOWED_SIZES))})")
        if quality not in ALLOWED_QUALITIES:
            raise ImageError(f"unsupported quality '{quality}' (allowed: low, medium, high)")

        record = self.content.get(content_id)
        if record is None:
            raise ContentError(f"content {content_id} not found")
        text = prompt or build_image_prompt(record.variant_text or record.text)

        cost = self.provider.estimate_cost(size, quality)
        reservation = self.budget.reserve("image", self.provider.name, cost, content_id)
        try:
            image = await self.provider.generate(text, size, quality)
        except Exception:
            self.budget.release(reservation)
            raise

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.output_dir / sanitize_filename(f"content-{content_id}-{timestamp}.{image.extension}")
        ensure_safe_write(path, image.data)
        self.content.set_media_ref(content_id, str(path))
        logger.info("Generated image for content %s (%s, $%.4f)", content_id, self.provider.name, cost)
        return ImageReceipt(
            content_id=content_id,
            path=str(path),
            cost_usd=cost,
            remaining_today_usd=self.budget.remaining_today(),
        )
