"""Simple in-memory rate limiter middleware using a sliding window.

For production with multiple API instances, replace this with a Redis-backed
limiter (e.g. fastapi-limiter).
"""

import math
import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.middleware.principal_key import principal_bucket_key


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int | None = None):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_per_minute
        self.window = 60  # seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Bucketing rule lives in principal_key so the concurrency limiter throttles
        # the same caller under the same key.
        principal = getattr(request.state, "principal", None)
        key = principal_bucket_key(request)
        if principal is not None and principal.type == "api_key" and principal.rate_limit is not None:
            limit = principal.rate_limit
        else:
            limit = self.max_requests

        now = time.time()
        self._hits[key] = [t for t in self._hits[key] if now - t < self.window]

        if len(self._hits[key]) >= limit:
            # The window slides, so the next slot opens when the oldest hit ages out —
            # tell the client exactly that instead of leaving it to guess a backoff.
            # A key with rate_limit=0 is blocked with no hits on record; waiting never helps,
            # so it just gets the full window.
            hits = self._hits[key]
            retry_after = max(1, math.ceil(self.window - (now - hits[0]))) if hits else self.window
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        self._hits[key].append(now)
        return await call_next(request)
