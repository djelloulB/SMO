"""OpenAI image generation (GPT Image models) behind the ImageProvider interface.

The Images API returns base64 data (``b64_json``); DALL·E models are retired.
Prices are the per-image rates OpenAI publishes for 1024x1024 output. They are
estimates used for the daily budget: re-check them against the live pricing page.
"""
import base64
import binascii
import logging
import os
from typing import Optional

import httpx

from .base import GeneratedImage, ImageError

logger = logging.getLogger(__name__)

API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-2"
REQUEST_TIMEOUT = 180.0

# (model, size, quality) -> USD per image
PRICE_USD: dict[tuple[str, str, str], float] = {
    ("gpt-image-2", "1024x1024", "low"): 0.006,
    ("gpt-image-2", "1024x1024", "medium"): 0.053,
    ("gpt-image-2", "1024x1024", "high"): 0.211,
}


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, api_key: str, model: Optional[str] = None, base_url: str = API_BASE):
        if not api_key:
            raise ImageError("OPENAI_API_KEY is not set")
        self._api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_environment(cls) -> "OpenAIImageProvider":
        return cls(os.getenv("OPENAI_API_KEY", "").strip(), os.getenv("IMAGE_MODEL", "").strip() or None)

    def estimate_cost(self, size: str, quality: str) -> float:
        key = (self.model, size, quality)
        if key not in PRICE_USD:
            raise ImageError(f"no price configured for {self.model} {size} {quality}")
        return PRICE_USD[key]

    async def generate(self, prompt: str, size: str, quality: str) -> GeneratedImage:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": 1,
            "output_format": "png",
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{self.base_url}/images/generations",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        if response.status_code >= 400:
            # Never echo the provider body: it can contain request details.
            raise ImageError(f"OpenAI image request failed: HTTP {response.status_code}")
        try:
            encoded = response.json()["data"][0]["b64_json"]
            data = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
            raise ImageError("OpenAI returned no usable image data") from exc
        if not data:
            raise ImageError("OpenAI returned an empty image")
        return GeneratedImage(data=data, extension="png")
