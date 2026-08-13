"""stdio freshness: the fingerprint poller and the session it notifies.

This is the half of the freshness problem that is actually solvable. stdio holds
one long-lived session, so a change to a definition can be pushed as
``notifications/tools/list_changed`` and the client updates without
reconnecting. The HTTP transport is stateless and gets none of this — see
``mcp_server/README.md`` → Freshness.

The watcher's whole job is to survive: it runs for the life of the process, and
one unhandled error would silently end notifications for that process without
anything failing loudly. Most of what follows is therefore about failure modes.
"""

import asyncio

import httpx
import pytest

from mcp_server.__main__ import watch_catalog
from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient
from mcp_server.server import SessionTracker

DEFINITION = {
    "name": "order_summary",
    "description": "Open orders",
    "permission": "query:orders",
    "params": {},
}
VERSION_PATH = "/api/v1/mcp/catalog-version"


class _FakeSession:
    def __init__(self, fail=False):
        self.notifications = 0
        self.fail = fail

    async def send_tool_list_changed(self):
        if self.fail:
            raise RuntimeError("session is gone")
        self.notifications += 1


def _client(state, counter=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url.path)
        if request.url.path == VERSION_PATH:
            if state.get("version_error"):
                raise httpx.ConnectError("hub down")
            return httpx.Response(200, json={"version": state["version"]})
        if state.get("defs_error"):
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json=state["defs"])

    return ApiHubClient("http://hub", transport=httpx.MockTransport(handler))


async def _run_once(client, catalog, tracker, ticks=1):
    """Let the watcher complete `ticks` iterations, then stop it."""
    task = asyncio.create_task(watch_catalog(client, catalog, tracker, interval=0.01))
    await asyncio.sleep(0.02 * ticks + 0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_notifies_the_client_when_the_catalog_changes():
    state = {"version": "v1", "defs": [DEFINITION]}
    client = _client(state)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    tracker = SessionTracker()
    tracker.session = _FakeSession()

    state["version"] = "v2"
    state["defs"] = [DEFINITION, DEFINITION | {"name": "other"}]
    await _run_once(client, catalog, tracker)

    assert tracker.session.notifications >= 1
    assert len(await catalog.tools()) == 2  # rebuilt, not just announced
    await client.aclose()


@pytest.mark.asyncio
async def test_quiet_when_nothing_changed():
    state = {"version": "v1", "defs": [DEFINITION]}
    counter: list[str] = []
    client = _client(state, counter)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    tracker = SessionTracker()
    tracker.session = _FakeSession()

    counter.clear()
    await _run_once(client, catalog, tracker, ticks=2)

    assert tracker.session.notifications == 0
    # Polling costs the version check and nothing else.
    assert set(counter) == {VERSION_PATH}
    await client.aclose()


@pytest.mark.asyncio
async def test_survives_an_unreachable_hub():
    state = {"version": "v1", "defs": [DEFINITION], "version_error": True}
    client = _client(state)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    tracker = SessionTracker()
    tracker.session = _FakeSession()

    await _run_once(client, catalog, tracker, ticks=2)  # must not raise

    # Recovers once the Hub is back rather than staying wedged.
    state["version_error"] = False
    state["version"] = "v9"
    await _run_once(client, catalog, tracker)
    assert tracker.session.notifications >= 1
    await client.aclose()


@pytest.mark.asyncio
async def test_survives_a_reload_failure_and_keeps_the_old_version():
    """A failed reload must not record the new version, or the change is lost forever."""
    state = {"version": "v1", "defs": [DEFINITION]}
    client = _client(state)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    tracker = SessionTracker()
    tracker.session = _FakeSession()

    state["version"] = "v2"
    state["defs_error"] = True
    await _run_once(client, catalog, tracker)
    assert tracker.session.notifications == 0
    assert catalog.version == "v1"

    state["defs_error"] = False
    await _run_once(client, catalog, tracker)
    assert tracker.session.notifications >= 1
    await client.aclose()


@pytest.mark.asyncio
async def test_survives_a_dead_session():
    state = {"version": "v1", "defs": [DEFINITION]}
    client = _client(state)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    tracker = SessionTracker()
    tracker.session = _FakeSession(fail=True)

    state["version"] = "v2"
    await _run_once(client, catalog, tracker)  # must not raise
    await client.aclose()


@pytest.mark.asyncio
async def test_reloads_even_before_a_session_exists():
    """A change before the first tools/list still updates the catalog silently."""
    state = {"version": "v1", "defs": [DEFINITION]}
    client = _client(state)
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    await catalog.tools()
    tracker = SessionTracker()  # no session captured yet

    state["version"] = "v2"
    state["defs"] = [DEFINITION, DEFINITION | {"name": "other"}]
    await _run_once(client, catalog, tracker)

    assert catalog.version == "v2"
    assert len(await catalog.tools()) == 2
    await client.aclose()


def test_session_tracker_ignores_a_handler_called_outside_a_request():
    """Direct handler calls (every test in this suite) have no request context."""
    from mcp.server import Server

    tracker = SessionTracker()
    tracker.capture(Server("t"))  # must not raise
    assert tracker.session is None
