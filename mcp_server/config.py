"""MCP server configuration, loaded from MCP_-prefixed environment variables.

The MCP server is a separate process from the FastAPI app; it only knows the
REST API's base URL and its own transport/auth settings.
"""

from pydantic_settings import BaseSettings


class McpSettings(BaseSettings):
    # Base URL of the Oracle API Hub REST API (the MCP server is a thin proxy over it)
    api_hub_url: str = "http://localhost:8000"
    # Phase 1 stdio auth: service-account API key sent as X-API-Key on every REST call
    api_hub_api_key: str = ""
    # Comma-separated db aliases to expose; empty = every alias from GET /databases
    db_aliases: str = ""
    # Hard cap for the page_size argument on query tools (REST itself allows up to 1000)
    page_size_max: int = 100
    # Tool catalog is re-fetched on the next tools/list after this many seconds
    catalog_ttl_seconds: int = 300

    # Streamable HTTP transport
    http_host: str = "0.0.0.0"
    http_port: int = 8810
    # Public URL prefix when served behind a reverse proxy (e.g. /apihub/mcp)
    path_prefix: str = ""

    # Phase 2 — OAuth 2.1 Resource Server (isap-auth)
    oauth_enabled: bool = False
    public_url: str = ""  # e.g. https://isap.sercomm.com/apihub/mcp
    auth_issuer: str = ""  # e.g. https://isap.sercomm.com/auth
    introspection_url: str = ""  # e.g. http://isap-auth:9997/auth/api/v1/introspect
    introspection_secret: str = ""
    bridge_secret: str = ""  # shared secret for POST /api/v1/auth/sso-token

    model_config = {"env_prefix": "MCP_", "env_file": ".env.mcp", "env_file_encoding": "utf-8"}


mcp_settings = McpSettings()
