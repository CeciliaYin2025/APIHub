"""Async HTTP client for the Oracle API Hub REST API.

The MCP server never talks to databases or the service layer directly —
every tool call is proxied through the REST API so RBAC, audit logging, and
rate limiting apply exactly as they do for any other API consumer.
"""

import re
from collections.abc import Callable
from urllib.parse import quote

import httpx

from mcp_server.config import mcp_settings


class ApiHubError(Exception):
    """A REST call failed; the message is safe to surface to the MCP client."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


# Path segments below are built from LLM-supplied tool arguments. Without these
# guards a table name of "../../../auth/users" collapses (httpx normalises "..")
# into a request against a completely different REST endpoint, carrying the
# caller's credential with it. Validate first, then percent-encode — encoding
# alone would still let "%2E%2E" style input through on a lenient server, and
# validation alone leaves "#" to be swallowed as a URL fragment.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_$#]{1,128}(?:\.[A-Za-z0-9_$#]{1,128})?$")


def _segment(value: str, pattern: re.Pattern, kind: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ApiHubError(400, f"Invalid {kind}: {value!r}")
    return quote(value, safe="")


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
        timeout: float | None = None,
    ):
        self._headers_provider = headers_provider or (lambda: {})
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api/v1",
            timeout=mcp_settings.request_timeout_seconds if timeout is None else timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ping(self, timeout: float = 3.0) -> None:
        """Raise ApiHubError if the REST API is not answering.

        Deliberately short-timeout and unauthenticated: the Hub's ``/health``
        needs no credential, and a health probe must not inherit the (much
        longer) request timeout — a hung upstream would hang the probe with it.
        """
        await self._request("GET", "/health", timeout=timeout)

    async def _request(self, method: str, url: str, **kwargs) -> dict | list:
        try:
            resp = await self._http.request(method, url, headers=self._headers_provider(), **kwargs)
        except httpx.HTTPError as e:
            raise ApiHubError(0, f"API Hub unreachable: {e}") from e
        if resp.status_code >= 500:
            # 5xx bodies carry driver text and object names (ORA-00942 and the
            # like). A 4xx detail is actionable feedback the model can correct
            # from; a 5xx one is an internal detail it can do nothing with.
            raise ApiHubError(resp.status_code, "API Hub returned a server error")
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
        return await self._request(
            "GET",
            f"/{_segment(db, _ALIAS_RE, 'database alias')}/queries/definitions",
            params={"permitted_only": "true"},
        )

    async def field_dictionary(self) -> dict:
        """The E&O db_field x MC label x Sales label mapping (metadata only).

        Backs the ``eo://data-dictionary`` resource. Fetched per read rather
        than cached: the ``caller_view`` it reports is per-principal, and one
        MCP process serves every caller on the HTTP transport.
        """
        return await self._request("GET", "/eo/field-dictionary")

    async def execute_query(
        self,
        db: str,
        query_name: str,
        params: dict,
        *,
        fetch_mode: str = "page",
        page: int = 1,
        page_size: int = 50,
        view: str | None = None,
        columns: list[str] | None = None,
        order_by: list[str] | None = None,
    ) -> dict:
        # page/page_size are always sent: the REST schema carries them for every
        # mode and ignores them outside "page", so there is nothing to branch on.
        db_seg = _segment(db, _ALIAS_RE, "database alias")
        payload = {
            "query_name": query_name,
            "params": params,
            "fetch_mode": fetch_mode,
            "page": page,
            "page_size": page_size,
        }
        # Omitted unless asked for: the REST schema rejects anything outside
        # mc/sales, and sending null on every call would put the key in front of
        # definitions that have no view at all.
        if view is not None:
            payload["view"] = view
        # Same reasoning as `view`: omitted unless asked for, so the default path
        # stays byte-identical to what every existing caller sends.
        if columns:
            payload["columns"] = columns
        if order_by:
            payload["order_by"] = order_by
        return await self._request("POST", f"/{db_seg}/queries/execute", json=payload)

    async def describe_table(self, db: str, table: str) -> dict:
        db_seg = _segment(db, _ALIAS_RE, "database alias")
        table_seg = _segment(table, _TABLE_RE, "table name")
        return await self._request("GET", f"/{db_seg}/schemas/describe/{table_seg}")
