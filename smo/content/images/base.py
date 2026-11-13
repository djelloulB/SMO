"""Image provider interface (FR-08). Providers estimate cost before they generate."""
from dataclasses import dataclass
from typing import Protocol


class ImageError(Exception):
    """The image provider is not configured, refused the request, or returned unusable data."""


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    extension: str


class ImageProvider(Protocol):
    name: str

    def estimate_cost(self, size: str, quality: str) -> float: ...

    async def generate(self, prompt: str, size: str, quality: str) -> GeneratedImage: ...
