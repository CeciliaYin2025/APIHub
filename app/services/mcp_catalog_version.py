"""A cheap fingerprint of everything that shapes the MCP tool catalog.

The MCP server used to have one way to find out whether its cached tool list
was still current: refetch every definition from every alias. That made a short
``MCP_CATALOG_TTL_SECONDS`` expensive, so the TTL stayed long and the catalog
stayed stale. This lets it ask one small question instead, and only pay for the
full refetch when the answer moved.

What goes into the hash is the whole point:

- **Included**: the alias list, and per definition its name, group, description,
  result_description, permission and parameter declarations — everything
  ``mcp_server/catalog.py`` reads to build a ``Tool``.
- **Excluded**: the SQL. Editing a definition's body changes what the query
  returns but not the tool, and tool calls are proxied live, so an SQL edit is
  already visible without any catalog refresh. Hashing it would force a
  pointless rebuild on every SQL tweak.

Computed from the in-memory registry rather than the database, so it describes
exactly what ``GET /{db}/queries/definitions`` would serve at this moment.

**Known gap:** the fingerprint is global, not per-caller. Editing a *role* — as
opposed to a definition — changes which tools a user is permitted to see
(``permitted_only``) without moving the hash, so that user keeps their cached
list until the TTL lapses or they reconnect. Bumping on role edits would mean
hashing the whole RBAC graph on every poll, for a change that is far rarer than
a definition edit.
"""

import hashlib
import json

from app.db.multi_pool import db_registry
from app.services.sql_registry import sql_registry


def catalog_version() -> str:
    """A hex digest that changes whenever the derived tool catalog would."""
    aliases = [row["alias"] for row in db_registry.list_databases()]

    definitions = []
    for definition in sorted(sql_registry.list_all(), key=lambda d: (d.db_alias, d.name)):
        definitions.append(
            [
                definition.db_alias,
                definition.name,
                definition.group_name,
                definition.description,
                definition.result_description,
                definition.permission,
                # sorted so a dict reordering in the registry is not a change
                sorted([p.name, p.type, p.required, p.description] for p in definition.params.values()),
            ]
        )

    payload = json.dumps([aliases, definitions], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
