# Oracle API Hub MCP Server

Exposes registered SQL definitions as MCP tools. Thin HTTP proxy over the
REST API — RBAC, audit, and rate limiting apply per caller, unchanged.

## Tools

- `query_<db>_<name>` — one per SQL definition (dynamic; paged, page_size ≤ MCP_PAGE_SIZE_MAX)
- `list_databases` / `describe_table` — schema discovery
- `refresh_query_tools` — rebuild the tool list after definition changes

## Transports & auth

| Transport | Run | Auth |
|---|---|---|
| stdio | `python -m mcp_server` | `MCP_API_HUB_API_KEY` env (service-account key) |
| Streamable HTTP | `uvicorn mcp_server.http_app:app --port 8810` | Caller's `X-API-Key` / `Authorization` header, passed through to the REST API |

## Claude Code registration

    claude mcp add apihub --transport stdio \
      -e MCP_API_HUB_URL=http://localhost:8000 \
      -e MCP_API_HUB_API_KEY=<key> \
      -- python -m mcp_server

    # or HTTP (note: /mcp/ with trailing slash — required)
    claude mcp add apihub-http --transport http http://localhost:8810/mcp/ \
      --header "X-API-Key: <key>"

## Settings

All via `MCP_*` env vars or `.env.mcp` — see `mcp_server/config.py`.
Phase 2 (OAuth 2.1 Resource Server, isap-auth) is toggled by `MCP_OAUTH_ENABLED`.
