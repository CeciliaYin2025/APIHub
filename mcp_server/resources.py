"""MCP resources — currently one: the E&O field dictionary.

A resource rather than a tool because it is reference material, not an action.
A model should be able to pull it once at the start of a conversation to learn
that "E&O OH (All) Amt" and "Obsolete All OH Amt" are the same column, without
spending a tool call and without the operator having to describe the mapping in
a prompt.

Like every other part of this process it goes through the REST API
(``GET /eo/field-dictionary``) — the MCP server never imports ``app/``.

Not cached. The payload carries ``caller_view``, which is per-principal, and one
MCP process serves every caller on the HTTP transport; a cached copy would hand
one caller's view to the next.
"""

import json

from mcp.types import Resource
from pydantic import AnyUrl

from mcp_server.client import ApiHubClient, ApiHubError

EO_DICTIONARY_URI = "eo://data-dictionary"

# What the model most needs and is least likely to infer from a column list.
_GUIDANCE = (
    "Sercomm E&O columns carry two different display names for the same database column. "
    "The MC report ('MC E&O Summary by Site') uses the internal term 'Obsolete'; the Excel "
    "sent to Sales renames it 'E&O' and uses plainer names elsewhere "
    "(MC 'FG on the way Amt' = Sales 'FG In Transit Amt'; MC 'FG Hub Stock Amt' = Sales "
    "'Hub OH Amt'). When a user quotes a column name, match it against BOTH mc_label and "
    "sales_label to find db_field, and answer using db_field. Fields with mc_only=true do "
    "not exist in the Sales view at all — if a user is looking at the Sales workbook they "
    "cannot see them, and a caller restricted to that view is not served them."
)


def resource_list() -> list[Resource]:
    return [
        Resource(
            uri=AnyUrl(EO_DICTIONARY_URI),
            name="eo-data-dictionary",
            title="E&O field dictionary (MC vs Sales)",
            description=(
                "Maps every E&O Summary column to its MC report name and its Sales workbook "
                "name, and says which of the two views may see it. Read this before "
                "interpreting a column name a user quoted."
            ),
            mimeType="application/json",
        )
    ]


def _normalise(uri: str) -> str:
    """A trailing slash is the same resource; AnyUrl keeps whatever was sent."""
    return uri.rstrip("/")


def is_known(uri: str) -> bool:
    return _normalise(uri) == _normalise(EO_DICTIONARY_URI)


async def read_resource(client: ApiHubClient, uri: str) -> str:
    """Fetch a resource's content as a JSON string."""
    if not is_known(uri):
        raise ApiHubError(404, f"Unknown resource: {uri}")

    payload = await client.field_dictionary()
    # Rendered rather than passed through so the guidance travels with the data;
    # the model reads one blob, not a blob plus a prompt it was never given.
    return json.dumps({"guidance": _GUIDANCE, **payload}, ensure_ascii=False, indent=2)
