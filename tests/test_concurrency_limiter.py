"""ConcurrencyLimiterMiddleware caps in-flight requests per principal."""

import asyncio
from collections import deque

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import app.middleware.auth_context as ac
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.concurrency_limiter import ConcurrencyLimiterMiddleware, _Slot
from app.models.user import User
from app.services.auth_service import Principal


def _build(monkeypatch, principal, max_concurrent=2, wait_seconds=0.0, hold=0.2, path="/probe"):
    application = FastAPI()
    application.add_middleware(ConcurrencyLimiterMiddleware, max_concurrent=max_concurrent, wait_seconds=wait_seconds)
    application.add_middleware(AuthContextMiddleware)

    @application.get(path)
    async def probe(request: Request):
        await asyncio.sleep(hold)
        return {"ok": True}

    @application.get("/admin/asset.js")
    async def asset(request: Request):
        await asyncio.sleep(hold)
        return {"ok": True}

    monkeypatch.setattr(ac, "build_principal", lambda request: principal)
    return application


def _user(username="00049"):
    return Principal(type="sso_user", user=User(username=username, is_active=True))


async def _fire(application, n, path="/probe"):
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        results = await asyncio.gather(*[client.get(path) for _ in range(n)])
    return [r.status_code for r in results]


@pytest.mark.asyncio
async def test_excess_concurrent_requests_are_rejected(monkeypatch):
    application = _build(monkeypatch, _user(), max_concurrent=2, wait_seconds=0.0)

    codes = await _fire(application, 5)

    assert codes.count(200) == 2
    assert codes.count(429) == 3


@pytest.mark.asyncio
async def test_sequential_requests_are_never_limited(monkeypatch):
    """The cap is on simultaneous requests, not on volume — that is the rate limiter."""
    application = _build(monkeypatch, _user(), max_concurrent=1, wait_seconds=0.0, hold=0.0)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        for _ in range(10):
            assert (await client.get("/probe")).status_code == 200


@pytest.mark.asyncio
async def test_waiting_lets_a_burst_through_instead_of_rejecting(monkeypatch):
    """With a wait budget, a short burst queues and succeeds."""
    application = _build(monkeypatch, _user(), max_concurrent=2, wait_seconds=5.0, hold=0.05)

    codes = await _fire(application, 6)

    assert codes == [200] * 6


@pytest.mark.asyncio
async def test_wait_expires_into_429(monkeypatch):
    application = _build(monkeypatch, _user(), max_concurrent=1, wait_seconds=0.05, hold=0.5)

    codes = await _fire(application, 3)

    assert codes.count(200) == 1
    assert codes.count(429) == 2


@pytest.mark.asyncio
async def test_one_caller_does_not_block_another(monkeypatch):
    """The whole point: 00049's fan-out must not starve everyone else."""
    application = FastAPI()
    application.add_middleware(ConcurrencyLimiterMiddleware, max_concurrent=1, wait_seconds=0.0)

    @application.get("/probe")
    async def probe(request: Request):
        await asyncio.sleep(0.2)
        return {"ok": True}

    # Alternate the principal per request so two distinct users interleave.
    names = iter(["00049", "00049", "A004077"])
    application.add_middleware(AuthContextMiddleware)
    monkeypatch.setattr(
        ac,
        "build_principal",
        lambda request: Principal(type="sso_user", user=User(username=next(names), is_active=True)),
    )

    codes = await _fire(application, 3)

    # 00049 gets one through and one rejected; the other user is unaffected.
    assert codes.count(200) == 2
    assert codes.count(429) == 1


@pytest.mark.asyncio
async def test_admin_assets_are_exempt(monkeypatch):
    """The SPA loads assets in parallel; those must not eat a query budget."""
    application = _build(monkeypatch, _user(), max_concurrent=1, wait_seconds=0.0, hold=0.1)

    codes = await _fire(application, 4, path="/admin/asset.js")

    assert codes == [200] * 4


@pytest.mark.asyncio
async def test_zero_disables_the_cap(monkeypatch):
    application = _build(monkeypatch, _user(), max_concurrent=0, wait_seconds=0.0, hold=0.1)

    codes = await _fire(application, 5)

    assert codes == [200] * 5


