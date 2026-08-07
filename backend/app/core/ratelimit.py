"""Dependency-free in-memory sliding-window rate limiter.

Single-instance in-memory is acceptable here: the backend runs as a single
uvicorn process, so process-local counters are correct and no external store
is needed. Multi-worker deployments would require a shared backend (Redis).
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status

_MAX_BUCKETS = 10_000

_lock = asyncio.Lock()
_buckets: dict[str, deque[float]] = {}


def reset() -> None:
    """Clear all in-memory rate-limit state.

    Test-support only: lets the test suite start from a clean sliding-window
    state between cases. No production caller invokes this, so production
    behavior is unchanged.
    """
    _buckets.clear()


def rate_limit(max_requests: int, window_seconds: int, key: str) -> Callable[..., Any]:
    """Return a FastAPI dependency enforcing a sliding window per client IP."""

    async def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        bucket_key = f"{key}:{client}"
        now = time.monotonic()
        async with _lock:
            # Evict the oldest bucket when at capacity to bound memory.
            if len(_buckets) >= _MAX_BUCKETS and bucket_key not in _buckets:
                _buckets.pop(next(iter(_buckets)))
            window = _buckets.setdefault(bucket_key, deque())
            # Prune requests that have fallen out of the sliding window.
            while window and window[0] <= now - window_seconds:
                window.popleft()
            if len(window) >= max_requests:
                retry_after = max(1, int(window[0] + window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            window.append(now)

    return dependency
