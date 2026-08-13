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
    # stdio only: poll the catalog fingerprint this often and push
    # notifications/tools/list_changed when it moves. 0 disables the poller, and
    # with it the tools_changed capability. Has no effect on the HTTP transport,
    # which is stateless and has no session to push to.
    watch_interval_seconds: int = 30

    # Phase 2 — OAuth 2.1 Resource Server (isap-auth)
    oauth_enabled: bool = False
    public_url: str = ""  # e.g. https://isap.sercomm.com/apihub/mcp
    auth_issuer: str = ""  # e.g. https://isap.sercomm.com/auth
    introspection_url: str = ""  # e.g. http://isap-auth:9997/auth/api/v1/introspect
    introspection_secret: str = ""
    bridge_secret: str = ""  # shared secret for POST /api/v1/auth/sso-token

    model_config = {"env_prefix": "MCP_", "env_file": ".env.mcp", "env_file_encoding": "utf-8"}


mcp_settings = McpSettings()
