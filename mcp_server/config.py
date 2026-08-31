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
    # Seconds to wait for one REST call before giving up. The ceiling matters
    # more than it looks: when it expires the MCP client reports a failure and
    # the model typically re-issues the same tool call, while the Hub is still
    # executing the abandoned one — so a value below the real query time turns
    # one slow query into an ever-growing pile of duplicates. Keep it above the
    # slowest definition being exposed.
    request_timeout_seconds: float = 120.0
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
    # Identity bridge (POST /api/v1/auth/sso-token). LEAVE EMPTY when the API
    # Hub runs with OAUTH_RS_ENABLED: the caller's own token is then forwarded
    # unchanged and the Hub validates it itself. Set it only for a Hub that
    # cannot accept isap-auth tokens directly — see mcp_server/oauth.py.
    bridge_secret: str = ""
    # Audience this server accepts in incoming tokens. Empty = use public_url.
    # A token whose aud/resource claim does not name us is rejected (MCP spec:
    # servers MUST validate that tokens were issued specifically for them).
    # Note this only bites when the AS actually returns an audience claim —
    # isap-auth returns none, see mcp_server/oauth.py::token_is_for_this_server.
    oauth_audience: str = ""
    # Fallback for an authorization server that returns no audience claim at
    # all: accept only these client_ids (space- or comma-separated). Empty =
    # accept any active token, which is the only workable default while clients
    # register through DCR and get a fresh client_id per install.
    oauth_allowed_client_ids: str = ""
    # Scopes advertised in the resource metadata and the 401 challenge, so
    # clients request these rather than everything the AS offers. Space- or
    # comma-separated; empty means advertise none.
    oauth_scopes: str = ""

    # DNS-rebinding protection for the HTTP transport (comma-separated).
    # Both default to values derived from public_url — see http_app.transport_security().
    allowed_hosts: str = ""
    allowed_origins: str = ""

    model_config = {"env_prefix": "MCP_", "env_file": ".env.mcp", "env_file_encoding": "utf-8"}


mcp_settings = McpSettings()
