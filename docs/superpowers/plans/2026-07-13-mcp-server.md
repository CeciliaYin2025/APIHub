# Oracle API Hub MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Oracle API Hub's registered SQL definitions as dynamically generated MCP tools, so AI assistants (Claude Code/Desktop, Hermes) can query any configured database with per-caller RBAC and audit intact.

**Architecture:** A new top-level `mcp_server/` package (separate process, mirroring the ITSM-PM template at `C:\app\ITSM-PM`) acts as a **thin HTTP proxy** over the existing REST API — it never touches databases or the service layer directly, so RBAC (`PermissionChecker`), audit logging, and rate limiting stay intact. Tools are generated dynamically from `GET /{db}/queries/definitions`: one tool per `(db_alias, definition)`, plus static `list_databases` / `describe_table` tools. Phase 1 authenticates with existing API keys (stdio: env var; HTTP: header passthrough). Phase 2 adds the OAuth 2.1 Resource Server gate (isap-auth introspection + identity-bridge endpoint), copying the ITSM-PM RS pattern.

**Tech Stack:** Python 3.10+, `mcp` SDK ≥ 1.8 (low-level `Server` API + `StreamableHTTPSessionManager`), httpx, Starlette (already a FastAPI dependency), pytest + pytest-asyncio.

## Global Constraints

- Python 3.10+, ruff, line length 120 (existing `pyproject.toml` settings).
- `mcp_server/` must NOT import from `app/` except nothing — it is a standalone proxy; all data access goes through the REST API via httpx. (Exception: Phase 2 Task 8 modifies `app/` itself.)
- MCP tool names must match `^[a-zA-Z0-9_-]{1,64}$` — sanitize definition names.
- Query tools always use `fetch_mode="page"`; `page_size` is clamped to `MCP_PAGE_SIZE_MAX` (default 100). Never expose `fetch_mode=all`.
- Writes/procedures are NOT exposed in this plan (per scope decision).
- All MCP env vars use prefix `MCP_` and load from `.env.mcp`.
- Async tests use explicit `@pytest.mark.asyncio` (pytest-asyncio strict mode; do not set `asyncio_mode=auto` globally).
- ITSM-PM pitfalls to honor (report §10.7): POST to an SSE-only path must 405 (we simply don't ship a legacy `/sse` route); reverse-proxy prefix must be configurable (`MCP_PATH_PREFIX`); OAuth loop-back/introspection patterns in Phase 2 copy the proven design.

---

## Phase 1 — API-key auth, dynamic tools, stdio + Streamable HTTP

### Task 1: Dependency + MCP settings

**Files:**
- Modify: `pyproject.toml` (dependencies list, lines 10–31)
- Create: `mcp_server/__init__.py`
- Create: `mcp_server/config.py`
- Test: `tests/mcp/__init__.py`, `tests/mcp/test_config.py`

**Interfaces:**
- Produces: `mcp_server.config.McpSettings` (pydantic-settings class, env prefix `MCP_`, env file `.env.mcp`) and module singleton `mcp_settings`. Fields (exact names): `api_hub_url: str = "http://localhost:8000"`, `api_hub_api_key: str = ""`, `db_aliases: str = ""`, `page_size_max: int = 100`, `catalog_ttl_seconds: int = 300`, `http_host: str = "0.0.0.0"`, `http_port: int = 8810`, `path_prefix: str = ""`, `oauth_enabled: bool = False`, `public_url: str = ""`, `auth_issuer: str = ""`, `introspection_url: str = ""`, `introspection_secret: str = ""`, `bridge_secret: str = ""`.

- [ ] **Step 1: Add the `mcp` dependency**

In `pyproject.toml` `[project] dependencies`, after `"pymssql>=2.2.8",` add:

```toml
    # MCP server (mcp_server/ package; StreamableHTTPSessionManager requires >=1.8)
    "mcp>=1.8.0",
```

Run: `pip install -e ".[dev]"`
Expected: `mcp` installs without dependency conflicts.

- [ ] **Step 2: Write the failing test**

Create `tests/mcp/__init__.py` (empty) and `tests/mcp/test_config.py`:

```python
"""MCP settings load from MCP_-prefixed env vars."""

from mcp_server.config import McpSettings


def test_defaults():
    s = McpSettings(_env_file=None)
    assert s.api_hub_url == "http://localhost:8000"
    assert s.page_size_max == 100
    assert s.catalog_ttl_seconds == 300
    assert s.oauth_enabled is False


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("MCP_API_HUB_URL", "http://api:9000")
    monkeypatch.setenv("MCP_API_HUB_API_KEY", "sk-test-123")
    monkeypatch.setenv("MCP_PAGE_SIZE_MAX", "50")
    s = McpSettings(_env_file=None)
    assert s.api_hub_url == "http://api:9000"
    assert s.api_hub_api_key == "sk-test-123"
    assert s.page_size_max == 50


def test_db_aliases_default_empty_means_all():
    s = McpSettings(_env_file=None)
    assert s.db_aliases == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/mcp/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server'`

- [ ] **Step 4: Write the implementation**

Create `mcp_server/__init__.py` (empty) and `mcp_server/config.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/mcp/test_config.py -v`
Expected: 3 PASS

- [ ] **Step 6: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add pyproject.toml mcp_server/ tests/mcp/
git commit -m "feat(mcp): add mcp dependency and MCP_-prefixed settings"
```

---

### Task 2: ApiHubClient — async thin proxy over the REST API

**Files:**
- Create: `mcp_server/client.py`
- Test: `tests/mcp/test_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone).
- Produces:
  - `ApiHubError(Exception)` with attributes `status_code: int`, `detail: str`; `str()` renders `"[<status>] <detail>"`.
  - `static_headers(api_key: str) -> Callable[[], dict]` — returns a provider that always yields `{"X-API-Key": api_key}` (empty dict if key is empty).
  - `ApiHubClient(base_url: str, *, headers_provider: Callable[[], dict] | None = None, transport: httpx.AsyncBaseTransport | None = None)` with async methods:
    - `list_databases() -> list[dict]` → `GET /api/v1/databases` (entries have `alias`, `db_type`, `description`, …)
    - `list_definitions(db: str) -> list[dict]` → `GET /api/v1/{db}/queries/definitions` (entries: `name`, `db_alias`, `description`, `permission`, `params: {pname: {type, required, description}}`)
    - `execute_query(db: str, query_name: str, params: dict, *, page: int = 1, page_size: int = 50) -> dict` → `POST /api/v1/{db}/queries/execute` with `fetch_mode="page"`
    - `describe_table(db: str, table: str) -> dict` → `GET /api/v1/{db}/schemas/describe/{table}`
    - `aclose() -> None`

The `headers_provider` is called per request — this is what lets Task 6 swap in per-request header passthrough (contextvar) without changing this class.

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_client.py`:

```python
"""ApiHubClient proxies REST calls with injected auth headers and maps errors."""

import httpx
import pytest

from mcp_server.client import ApiHubClient, ApiHubError, static_headers


