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

    async def ping(self, timeout: float = 3.0) -> None:
        """Raise ApiHubError if the REST API is not answering.

        Deliberately short-timeout and unauthenticated: the Hub's ``/health``
        needs no credential, and a health probe must not inherit the 30 s
        request timeout — a hung upstream would hang the probe with it.
        """
        await self._request("GET", "/health", timeout=timeout)

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

    async def catalog_version(self) -> str:
        """Opaque hash of everything that shapes the tool catalog (see app/api/v1/mcp.py)."""
        result = await self._request("GET", "/mcp/catalog-version")
        return str(result.get("version", ""))

    async def list_definitions(self, db: str) -> list[dict]:
        # permitted_only: the tool catalog is pushed into an LLM's context, so an
        # unfiltered list would hand every caller the names, descriptions and
        # parameters of definitions they hold no query:<name> permission for.
        # Execution was always gated; discovery was not.
        return await self._request("GET", f"/{db}/queries/definitions", params={"permitted_only": "true"})

    async def execute_query(
        self,
        db: str,
        query_name: str,
        params: dict,
        *,
        fetch_mode: str = "page",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        # page/page_size are always sent: the REST schema carries them for every
        # mode and ignores them outside "page", so there is nothing to branch on.
        payload = {
            "query_name": query_name,
            "params": params,
            "fetch_mode": fetch_mode,
            "page": page,
            "page_size": page_size,
        }
        return await self._request("POST", f"/{db}/queries/execute", json=payload)

    async def describe_table(self, db: str, table: str) -> dict:
        return await self._request("GET", f"/{db}/schemas/describe/{table}")
