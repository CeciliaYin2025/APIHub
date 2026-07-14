"""stdio entrypoint: ``python -m mcp_server``.

Authenticates to the REST API with the service-account API key from
MCP_API_HUB_API_KEY (or .env.mcp). For per-user identity use the HTTP
transport (http_app.py) instead.
"""

import asyncio

from mcp.server.stdio import stdio_server

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, static_headers
from mcp_server.config import mcp_settings
from mcp_server.server import create_server


async def main() -> None:
    client = ApiHubClient(
        mcp_settings.api_hub_url,
        headers_provider=static_headers(mcp_settings.api_hub_api_key),
    )
    aliases = [a.strip() for a in mcp_settings.db_aliases.split(",") if a.strip()] or None
    catalog = ToolCatalog(
        client,
        db_aliases=aliases,
        page_size_max=mcp_settings.page_size_max,
        ttl_seconds=mcp_settings.catalog_ttl_seconds,
    )
    server = create_server(client, catalog)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