def make_client(handler, headers_provider=None):
    return ApiHubClient(
        "http://hub",
        headers_provider=headers_provider,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_execute_query_posts_payload_with_api_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("X-API-Key")
        seen["body"] = request.content
        return httpx.Response(200, json={"success": True, "total": 0, "columns": [], "data": []})

    client = make_client(handler, headers_provider=static_headers("sk-test"))
    result = await client.execute_query("pg_test", "order_summary", {"status": "OPEN"}, page=2, page_size=10)

    assert result["success"] is True
    assert seen["url"] == "http://hub/api/v1/pg_test/queries/execute"
    assert seen["api_key"] == "sk-test"
    import json

    body = json.loads(seen["body"])
    assert body == {
        "query_name": "order_summary",
        "params": {"status": "OPEN"},
        "fetch_mode": "page",
        "page": 2,
        "page_size": 10,
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_list_definitions_and_databases_paths():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[])

    client = make_client(handler)
    await client.list_databases()
    await client.list_definitions("orcl")
    await client.describe_table("orcl", "HR.EMPLOYEES")
    assert calls == [
        "http://hub/api/v1/databases",
        "http://hub/api/v1/orcl/queries/definitions",
        "http://hub/api/v1/orcl/schemas/describe/HR.EMPLOYEES",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_http_error_maps_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Permission denied: read on query:orders"})

    client = make_client(handler)
    with pytest.raises(ApiHubError) as exc:
        await client.execute_query("orcl", "orders", {})
    assert exc.value.status_code == 403
    assert "Permission denied" in exc.value.detail
    await client.aclose()


@pytest.mark.asyncio
async def test_connection_error_maps_to_apihub_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = make_client(handler)
    with pytest.raises(ApiHubError) as exc:
        await client.list_databases()
    assert exc.value.status_code == 0
    assert "unreachable" in exc.value.detail.lower()
    await client.aclose()


def test_static_headers_empty_key_yields_no_header():
    assert static_headers("")() == {}
    assert static_headers("sk-x")() == {"X-API-Key": "sk-x"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` on `mcp_server.client`

- [ ] **Step 3: Write the implementation**

Create `mcp_server/client.py`:

```python
"""Async HTTP client for the Oracle API Hub REST API.

The MCP server never talks to databases or the service layer directly —
every tool call is proxied through the REST API so RBAC, audit logging, and
rate limiting apply exactly as they do for any other API consumer.
"""

from collections.abc import Callable

import httpx


class ApiHubError(Exception):
    """A REST call failed; the message is safe to surface to the MCP client."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


def static_headers(api_key: str) -> Callable[[], dict]:
    """Header provider for a fixed API key (stdio transport)."""
    headers = {"X-API-Key": api_key} if api_key else {}
    return lambda: headers


class ApiHubClient:
    """Thin async proxy over the API Hub REST endpoints used by MCP tools.

    ``headers_provider`` is invoked on every request, which allows per-request
    credential injection (HTTP transport passthrough) as well as static keys.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers_provider: Callable[[], dict] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._headers_provider = headers_provider or (lambda: {})
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api/v1",
            timeout=30.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> dict | list:
        try:
            resp = await self._http.request(method, url, headers=self._headers_provider(), **kwargs)
        except httpx.HTTPError as e:
            raise ApiHubError(0, f"API Hub unreachable: {e}") from e
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise ApiHubError(resp.status_code, str(detail))
        return resp.json()

    async def list_databases(self) -> list[dict]:
        return await self._request("GET", "/databases")

    async def list_definitions(self, db: str) -> list[dict]:
        return await self._request("GET", f"/{db}/queries/definitions")

    async def execute_query(
        self, db: str, query_name: str, params: dict, *, page: int = 1, page_size: int = 50
    ) -> dict:
        payload = {
            "query_name": query_name,
            "params": params,
            "fetch_mode": "page",
            "page": page,
            "page_size": page_size,
        }
        return await self._request("POST", f"/{db}/queries/execute", json=payload)

    async def describe_table(self, db: str, table: str) -> dict:
        return await self._request("GET", f"/{db}/schemas/describe/{table}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/test_client.py -v`
Expected: 5 PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add mcp_server/client.py tests/mcp/test_client.py
git commit -m "feat(mcp): ApiHubClient thin async proxy with pluggable auth headers"
```

---

### Task 3: ToolCatalog — dynamic tools from SQL definitions

**Files:**
- Create: `mcp_server/catalog.py`
- Test: `tests/mcp/test_catalog.py`

**Interfaces:**
- Consumes: `ApiHubClient.list_databases()`, `ApiHubClient.list_definitions(db)` from Task 2.
- Produces:
  - `sanitize(text: str) -> str` — lowercase, non-`[a-zA-Z0-9_-]` runs → `_`, strip leading/trailing `_`.
  - `tool_name(db_alias: str, definition_name: str) -> str` — `f"query_{sanitize(db)}_{sanitize(name)}"` truncated to 64 chars.
  - `build_tool(db_alias: str, definition: dict, page_size_max: int) -> mcp.types.Tool` — JSON schema from `params` (`string`→string, `number`→number, `date`→string+format=date) plus `page`/`page_size` paging args.
  - `QueryTarget` dataclass: `db_alias: str`, `query_name: str`.
  - `ToolCatalog(client, *, db_aliases: list[str] | None = None, page_size_max: int = 100, ttl_seconds: int = 300)` with:
    - `async tools(force: bool = False) -> list[Tool]` (TTL-cached; re-fetches when stale/forced)
    - `target(name: str) -> QueryTarget | None`
    - property `page_size_max: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_catalog.py`:

```python
"""Dynamic tool catalog: definition → MCP Tool mapping, naming, TTL refresh."""

import httpx
import pytest

from mcp_server.catalog import ToolCatalog, build_tool, sanitize, tool_name
from mcp_server.client import ApiHubClient

DEFINITION = {
    "name": "order_summary",
    "db_alias": "pg_test",
    "description": "Open orders grouped by customer",
    "permission": "query:orders",
    "params": {
        "status": {"type": "string", "required": True, "description": "Order status code"},
        "min_amount": {"type": "number", "required": False, "description": ""},
        "since": {"type": "date", "required": False, "description": "Earliest order date"},
    },
}


def test_sanitize_and_tool_name():
    assert sanitize("HR.Employee Report!") == "hr_employee_report"
    assert tool_name("pg_test", "order_summary") == "query_pg_test_order_summary"
    assert len(tool_name("a" * 40, "b" * 60)) <= 64


def test_build_tool_schema():
    tool = build_tool("pg_test", DEFINITION, page_size_max=100)
    assert tool.name == "query_pg_test_order_summary"
    assert "Open orders grouped by customer" in tool.description
    assert "[pg_test]" in tool.description
    props = tool.inputSchema["properties"]
    assert props["status"] == {"type": "string", "description": "Order status code"}
    assert props["min_amount"] == {"type": "number"}
    assert props["since"]["type"] == "string"
    assert props["since"]["format"] == "date"
    assert props["page"]["type"] == "integer"
    assert props["page_size"]["maximum"] == 100
    assert tool.inputSchema["required"] == ["status"]
    assert tool.inputSchema["additionalProperties"] is False


def _mock_client(definitions_by_db, databases=None, counter=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url.path)
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=databases or [])
        for db, defs in definitions_by_db.items():
            if request.url.path == f"/api/v1/{db}/queries/definitions":
                return httpx.Response(200, json=defs)
        return httpx.Response(404, json={"detail": "not found"})

    return ApiHubClient("http://hub", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_catalog_loads_all_databases_when_aliases_empty():
    client = _mock_client(
        {"orcl": [DEFINITION | {"db_alias": "orcl"}], "pg_test": [DEFINITION]},
        databases=[{"alias": "orcl"}, {"alias": "pg_test"}],
    )
    catalog = ToolCatalog(client)
    tools = await catalog.tools()
    names = {t.name for t in tools}
    assert names == {"query_orcl_order_summary", "query_pg_test_order_summary"}
    target = catalog.target("query_pg_test_order_summary")
    assert target.db_alias == "pg_test"
    assert target.query_name == "order_summary"
    await client.aclose()


@pytest.mark.asyncio
async def test_catalog_respects_explicit_aliases_and_ttl_cache():
    counter: list[str] = []
    client = _mock_client({"pg_test": [DEFINITION]}, counter=counter)
    catalog = ToolCatalog(client, db_aliases=["pg_test"], ttl_seconds=3600)
    await catalog.tools()
    await catalog.tools()  # within TTL — no extra fetch
    assert counter.count("/api/v1/pg_test/queries/definitions") == 1
    await catalog.tools(force=True)  # forced refresh fetches again
    assert counter.count("/api/v1/pg_test/queries/definitions") == 2
    # explicit aliases → /databases is never called
    assert "/api/v1/databases" not in counter
    await client.aclose()


@pytest.mark.asyncio
async def test_name_collision_first_wins():
    defs = [DEFINITION, DEFINITION | {"name": "order summary"}]  # sanitizes to same name
    client = _mock_client({"pg_test": defs})
    catalog = ToolCatalog(client, db_aliases=["pg_test"])
    tools = await catalog.tools()
    assert len(tools) == 1
    assert catalog.target("query_pg_test_order_summary").query_name == "order_summary"
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError` on `mcp_server.catalog`

- [ ] **Step 3: Write the implementation**

Create `mcp_server/catalog.py`:

```python
"""Build the dynamic MCP tool catalog from registered SQL definitions.

One MCP tool per (db_alias, SQL definition). The tool's JSON schema is derived
from the definition's declared bind parameters, so the LLM sees real,
self-describing tools instead of a generic execute(name, params) black box.
"""

import re
import time
from dataclasses import dataclass

from mcp.types import Tool

from mcp_server.client import ApiHubClient

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")

# SQL definition param types (app/services/sql_registry.py ParamDef) → JSON schema
PARAM_TYPE_MAP = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "date": {"type": "string", "format": "date", "description": "Date in YYYY-MM-DD format"},
}


def sanitize(text: str) -> str:
    return _NAME_RE.sub("_", text).strip("_").lower()


def tool_name(db_alias: str, definition_name: str) -> str:
    return f"query_{sanitize(db_alias)}_{sanitize(definition_name)}"[:64]


def build_tool(db_alias: str, definition: dict, page_size_max: int) -> Tool:
    properties: dict = {}
    required: list[str] = []
    for pname, pdef in definition.get("params", {}).items():
        schema = dict(PARAM_TYPE_MAP.get(pdef.get("type", "string"), {"type": "string"}))
        if pdef.get("description"):
            schema["description"] = pdef["description"]
        properties[pname] = schema
        if pdef.get("required"):
            required.append(pname)
    properties["page"] = {"type": "integer", "minimum": 1, "default": 1, "description": "Result page number"}
    properties["page_size"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": page_size_max,
        "default": 50,
        "description": "Rows per page",
    }
    description = definition.get("description") or f"Run registered query '{definition['name']}'"
    return Tool(
        name=tool_name(db_alias, definition["name"]),
        description=f"[{db_alias}] {description}",
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    )


@dataclass
class QueryTarget:
    db_alias: str
    query_name: str


class ToolCatalog:
    """Maps MCP tool names to (db_alias, query definition) targets, with TTL refresh."""

    def __init__(
        self,
        client: ApiHubClient,
        *,
        db_aliases: list[str] | None = None,
        page_size_max: int = 100,
        ttl_seconds: int = 300,
    ):
        self._client = client
        self._db_aliases = db_aliases
        self._page_size_max = page_size_max
        self._ttl = ttl_seconds
        self._loaded_at = 0.0
        self._tools: list[Tool] = []
        self._targets: dict[str, QueryTarget] = {}

    @property
    def page_size_max(self) -> int:
        return self._page_size_max

    def target(self, name: str) -> QueryTarget | None:
        return self._targets.get(name)

    async def tools(self, force: bool = False) -> list[Tool]:
        stale = (time.monotonic() - self._loaded_at) > self._ttl
        if force or not self._tools or stale:
            await self._load()
        return self._tools

    async def _load(self) -> None:
        if self._db_aliases:
            aliases = self._db_aliases
        else:
            aliases = [d["alias"] for d in await self._client.list_databases()]
        tools: list[Tool] = []
        targets: dict[str, QueryTarget] = {}
        for alias in aliases:
            for definition in await self._client.list_definitions(alias):
                tool = build_tool(alias, definition, self._page_size_max)
                if tool.name in targets:
                    continue  # sanitized-name collision — first definition wins
                tools.append(tool)
                targets[tool.name] = QueryTarget(db_alias=alias, query_name=definition["name"])
        self._tools = tools
        self._targets = targets
        self._loaded_at = time.monotonic()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/test_catalog.py -v`
Expected: 5 PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add mcp_server/catalog.py tests/mcp/test_catalog.py
git commit -m "feat(mcp): dynamic tool catalog generated from SQL definitions"
```

---

### Task 4: Server core — tool listing and dispatch

**Files:**
- Create: `mcp_server/server.py`
- Test: `tests/mcp/test_server.py`

**Interfaces:**
- Consumes: `ApiHubClient` (Task 2), `ToolCatalog` / `QueryTarget` (Task 3).
- Produces:
  - `STATIC_TOOLS: list[Tool]` — `list_databases`, `describe_table`, `refresh_query_tools`.
  - `async list_all_tools(catalog: ToolCatalog) -> list[Tool]` — static + dynamic.
  - `async dispatch_tool(client: ApiHubClient, catalog: ToolCatalog, name: str, args: dict) -> dict | list` — routes to client methods; clamps `page_size`; raises `ApiHubError` for unknown tools (the mcp low-level server converts raised exceptions into `isError` tool results).
  - `create_server(client: ApiHubClient, catalog: ToolCatalog) -> mcp.server.Server` — registers the two handlers, serializes results as JSON `TextContent`.

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_server.py`:

```python
"""Tool listing and dispatch logic (transport-independent)."""

import httpx
import pytest

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, ApiHubError
from mcp_server.server import STATIC_TOOLS, create_server, dispatch_tool, list_all_tools

DEFINITION = {
    "name": "order_summary",
    "db_alias": "pg_test",
    "description": "Open orders",
    "permission": "query:orders",
    "params": {"status": {"type": "string", "required": True, "description": ""}},
}


def _fixture(seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/v1/pg_test/queries/definitions":
            return httpx.Response(200, json=[DEFINITION])
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json=[{"alias": "pg_test", "db_type": "postgresql", "description": "t"}])
        if request.url.path == "/api/v1/pg_test/queries/execute":
            return httpx.Response(200, json={"success": True, "total": 1, "columns": ["A"], "data": [{"A": 1}]})
        if request.url.path == "/api/v1/pg_test/schemas/describe/orders":
            return httpx.Response(200, json={"table": "orders", "columns": []})
        return httpx.Response(404, json={"detail": "nope"})

    client = ApiHubClient("http://hub", transport=httpx.MockTransport(handler))
    catalog = ToolCatalog(client, db_aliases=["pg_test"], page_size_max=100)
    return client, catalog


@pytest.mark.asyncio
async def test_list_all_tools_static_plus_dynamic():
    client, catalog = _fixture()
    tools = await list_all_tools(catalog)
    names = [t.name for t in tools]
    assert "list_databases" in names
    assert "describe_table" in names
    assert "refresh_query_tools" in names
    assert "query_pg_test_order_summary" in names
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_query_tool_clamps_page_size():
    seen = []
    client, catalog = _fixture(seen)
    await catalog.tools()
    result = await dispatch_tool(
        client, catalog, "query_pg_test_order_summary", {"status": "OPEN", "page": 1, "page_size": 9999}
    )
    assert result["success"] is True
    import json

    body = json.loads([c for c in seen if c[1].endswith("/execute")][0][2])
    assert body["page_size"] == 100  # clamped to catalog.page_size_max
    assert body["params"] == {"status": "OPEN"}  # paging args not forwarded as bind params
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_static_tools():
    client, catalog = _fixture()
    dbs = await dispatch_tool(client, catalog, "list_databases", {})
    assert dbs[0]["alias"] == "pg_test"
    desc = await dispatch_tool(client, catalog, "describe_table", {"db": "pg_test", "table": "orders"})
    assert desc["table"] == "orders"
    refreshed = await dispatch_tool(client, catalog, "refresh_query_tools", {})
    assert refreshed == {"query_tools": 1}
    await client.aclose()


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises():
    client, catalog = _fixture()
    await catalog.tools()
    with pytest.raises(ApiHubError) as exc:
        await dispatch_tool(client, catalog, "query_nope_nope", {})
    assert exc.value.status_code == 404
    await client.aclose()


def test_create_server_returns_named_server():
    client, catalog = _fixture()
    server = create_server(client, catalog)
    assert server.name == "oracle-api-hub"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError` on `mcp_server.server`

- [ ] **Step 3: Write the implementation**

Create `mcp_server/server.py`:

```python
"""MCP server core: dynamic query tools + static schema tools.

Every tool call is proxied to the REST API (thin proxy) — see client.py.
Handlers are plain functions so tests can call them without a transport.
"""

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient, ApiHubError

STATIC_TOOLS = [
    Tool(
        name="list_databases",
        description="List the configured database connections (alias, engine type, description).",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="describe_table",
        description=(
            "Return column metadata and primary-key info for a table or view. "
            "Supports schema-qualified names (e.g. HR.EMPLOYEES)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "db": {"type": "string", "description": "Database alias (see list_databases)"},
                "table": {"type": "string", "description": "Table or view name"},
            },
            "required": ["db", "table"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="refresh_query_tools",
        description=(
            "Re-read SQL definitions from the API Hub and rebuild the query tool list. "
            "Call this when a definition was just added or changed."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
]


async def list_all_tools(catalog: ToolCatalog) -> list[Tool]:
    return STATIC_TOOLS + await catalog.tools()


async def dispatch_tool(client: ApiHubClient, catalog: ToolCatalog, name: str, args: dict) -> dict | list:
    args = dict(args or {})
    if name == "list_databases":
        return await client.list_databases()
    if name == "describe_table":
        return await client.describe_table(args["db"], args["table"])
    if name == "refresh_query_tools":
        tools = await catalog.tools(force=True)
        return {"query_tools": len(tools)}

    target = catalog.target(name)
    if target is None:
        raise ApiHubError(404, f"Unknown tool: {name}")
    page = int(args.pop("page", 1) or 1)
    page_size = min(int(args.pop("page_size", 50) or 50), catalog.page_size_max)
    return await client.execute_query(target.db_alias, target.query_name, args, page=page, page_size=page_size)


def create_server(client: ApiHubClient, catalog: ToolCatalog) -> Server:
    server = Server("oracle-api-hub")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return await list_all_tools(catalog)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Exceptions raised here are converted by the SDK into isError results.
        result = await dispatch_tool(client, catalog, name, arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    return server
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/test_server.py -v`
Expected: 5 PASS

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest -v`
Expected: all tests pass (pre-existing + new)

- [ ] **Step 6: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add mcp_server/server.py tests/mcp/test_server.py
git commit -m "feat(mcp): server core with dynamic tool listing and dispatch"
```

---

### Task 5: stdio entrypoint (`python -m mcp_server`)

**Files:**
- Create: `mcp_server/__main__.py`

**Interfaces:**
- Consumes: `mcp_settings` (Task 1), `ApiHubClient`/`static_headers` (Task 2), `ToolCatalog` (Task 3), `create_server` (Task 4).
- Produces: runnable module — `python -m mcp_server` speaks MCP over stdio; auth via `MCP_API_HUB_API_KEY`.

- [ ] **Step 1: Write the implementation** (entrypoints are verified by smoke test, not unit test)

Create `mcp_server/__main__.py`:

```python
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
```

- [ ] **Step 2: Smoke test against a running API Hub**

Prerequisites: API Hub running locally (`uvicorn app.main:app --port 8000`) with at least one SQL definition, and a service-account API key (create via Admin UI or `POST /api/v1/api-keys`).

```bash
# Windows Git Bash — send an initialize + tools/list over stdio and inspect output
export MCP_API_HUB_URL=http://localhost:8000
export MCP_API_HUB_API_KEY=<your-key>
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python -m mcp_server
```

Expected: two JSON-RPC responses; the `tools/list` result contains `list_databases`, `describe_table`, `refresh_query_tools`, and one `query_<alias>_<name>` per registered definition.

- [ ] **Step 3: Register with Claude Code and verify interactively**

```bash
claude mcp add apihub --transport stdio \
  -e MCP_API_HUB_URL=http://localhost:8000 \
  -e MCP_API_HUB_API_KEY=<your-key> \
  -- python -m mcp_server
```

In a Claude Code session, ask: "用 list_databases 看有哪些資料庫" — expect a successful tool call.

- [ ] **Step 4: Commit**

```bash
ruff check mcp_server/ && ruff format mcp_server/
git add mcp_server/__main__.py
git commit -m "feat(mcp): stdio entrypoint for Claude Code/Desktop"
```

---

### Task 6: Streamable HTTP transport with credential passthrough

**Files:**
- Create: `mcp_server/http_app.py`
- Test: `tests/mcp/test_http_app.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces:
  - contextvar-based provider `passthrough_headers() -> dict` and `CredentialPassthroughMiddleware` (pure ASGI middleware) — copies the incoming request's `X-API-Key` or `Authorization` header into a contextvar; `ApiHubClient` forwards it on every REST call, so **the API Hub itself validates the credential** and RBAC/audit apply per caller. No token validation happens in the MCP server in Phase 1.
  - `create_http_app() -> ASGI app` — routes: `GET /health`, `Mount /mcp` → `StreamableHTTPSessionManager(stateless=True)`. Stateless mode is **required**: tool handlers run inside the HTTP request's task context, which is what makes the contextvar passthrough correct. (Task 10 wires the OAuth gate in front of this same app.)
  - Module-level `app = create_http_app()` for `uvicorn mcp_server.http_app:app`.

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_http_app.py`:

```python
"""HTTP transport: health route and credential passthrough middleware."""

import httpx
import pytest

from mcp_server.http_app import CredentialPassthroughMiddleware, create_http_app, passthrough_headers


@pytest.mark.asyncio
async def test_health_endpoint():
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_passthrough_middleware_captures_api_key_header():
    captured = {}

    async def inner_app(scope, receive, send):
        captured["headers"] = passthrough_headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = CredentialPassthroughMiddleware(inner_app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        await client.get("/anything", headers={"X-API-Key": "sk-abc"})
    assert captured["headers"] == {"X-API-Key": "sk-abc"}


@pytest.mark.asyncio
async def test_passthrough_middleware_prefers_api_key_falls_back_to_bearer():
    captured = {}

    async def inner_app(scope, receive, send):
        captured["headers"] = passthrough_headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = CredentialPassthroughMiddleware(inner_app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        await client.get("/x", headers={"Authorization": "Bearer sk-tok"})
        assert captured["headers"] == {"Authorization": "Bearer sk-tok"}
        await client.get("/x")
        assert captured["headers"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_http_app.py -v`
Expected: FAIL with `ModuleNotFoundError` on `mcp_server.http_app`

- [ ] **Step 3: Write the implementation**

Create `mcp_server/http_app.py`:

```python
"""Streamable HTTP transport for the MCP server.

Phase 1 auth = credential passthrough: the caller's ``X-API-Key`` (or
``Authorization: Bearer <api key>``) header is forwarded on every proxied
REST call, so the API Hub validates the credential and applies RBAC, audit,
and rate limiting per caller. The MCP server itself validates nothing.

The session manager runs in stateless mode on purpose: each HTTP request
executes tool handlers inside its own task context, which is what makes the
contextvar-based header passthrough correct. Do not switch to stateful mode
without replacing the passthrough with per-session credential binding
(Phase 2 OAuth does exactly that).
"""

import contextlib
import contextvars

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_server.catalog import ToolCatalog
from mcp_server.client import ApiHubClient
from mcp_server.config import mcp_settings
from mcp_server.server import create_server

_request_headers: contextvars.ContextVar[dict] = contextvars.ContextVar("mcp_request_headers", default={})


def passthrough_headers() -> dict:
    """Header provider: whatever credential the current HTTP request carried."""
    return _request_headers.get()


class CredentialPassthroughMiddleware:
    """Copy the caller's API credential header into a contextvar for ApiHubClient."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        cred: dict = {}
        if headers.get("x-api-key"):
            cred = {"X-API-Key": headers["x-api-key"]}
        elif headers.get("authorization"):
            cred = {"Authorization": headers["authorization"]}
        token = _request_headers.set(cred)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_headers.reset(token)


def create_http_app():
    client = ApiHubClient(mcp_settings.api_hub_url, headers_provider=passthrough_headers)
    aliases = [a.strip() for a in mcp_settings.db_aliases.split(",") if a.strip()] or None
    catalog = ToolCatalog(
        client,
        db_aliases=aliases,
        page_size_max=mcp_settings.page_size_max,
        ttl_seconds=mcp_settings.catalog_ttl_seconds,
    )
    server = create_server(client, catalog)
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    async def handle_mcp(scope, receive, send):
        await session_manager.handle_request(scope, receive, send)

    async def health(request):
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield
        await client.aclose()

    starlette_app = Starlette(
        routes=[Route("/health", health), Mount("/mcp", app=handle_mcp)],
        lifespan=lifespan,
    )
    return CredentialPassthroughMiddleware(starlette_app)


app = create_http_app()
```

Note: the catalog's own REST calls (`list_definitions`) also run inside a request context (triggered by `tools/list`), so they carry the caller's credential — a caller only sees tools their key can enumerate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/test_http_app.py -v`
Expected: 3 PASS

- [ ] **Step 5: Manual end-to-end check over HTTP**

With the API Hub on :8000 and `uvicorn mcp_server.http_app:app --port 8810`:

```bash
curl -s http://localhost:8810/health
# → {"status":"ok"}
claude mcp add apihub-http --transport http http://localhost:8810/mcp \
  --header "X-API-Key: <your-key>"
```

In Claude Code, verify `list_databases` and one dynamic query tool work.

- [ ] **Step 6: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add mcp_server/http_app.py tests/mcp/test_http_app.py
git commit -m "feat(mcp): Streamable HTTP transport with API-key passthrough"
```

---

### Task 7: Phase 1 docs + deployment wiring

**Files:**
- Create: `mcp_server/README.md`
- Create: `.env.mcp.example`
- Modify: `docker-compose.yaml` (add `mcp` service next to the existing `api` service)
- Modify: `CLAUDE.md` (add MCP server section under Architecture)

- [ ] **Step 1: Write `mcp_server/README.md`**

```markdown
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

    # or HTTP
    claude mcp add apihub-http --transport http http://localhost:8810/mcp \
      --header "X-API-Key: <key>"

## Settings

All via `MCP_*` env vars or `.env.mcp` — see `mcp_server/config.py`.
Phase 2 (OAuth 2.1 Resource Server, isap-auth) is toggled by `MCP_OAUTH_ENABLED`.
```

- [ ] **Step 2: Write `.env.mcp.example`**

```bash
# Oracle API Hub MCP server settings (copy to .env.mcp)
MCP_API_HUB_URL=http://localhost:8000
# stdio transport only — service-account API key
MCP_API_HUB_API_KEY=
# empty = expose every configured database alias
MCP_DB_ALIASES=
MCP_PAGE_SIZE_MAX=100
MCP_CATALOG_TTL_SECONDS=300
# Phase 2 (OAuth RS) — leave disabled until isap-auth integration is deployed
MCP_OAUTH_ENABLED=false
MCP_PUBLIC_URL=
MCP_AUTH_ISSUER=
MCP_INTROSPECTION_URL=
MCP_INTROSPECTION_SECRET=
MCP_BRIDGE_SECRET=
```

- [ ] **Step 3: Add the compose service**

In root `docker-compose.yaml`, add alongside the `api` service (match the existing service's `build`/image and env-file conventions in that file):

```yaml
  mcp:
    build: .
    command: uvicorn mcp_server.http_app:app --host 0.0.0.0 --port 8810
    environment:
      MCP_API_HUB_URL: http://api:8000
    ports:
      - "8810:8810"
    depends_on:
      - api
```

Verify: `docker-compose config` parses cleanly.

- [ ] **Step 4: Update `CLAUDE.md`**

Add to the Architecture section:

```markdown
- **MCP server** (`mcp_server/`): standalone MCP process exposing SQL definitions as
  dynamic tools. Thin HTTP proxy over the REST API (never imports `app/` or touches
  DBs directly). stdio (`python -m mcp_server`) + Streamable HTTP
  (`uvicorn mcp_server.http_app:app`). Settings: `MCP_*` env vars / `.env.mcp`.
```

- [ ] **Step 5: Commit**

```bash
git add mcp_server/README.md .env.mcp.example docker-compose.yaml CLAUDE.md
git commit -m "docs(mcp): README, env example, compose service, CLAUDE.md"
```

**Phase 1 complete — deliverable:** Claude Code/Desktop can query every registered SQL definition through MCP with an API key, over stdio or HTTP.

---

## Phase 2 — OAuth 2.1 Resource Server (isap-auth), per-user SSO identity

> Copies the proven ITSM-PM pattern (see `C:\app\ITSM-PM\mcp-technical-report.html` §10).
> isap-auth AS, gateway well-known rewrites, and test templates already exist (platform-level).
> Prereq for deployment: API Hub runs behind the isap gateway (`deploy/isap/`).

### Task 8: Identity-bridge endpoint (`POST /api/v1/auth/sso-token`)

**Files:**
- Modify: `app/core/config.py` (add two settings)
- Modify: `app/api/v1/auth.py` (add endpoint)
- Create: `app/schemas/sso_token.py`
- Test: `tests/test_sso_token_bridge.py`

**Interfaces:**
- Consumes: `create_access_token` from `app/core/security.py:25`, `get_db` from `app/db/session.py`.
- Produces: `POST /api/v1/auth/sso-token` — internal-only endpoint the MCP RS calls **after** introspection succeeds. Guarded by header `X-Bridge-Secret` matching `settings.mcp_bridge_secret` (empty setting = endpoint disabled, 404). Body `{"username": str, "email": str | ""}` → `{"access_token": str, "token_type": "bearer"}`. Rejects unknown users (403) unless `settings.mcp_bridge_autocreate` is true (then creates an active user with **no roles** — RBAC denies everything until an admin grants permissions). Always rejects service accounts and inactive users (403). This is the API Hub equivalent of ITSM-PM's `/base/sso_token`; never expose it through the gateway.

- [ ] **Step 1: Add settings**

In `app/core/config.py`, after the `api_key_last_used_throttle_seconds` block add:

```python
    # MCP identity bridge (Phase 2 OAuth): shared secret required in X-Bridge-Secret.
    # Empty (default) disables the endpoint entirely.
    mcp_bridge_secret: str = ""
    # Auto-create a role-less local user on first SSO login via the bridge.
    mcp_bridge_autocreate: bool = False
```

- [ ] **Step 2: Add the schema**

Create `app/schemas/sso_token.py`:

```python
"""Schemas for the MCP identity-bridge endpoint."""

from pydantic import BaseModel, Field


class SsoTokenRequest(BaseModel):
    """Verified SSO claims forwarded by the MCP Resource Server after introspection."""

    username: str = Field(..., min_length=1)
    email: str = ""


class SsoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_sso_token_bridge.py` (follows the existing direct-call + fake-DB convention of `tests/test_api_key_endpoints.py`):

```python
"""Identity bridge: verified SSO claims -> local JWT. Internal-only, secret-gated."""

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.v1.auth import sso_token_bridge
from app.core.config import settings
from app.models.user import User
from app.schemas.sso_token import SsoTokenRequest


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, user=None):
        self._user = user
        self.added = []

    def query(self, model):
        return _FakeQuery(self._user)

    def add(self, obj):
        self.added.append(obj)
        self._user = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _payload():
    return SsoTokenRequest(username="alice", email="alice@example.com")


def test_disabled_when_secret_unset(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "")
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="anything", db=_FakeDB())
    assert exc.value.status_code == 404


def test_wrong_secret_403(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="wrong", db=_FakeDB())
    assert exc.value.status_code == 403


def test_known_active_user_gets_jwt(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    user = User(username="alice", is_active=True, is_service_account=False)
    resp = sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=_FakeDB(user=user))
    claims = jwt.decode(resp.access_token, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"
    assert resp.token_type == "bearer"


def test_service_account_rejected(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    user = User(username="alice", is_active=True, is_service_account=True)
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=_FakeDB(user=user))
    assert exc.value.status_code == 403


def test_unknown_user_403_when_autocreate_off(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", False)
    with pytest.raises(HTTPException) as exc:
        sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=_FakeDB(user=None))
    assert exc.value.status_code == 403


def test_unknown_user_created_roleless_when_autocreate_on(monkeypatch):
    monkeypatch.setattr(settings, "mcp_bridge_secret", "s3cret")
    monkeypatch.setattr(settings, "mcp_bridge_autocreate", True)
    db = _FakeDB(user=None)
    resp = sso_token_bridge(_payload(), x_bridge_secret="s3cret", db=db)
    assert len(db.added) == 1
    created = db.added[0]
    assert created.username == "alice"
    assert created.is_active is True
    assert created.is_superuser is False
    claims = jwt.decode(resp.access_token, settings.secret_key, algorithms=[settings.algorithm])
    assert claims["sub"] == "alice"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_sso_token_bridge.py -v`
Expected: FAIL with `ImportError: cannot import name 'sso_token_bridge'`

- [ ] **Step 5: Write the endpoint**

In `app/api/v1/auth.py`, add imports (`Header` from fastapi; `secrets`; `SsoTokenRequest`, `SsoTokenResponse` from `app.schemas.sso_token`; `create_access_token` from `app.core.security` if not already imported) and append:

```python
@router.post("/sso-token", response_model=SsoTokenResponse, include_in_schema=False)
def sso_token_bridge(
    payload: SsoTokenRequest,
    x_bridge_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Identity bridge for the MCP OAuth Resource Server (internal only).

    The MCP RS calls this AFTER validating a Bearer token via isap-auth
    introspection; the claims here are therefore already verified. Guarded by
    a shared secret; never route this path through the public gateway.
    """
    if not settings.mcp_bridge_secret:
        raise HTTPException(status_code=404, detail="Not found")
    if not secrets.compare_digest(x_bridge_secret, settings.mcp_bridge_secret):
        raise HTTPException(status_code=403, detail="Invalid bridge secret")

    user = db.query(User).filter(User.username == payload.username).first()
    if user is None:
        if not settings.mcp_bridge_autocreate:
            raise HTTPException(status_code=403, detail="User not provisioned")
        user = User(
            username=payload.username,
            email=payload.email or None,
            hashed_password="",  # SSO users have no local password
            is_active=True,
            is_superuser=False,
            is_service_account=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    if user.is_service_account or not user.is_active:
        raise HTTPException(status_code=403, detail="Account not eligible for SSO bridge")

    token = create_access_token(data={"sub": user.username})
    return SsoTokenResponse(access_token=token)
```

Note: check `User` model field names before committing (e.g. whether `email` exists / `hashed_password` is nullable); adjust the autocreate constructor to the actual columns — the tests pin only `username`, `is_active`, `is_superuser`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_sso_token_bridge.py -v`
Expected: 6 PASS. Then `pytest -v` for regressions.

- [ ] **Step 7: Lint and commit**

```bash
ruff check app/ tests/ && ruff format app/api/v1/auth.py app/schemas/sso_token.py tests/test_sso_token_bridge.py
git add app/core/config.py app/api/v1/auth.py app/schemas/sso_token.py tests/test_sso_token_bridge.py
git commit -m "feat(auth): MCP identity-bridge endpoint (verified SSO claims -> local JWT)"
```

---

### Task 9: OAuth Resource Server module (`mcp_server/oauth.py`)

**Files:**
- Create: `mcp_server/oauth.py`
- Test: `tests/mcp/test_oauth.py`

**Interfaces:**
- Consumes: `mcp_settings` fields `introspection_url`, `introspection_secret`, `bridge_secret`, `api_hub_url`, `public_url`, `auth_issuer`.
- Produces:
  - `protected_resource_metadata() -> dict` — `{"resource": mcp_settings.public_url, "authorization_servers": [mcp_settings.auth_issuer]}` (RFC 9728).
  - `challenge_header() -> str` — `f'Bearer resource_metadata="{mcp_settings.public_url}/.well-known/oauth-protected-resource"'`.
  - `TokenExchanger(transport=None)` with `async exchange(bearer_token: str) -> str | None` — introspects the token (`POST introspection_url`, form `{"token": ..., "secret": introspection_secret}`); on `active: true` calls the bridge (`POST {api_hub_url}/api/v1/auth/sso-token` with `X-Bridge-Secret`) and returns the local JWT; returns `None` on inactive/failed. Results cached in-memory for 60 s keyed by SHA-256 of the bearer token (mirrors ITSM-PM's 60 s introspection cache).

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_oauth.py`:

```python
"""OAuth RS: metadata, challenge header, introspection -> bridge JWT exchange."""

import httpx
import pytest

from mcp_server.config import mcp_settings
from mcp_server.oauth import TokenExchanger, challenge_header, protected_resource_metadata


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "auth_issuer", "https://isap.example.com/auth")
    monkeypatch.setattr(mcp_settings, "introspection_url", "http://isap-auth:9997/auth/api/v1/introspect")
    monkeypatch.setattr(mcp_settings, "introspection_secret", "isecret")
    monkeypatch.setattr(mcp_settings, "bridge_secret", "bsecret")
    monkeypatch.setattr(mcp_settings, "api_hub_url", "http://api:8000")


def test_metadata_and_challenge():
    meta = protected_resource_metadata()
    assert meta == {
        "resource": "https://isap.example.com/apihub/mcp",
        "authorization_servers": ["https://isap.example.com/auth"],
    }
    assert 'resource_metadata="https://isap.example.com/apihub/mcp/.well-known/oauth-protected-resource"' in (
        challenge_header()
    )


def _transport(introspect_response, bridge_response=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if "introspect" in str(request.url):
            return httpx.Response(200, json=introspect_response)
        if "sso-token" in str(request.url):
            assert request.headers["X-Bridge-Secret"] == "bsecret"
            return httpx.Response(200, json=bridge_response or {"access_token": "local-jwt", "token_type": "bearer"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_active_token_exchanged_for_local_jwt():
    exchanger = TokenExchanger(transport=_transport({"active": True, "username": "alice", "email": "a@x.com"}))
    jwt_token = await exchanger.exchange("opaque-token")
    assert jwt_token == "local-jwt"
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_inactive_token_returns_none():
    exchanger = TokenExchanger(transport=_transport({"active": False}))
    assert await exchanger.exchange("bad-token") is None
    await exchanger.aclose()


@pytest.mark.asyncio
async def test_exchange_result_cached_60s():
    calls: list[str] = []
    exchanger = TokenExchanger(
        transport=_transport({"active": True, "username": "alice", "email": ""}, calls=calls)
    )
    await exchanger.exchange("opaque-token")
    await exchanger.exchange("opaque-token")
    assert len([c for c in calls if "introspect" in c]) == 1  # second call served from cache
    await exchanger.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError` on `mcp_server.oauth`

- [ ] **Step 3: Write the implementation**

Create `mcp_server/oauth.py`:

```python
"""OAuth 2.1 Resource Server pieces for the MCP HTTP transport.

Pattern copied from the ITSM-PM MCP server (mcp-technical-report §10.4):
401 challenge with RFC 9728 resource metadata -> isap-auth introspection
(60 s cache) -> identity-bridge exchange for a local API Hub JWT.
"""

import hashlib
import time

import httpx

from mcp_server.config import mcp_settings

_CACHE_TTL = 60.0


def protected_resource_metadata() -> dict:
    return {
        "resource": mcp_settings.public_url,
        "authorization_servers": [mcp_settings.auth_issuer],
    }


def challenge_header() -> str:
    return f'Bearer resource_metadata="{mcp_settings.public_url}/.well-known/oauth-protected-resource"'


class TokenExchanger:
    """Introspect a Bearer token and exchange verified claims for a local JWT."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(timeout=10.0, transport=transport)
        self._cache: dict[str, tuple[float, str | None]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def exchange(self, bearer_token: str) -> str | None:
        key = hashlib.sha256(bearer_token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        result = await self._exchange_uncached(bearer_token)
        self._cache[key] = (now, result)
        # opportunistic cleanup of expired entries
        self._cache = {k: v for k, v in self._cache.items() if (now - v[0]) < _CACHE_TTL}
        return result

    async def _exchange_uncached(self, bearer_token: str) -> str | None:
        try:
            resp = await self._http.post(
                mcp_settings.introspection_url,
                data={"token": bearer_token, "secret": mcp_settings.introspection_secret},
            )
            claims = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if resp.status_code != 200 or not claims.get("active"):
            return None

        try:
            bridge = await self._http.post(
                f"{mcp_settings.api_hub_url.rstrip('/')}/api/v1/auth/sso-token",
                json={"username": claims.get("username", ""), "email": claims.get("email") or ""},
                headers={"X-Bridge-Secret": mcp_settings.bridge_secret},
            )
        except httpx.HTTPError:
            return None
        if bridge.status_code != 200:
            return None
        return bridge.json().get("access_token")
```

Note: confirm the introspection request shape (form field names, auth header) against isap-auth's `/auth/api/v1/introspect` implementation (`C:\app\isap-auth\app\api\v1\auth\auth.py`) before deployment — the tests pin our side only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/test_oauth.py -v`
Expected: 4 PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add mcp_server/oauth.py tests/mcp/test_oauth.py
git commit -m "feat(mcp): OAuth RS token exchange (introspection + identity bridge)"
```

---

### Task 10: OAuth gate in the HTTP app

**Files:**
- Modify: `mcp_server/http_app.py`
- Test: `tests/mcp/test_http_app_oauth.py`

**Interfaces:**
- Consumes: `TokenExchanger`, `challenge_header`, `protected_resource_metadata` (Task 9); `_request_headers` contextvar (Task 6).
- Produces: `OAuthGateMiddleware(app, exchanger)` — for paths under `/mcp`: missing/invalid Bearer → 401 with `WWW-Authenticate: <challenge_header()>`; valid → sets contextvar to `{"Authorization": f"Bearer {local_jwt}"}` so all proxied REST calls run as the SSO user. `create_http_app()` adds route `GET /.well-known/oauth-protected-resource` (unauthenticated) and selects the middleware by `mcp_settings.oauth_enabled` (false → Phase 1 passthrough, unchanged).

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_http_app_oauth.py`:

```python
"""OAuth gate: 401 challenge, metadata route, JWT injection on success."""

import httpx
import pytest

from mcp_server.config import mcp_settings
from mcp_server.http_app import OAuthGateMiddleware, create_http_app, passthrough_headers


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(mcp_settings, "oauth_enabled", True)
    monkeypatch.setattr(mcp_settings, "public_url", "https://isap.example.com/apihub/mcp")
    monkeypatch.setattr(mcp_settings, "auth_issuer", "https://isap.example.com/auth")


class _FakeExchanger:
    def __init__(self, result):
        self._result = result

    async def exchange(self, token):
        return self._result if token == "good" else None


def _inner_capture(captured):
    async def inner_app(scope, receive, send):
        captured["headers"] = passthrough_headers()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return inner_app


@pytest.mark.asyncio
async def test_missing_bearer_gets_401_challenge():
    app = OAuthGateMiddleware(_inner_capture({}), _FakeExchanger("jwt"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp")
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers["WWW-Authenticate"]


@pytest.mark.asyncio
async def test_invalid_bearer_gets_401():
    app = OAuthGateMiddleware(_inner_capture({}), _FakeExchanger("jwt"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_bearer_injects_local_jwt():
    captured = {}
    app = OAuthGateMiddleware(_inner_capture(captured), _FakeExchanger("local-jwt"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.post("/mcp", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert captured["headers"] == {"Authorization": "Bearer local-jwt"}


@pytest.mark.asyncio
async def test_wellknown_route_is_public():
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert resp.json()["authorization_servers"] == ["https://isap.example.com/auth"]


@pytest.mark.asyncio
async def test_health_stays_public_under_oauth():
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp/test_http_app_oauth.py -v`
Expected: FAIL with `ImportError: cannot import name 'OAuthGateMiddleware'`

- [ ] **Step 3: Implement**

In `mcp_server/http_app.py`:

1. Add imports: `from starlette.responses import JSONResponse, Response` (extend existing import) and `from mcp_server.oauth import TokenExchanger, challenge_header, protected_resource_metadata`.

2. Add the middleware class:

```python
class OAuthGateMiddleware:
    """OAuth 2.1 Resource Server gate for /mcp (Phase 2).

    Validates the caller's Bearer token via isap-auth introspection, exchanges
    it for a local API Hub JWT through the identity bridge, and injects that
    JWT into the passthrough contextvar so every proxied REST call runs with
    the SSO user's own RBAC and audit identity.
    """

    def __init__(self, app, exchanger: TokenExchanger):
        self.app = app
        self.exchanger = exchanger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        local_jwt = None
        if auth.startswith("Bearer "):
            local_jwt = await self.exchanger.exchange(auth[len("Bearer ") :])
        if local_jwt is None:
            response = Response(
                status_code=401,
                content=b"",
                headers={"WWW-Authenticate": challenge_header()},
            )
            await response(scope, receive, send)
            return
        token = _request_headers.set({"Authorization": f"Bearer {local_jwt}"})
        try:
            await self.app(scope, receive, send)
        finally:
            _request_headers.reset(token)
```

3. In `create_http_app()`, add the metadata route and select the gate:

```python
    async def oauth_metadata(request):
        return JSONResponse(protected_resource_metadata())

    starlette_app = Starlette(
        routes=[
            Route("/health", health),
            Route("/.well-known/oauth-protected-resource", oauth_metadata),
            Mount("/mcp", app=handle_mcp),
        ],
        lifespan=lifespan,
    )
    if mcp_settings.oauth_enabled:
        return OAuthGateMiddleware(starlette_app, TokenExchanger())
    return CredentialPassthroughMiddleware(starlette_app)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/mcp/ -v`
Expected: all MCP tests pass, including the Phase 1 ones (passthrough path unchanged when `oauth_enabled` is false).

- [ ] **Step 5: Lint and commit**

```bash
ruff check mcp_server/ tests/mcp/ && ruff format mcp_server/ tests/mcp/
git add mcp_server/http_app.py tests/mcp/test_http_app_oauth.py
git commit -m "feat(mcp): OAuth RS gate on Streamable HTTP transport"
```

---

### Task 11: Gateway + deployment wiring and end-to-end verification

**Files:**
- Modify: `deploy/isap/docker-compose.yml` (add `apihub-mcp` service)
- Create: gateway conf for `/apihub/mcp/*` (copy ITSM-PM's `itpm-mcp.conf` pattern from `C:\app\ITSM-PM\deploy\infrastructure\gateway\conf.d\apps\`)
- Modify: gateway `oauth-wellknown.conf` (add `/.well-known/oauth-protected-resource/apihub/mcp` rewrite)
- Modify: `deploy/isap/.env` additions documented in `mcp_server/README.md`

This task is configuration + manual verification (no pytest). Follow ITSM-PM report §10.6 steps 8–10 and §10.7 pitfalls.

- [ ] **Step 1: Add the MCP service to `deploy/isap/docker-compose.yml`**

Mirror the existing `api` service definition (image, networks incl. `gateway-net`, env file), with:

```yaml
  apihub-mcp:
    # same image/build as the api service
    command: uvicorn mcp_server.http_app:app --host 0.0.0.0 --port 8810
    environment:
      MCP_API_HUB_URL: http://api:8000          # docker-internal, bridge stays private
      MCP_OAUTH_ENABLED: "true"
      MCP_PUBLIC_URL: https://isap.sercomm.com/apihub/mcp
      MCP_AUTH_ISSUER: https://isap.sercomm.com/auth
      MCP_INTROSPECTION_URL: http://isap-auth:9997/auth/api/v1/introspect
      MCP_INTROSPECTION_SECRET: ${OAUTH_INTROSPECTION_SECRET}
      MCP_BRIDGE_SECRET: ${MCP_BRIDGE_SECRET}
```

And add `MCP_BRIDGE_SECRET` (same value) to the `api` service env as `MCP_BRIDGE_SECRET` → maps to `settings.mcp_bridge_secret`... **Note:** the app reads it as `MCP_BRIDGE_SECRET` only if pydantic settings field is `mcp_bridge_secret` (it is — field names map case-insensitively to env vars). Set `MCP_BRIDGE_AUTOCREATE` per site policy (default false).

- [ ] **Step 2: Gateway config**

Copy `itpm-mcp.conf` → `apihub-mcp.conf` in the gateway's `conf.d/apps/`, replacing the upstream with `apihub-mcp:8810` and the location with `/apihub/mcp/` (prefix stripped on proxy_pass), keeping: SSE/long-connection settings (`proxy_buffering off`, long `proxy_read_timeout`), RFC1918 allowlist. Add to the root well-known conf:

```nginx
location = /.well-known/oauth-protected-resource/apihub/mcp {
    rewrite ^ /.well-known/oauth-protected-resource break;
    proxy_pass http://apihub-mcp:8810;
}
```

Reload: `docker exec isap-gateway nginx -s reload`

- [ ] **Step 3: End-to-end verification checklist** (from ITSM-PM §10.6 step 10)

```bash
curl -s https://isap.sercomm.com/auth/.well-known/oauth-authorization-server   # 200 (platform, already live)
curl -s https://isap.sercomm.com/.well-known/oauth-protected-resource/apihub/mcp  # 200, lists auth issuer
curl -si https://isap.sercomm.com/apihub/mcp/mcp -X POST                        # 401 + WWW-Authenticate challenge
```

Then from a client:
1. `claude mcp add apihub --transport http https://isap.sercomm.com/apihub/mcp/mcp` → browser SSO flow completes.
2. `list_databases` tool call succeeds; audit log records the SSO user's username.
3. A query tool respects the user's RBAC (403 surfaces as a tool error for unpermitted definitions).
4. SSO logout → next tool call gets 401 (introspection `active: false`).

- [ ] **Step 4: Commit**

```bash
git add deploy/
git commit -m "deploy(mcp): apihub-mcp service, gateway routes, OAuth well-known rewrite"
```

---

## Self-Review Notes

- **Spec coverage:** dynamic per-definition tools (Tasks 3–4), thin proxy (Task 2), stdio + HTTP transports (Tasks 5–6), API-key phase (Tasks 1–7), OAuth RS phase reserved-then-implemented (Tasks 8–11), schema tools (Task 4), page clamp (Tasks 3–4), no writes/procedures (scope held). ✓
- **Type consistency:** `ApiHubError(status_code, detail)`, `headers_provider: Callable[[], dict]`, `ToolCatalog.tools(force=...)`, `catalog.page_size_max`, `passthrough_headers()`, `TokenExchanger.exchange() -> str | None` used consistently across tasks. ✓
- **Known verify-before-deploy items (flagged in tasks, not placeholders):** `User` model columns for autocreate (Task 8 Step 5 note); isap-auth introspection request shape (Task 9 Step 3 note); exact compose/gateway file conventions (Tasks 7/11 mirror existing files).
