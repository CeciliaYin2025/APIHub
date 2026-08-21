"""stdio entrypoint: ``python -m mcp_server``.

Authenticates to the REST API with the service-account API key from
MCP_API_HUB_API_KEY (or .env.mcp). For per-user identity use the HTTP
transport (http_app.py) instead.

Unlike the HTTP transport this holds one long-lived session, so it can watch the
Hub's catalog fingerprint and push ``notifications/tools/list_changed`` when a
definition is added, renamed, removed or re-described — the client sees the new
tool without reconnecting. See ``mcp_server/README.md`` → Freshness.
"""

import asyncio
import contextlib
import logging

from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, ApiHubError, static_headers
from mcp_server.config import mcp_settings
from mcp_server.server import SessionTracker, create_server

logger = logging.getLogger(__name__)


async def watch_catalog(
    client: ApiHubClient,
    catalog: ToolCatalog,
    tracker: SessionTracker,
    interval: float,
) -> None:
    """Poll the catalog fingerprint; on a change, rebuild and tell the client.

    Every failure mode here is survivable and none should kill the server: an
    unreachable Hub, an older Hub without the endpoint, or a client that went
    away between the check and the notification. Each just means "try again next
    tick" — a watcher that died on the first blip would silently stop notifying
    for the rest of the process's life, which is worse than the staleness it was
    added to fix.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            version = await client.catalog_version()
        except ApiHubError as e:
            logger.debug("catalog-version poll failed: %s", e)
            continue

        if version == catalog.version:
            continue

        try:
            await catalog.tools(force=True)
        except ApiHubError as e:
            logger.warning("catalog changed but the reload failed: %s", e)
            continue

        session = tracker.session
        if session is None:
            # No tools/list yet — the client will get the fresh list when it asks.
            continue
        try:
            await session.send_tool_list_changed()
            logger.info("catalog changed; notified the client (%d tools)", len(await catalog.tools()))
        except Exception as e:  # noqa: BLE001 — a dead session must not kill the watcher
            logger.debug("could not send tools/list_changed: %s", e)


async def main() -> None:
    client = ApiHubClient(
        mcp_settings.api_hub_url,
        headers_provider=static_headers(mcp_settings.api_hub_api_key),
        timeout=mcp_settings.request_timeout_seconds,
    )
    aliases = [a.strip() for a in mcp_settings.db_aliases.split(",") if a.strip()] or None
    catalog = ToolCatalog(
        client,
        db_aliases=aliases,
        page_size_max=mcp_settings.page_size_max,
    )
    tracker = SessionTracker()
    server = create_server(client, catalog, tracker)

    interval = mcp_settings.watch_interval_seconds
    # Only advertise tools_changed when something will actually send it —
    # a client that trusts the capability and stops polling would otherwise
    # never see a new definition at all.
    options = server.create_initialization_options(NotificationOptions(tools_changed=interval > 0))

    watcher = asyncio.create_task(watch_catalog(client, catalog, tracker, interval)) if interval > 0 else None
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, options)
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