@pytest.mark.asyncio
async def test_slots_are_reaped_after_requests_finish(monkeypatch):
    """A long-lived process must not accumulate one semaphore per username seen."""
    application = _build(monkeypatch, _user(), max_concurrent=2, wait_seconds=5.0, hold=0.0)
    await _fire(application, 4)

    limiter = next(m for m in application.user_middleware if m.cls is ConcurrencyLimiterMiddleware)
    # user_middleware holds the spec, not the instance; reach the live one through
    # the built stack.
    stack = application.middleware_stack
    while stack is not None and not isinstance(stack, ConcurrencyLimiterMiddleware):
        stack = getattr(stack, "app", None)
    assert stack is not None, f"limiter not found in stack (spec: {limiter})"
    assert stack._slots == {}


@pytest.mark.asyncio
async def test_rejection_carries_retry_after(monkeypatch):
    application = _build(monkeypatch, _user(), max_concurrent=1, wait_seconds=0.0, hold=0.3)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        results = await asyncio.gather(*[client.get("/probe") for _ in range(2)])

    rejected = [r for r in results if r.status_code == 429]
    assert len(rejected) == 1
    assert rejected[0].headers["Retry-After"] == "5"


def test_anonymous_callers_are_bucketed_by_ip(monkeypatch):
    application = FastAPI()
    application.add_middleware(ConcurrencyLimiterMiddleware, max_concurrent=1, wait_seconds=0.0)
    application.add_middleware(AuthContextMiddleware)

    @application.get("/probe")
    def probe(request: Request):
        return {"ok": True}

    monkeypatch.setattr(ac, "build_principal", lambda request: None)
    client = TestClient(application)
    # Sequential anonymous calls still pass — no principal is not an error here.
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200


# ── _Slot invariants ────────────────────────────────────────────────────────
# The slot is hand-rolled rather than an asyncio.Semaphore precisely so the
# hand-off is deterministic across Python versions (prod 3.11, dev 3.12).


@pytest.mark.asyncio
async def test_slot_hands_over_without_touching_in_flight():
    slot = _Slot(limit=1)
    assert await slot.acquire(0) is True

    waiting = asyncio.ensure_future(slot.acquire(5))
    await asyncio.sleep(0)  # let it queue
    assert slot.in_flight == 1

    slot.release()
    assert await waiting is True
    # The slot was transferred, not returned and re-taken.
    assert slot.in_flight == 1

    slot.release()
    assert slot.in_flight == 0


@pytest.mark.asyncio
async def test_timed_out_waiter_does_not_consume_a_slot():
    slot = _Slot(limit=1)
    assert await slot.acquire(0) is True
    assert await slot.acquire(0.01) is False
    assert slot.waiters == deque()

    slot.release()
    assert slot.in_flight == 0
    assert await slot.acquire(0) is True


@pytest.mark.asyncio
async def test_release_skips_a_timed_out_waiter_and_reaches_a_live_one():
    """A stale waiter must not swallow the slot."""
    slot = _Slot(limit=1)
    assert await slot.acquire(0) is True

    doomed = asyncio.ensure_future(slot.acquire(0.01))
    live = asyncio.ensure_future(slot.acquire(5))
    await asyncio.sleep(0.05)  # doomed times out, live still queued

    assert await doomed is False
    slot.release()
    assert await live is True
    assert slot.in_flight == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_gives_the_slot_back():
    """A client that disconnects while queued must not leak its slot."""
    slot = _Slot(limit=1)
    assert await slot.acquire(0) is True

    queued = asyncio.ensure_future(slot.acquire(5))
    await asyncio.sleep(0)

    # Hand the slot over, then cancel before the waiter can resume.
    slot.release()
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued

    assert slot.in_flight == 0
    assert await slot.acquire(0) is True


@pytest.mark.asyncio
async def test_capacity_survives_repeated_timeouts():
    """The failure mode this design exists to avoid: a shrinking budget."""
    slot = _Slot(limit=2)
    assert await slot.acquire(0) is True
    assert await slot.acquire(0) is True

    for _ in range(50):
        assert await slot.acquire(0.001) is False

    slot.release()
    slot.release()
    assert slot.in_flight == 0
    assert await slot.acquire(0) is True
    assert await slot.acquire(0) is True
    assert await slot.acquire(0) is False
