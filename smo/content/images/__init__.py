"""Image providers (FR-08): OpenAI GPT Image models for now; Stability and local Stable Diffusion later."""
import os

from .base import GeneratedImage, ImageError, ImageProvider
from .openai_images import OpenAIImageProvider


def get_image_provider() -> ImageProvider:
    provider = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()
    if provider != "openai":
        raise ImageError(f"image provider '{provider}' is not wired yet; use openai")
    return OpenAIImageProvider.from_environment()


__all__ = ["GeneratedImage", "ImageError", "ImageProvider", "OpenAIImageProvider", "get_image_provider"]
