"""Cap how many requests ONE principal can have in flight at the same time.

The sliding-window rate limiter counts request *starts*, which says nothing about
how much of the server a caller is holding. A client that fans out eight parallel
page requests against a heavy definition stays comfortably under 60/min while
occupying eight worker threads and eight DB connections for tens of seconds each.
That is what happened on 2026-08-20 and again on 2026-08-24: one MCP caller walking
an unfiltered ``eo_detail`` at ~7.5 average concurrency took 99.5% of the server's
request time, and everybody else queued behind it.

So: a per-key slot count, a short bounded wait, then 429. The bounded wait is the
same reasoning as the DB pool's ``pool_acquire_timeout`` — queueing indefinitely
just pins worker threads and turns a slow caller into an outage.

Process-local, like the rate limiter: with N API instances the effective cap is
N × the configured value. That is fine for the case this defends against (one
client's fan-out) and is not a substitute for a shared limiter.
"""

import asyncio
import logging
from collections import deque

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.middleware.principal_key import principal_bucket_key

logger = logging.getLogger(__name__)

# Paths that never touch a data source and are frequently fetched in parallel by
# the browser. Counting the Admin UI's asset loads against a human's query budget
# would make the SPA feel broken for no benefit.
EXEMPT_PREFIXES = ("/admin", "/docs", "/redoc", "/openapi.json", "/static", "/favicon")


class _Slot:
    """In-flight count plus a FIFO of waiters, for one principal bucket.

    Deliberately NOT an ``asyncio.Semaphore``. A semaphore's timeout path goes
    through cancelling ``acquire()``, and whether a permit granted in the same
    tick as the cancellation is handed back differs across Python versions
    (prod runs 3.11, dev 3.12). A leaked permit here would silently shrink a
    caller's budget until every request 429s, so the hand-off is explicit
    instead: ``release`` gives the slot directly to the next live waiter and
    only decrements when nobody is queued.
    """

    __slots__ = ("limit", "in_flight", "waiters", "refcount")

    def __init__(self, limit: int):
        self.limit = limit
        self.in_flight = 0
        self.waiters: deque[asyncio.Future] = deque()
        # Tasks holding or waiting on this slot. The middleware drops the entry
        # at zero so a long-lived process does not keep one slot per username
        # it has ever seen.
        self.refcount = 0

    async def acquire(self, timeout: float) -> bool:
        """Take a slot, waiting up to ``timeout`` seconds. False = give up."""
        if self.in_flight < self.limit:
            self.in_flight += 1
            return True
        if timeout <= 0:
            return False

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self.waiters.append(waiter)

        # The future resolves True (a slot was handed over) or False (we ran out
        # of patience). No cancellation is involved in either path.
        def _expire() -> None:
            if not waiter.done():
                waiter.set_result(False)

        timer = loop.call_later(timeout, _expire)
        try:
            return await waiter
        except asyncio.CancelledError:
            # Client went away while we were queued. If release() had already
            # handed us the slot, give it back rather than leaking it.
            if waiter.done() and not waiter.cancelled() and waiter.result():
                self.release()
            raise
        finally:
            timer.cancel()
            try:
                self.waiters.remove(waiter)
            except ValueError:
                pass

    def release(self) -> None:
        """Hand the slot to the next live waiter, or give it back to the pool."""
        while self.waiters:
            waiter = self.waiters.popleft()
            if not waiter.done():
                waiter.set_result(True)
                return
        self.in_flight -= 1


class ConcurrencyLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_concurrent: int | None = None, wait_seconds: float | None = None):
        super().__init__(app)
        self.max_concurrent = (
            max_concurrent if max_concurrent is not None else settings.max_concurrent_requests_per_principal
        )
        self.wait_seconds = wait_seconds if wait_seconds is not None else settings.concurrency_wait_seconds
        self._slots: dict[str, _Slot] = {}

    def _is_exempt(self, request: Request) -> bool:
        path = request.url.path
        return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)

    async def dispatch(self, request: Request, call_next) -> Response:
        if self.max_concurrent <= 0 or self._is_exempt(request):
            return await call_next(request)

        key = principal_bucket_key(request)

        # No await between lookup and refcount bump: the event loop is
        # single-threaded, so this stretch is atomic and the entry cannot be
        # reaped from under us.
        slot = self._slots.get(key)
        if slot is None:
            slot = _Slot(self.max_concurrent)
            self._slots[key] = slot
        slot.refcount += 1

        try:
            if not await slot.acquire(self.wait_seconds):
                logger.warning(
                    "Concurrency limit hit for %s (max %d in flight) on %s",
                    key,
                    self.max_concurrent,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            f"Too many concurrent requests for this caller "
                            f"(max {self.max_concurrent}). Retry after the in-flight ones finish."
                        )
                    },
                    headers={"Retry-After": "5"},
                )

            try:
                return await call_next(request)
            finally:
                slot.release()
        finally:
            slot.refcount -= 1
            if slot.refcount <= 0:
                self._slots.pop(key, None)
