"""Retry policy: exponential backoff with jitter, and which errors are safe to retry (FR-11).

A publication is retried only when the platform clearly did not accept it:
connection failures, HTTP 429 and HTTP 5xx. A read timeout is not retried,
because the post may have been created and a retry would publish it twice.
"""
import random
from typing import Optional

import httpx

MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 3600
JITTER_RATIO = 0.1


def backoff_seconds(attempt: int, rng: Optional[random.Random] = None) -> float:
    """Delay before attempt ``attempt + 1``. ``attempt`` starts at 1."""
    delay = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** max(attempt - 1, 0)))
    generator = rng or random.Random()
    return delay + generator.uniform(0, delay * JITTER_RATIO)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False
