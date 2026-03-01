"""Simple in-memory rate limiter middleware using a sliding window.

For production with multiple API instances, replace this with a Redis-backed
limiter (e.g. fastapi-limiter).
"""

import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int | None = None):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_per_minute
        self.window = 60  # seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract client identifier (IP or authenticated user)
        client_ip = request.client.host if request.client else "unknown"
        key = client_ip
        now = time.time()

        # Prune old entries
        self._hits[key] = [t for t in self._hits[key] if now - t < self.window]

        if len(self._hits[key]) >= self.max_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        self._hits[key].append(now)
        return await call_next(request)
