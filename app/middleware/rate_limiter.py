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
from app.services.auth_service import get_client_ip


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int | None = None):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_per_minute
        self.window = 60  # seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        principal = getattr(request.state, "principal", None)
        if principal is not None and principal.type == "api_key":
            key = f"api_key:{principal.key_id}"
            limit = principal.rate_limit if principal.rate_limit is not None else self.max_requests
        elif principal is not None:
            # Human callers (password JWT or SSO) get one bucket per username.
            # Keying them on the client IP instead — as this used to — collapses
            # every user behind the reverse proxy into a SINGLE shared window,
            # because the direct peer is always the gateway.
            key = f"user:{principal.user.username}"
            limit = self.max_requests
        else:
            # Anonymous: still IP-keyed, but through get_client_ip so a
            # configured TRUSTED_PROXIES chain resolves the real client instead
            # of the gateway. Default (no trusted proxies) is unchanged: the
            # direct peer, used as-is.
            key = f"ip:{get_client_ip(request)}"
            limit = self.max_requests

        now = time.time()
        self._hits[key] = [t for t in self._hits[key] if now - t < self.window]

        if len(self._hits[key]) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        self._hits[key].append(now)
        return await call_next(request)
