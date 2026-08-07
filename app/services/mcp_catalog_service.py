"""Read-only projection of what the MCP server exposes as tools.

The MCP server derives one tool per (db_alias, SQL definition) and drops any
whose derived name collides with one already taken — silently, in its own
process, where no operator can see it. This rebuilds the same mapping from the
Hub's own registries so the Admin UI can show which definitions actually reach
an LLM and which disappear.

Faithfulness to the real catalog rests on two things: the names come from
``mcp_server.naming`` (the same module ``catalog.py`` uses), and the iteration
order matches ``ToolCatalog._load`` — aliases in ``GET /databases`` order, then
definitions in ``GET /{db}/queries/definitions`` order. Order decides which
member of a colliding pair wins, so it is part of the answer, not an
incidental detail.
"""

from app.db.multi_pool import db_registry
from app.services.sql_registry import sql_registry
from mcp_server.naming import TOOL_NAME_MAX_LEN, untruncated_tool_name


def build_tool_view() -> dict:
    """Return every (alias, definition) pair with its derived MCP tool name."""
    aliases = [row["alias"] for row in db_registry.list_databases()]

    tools: list[dict] = []
    winners: dict[str, dict] = {}

    for alias in aliases:
        for definition in sql_registry.list_all(db_alias=alias):
            untruncated = untruncated_tool_name(alias, definition.name)
            name = untruncated[:TOOL_NAME_MAX_LEN]
            row = {
                "tool_name": name,
                "db_alias": alias,
                "definition": definition.name,
                "description": definition.description or "",
                "params": sorted(definition.params.keys()),
                "truncated": len(untruncated) > TOOL_NAME_MAX_LEN,
                "exposed": True,
                "shadowed_by": None,
                "shadows": [],
            }
            winner = winners.get(name)
            if winner is None:
                winners[name] = row
            else:
                row["exposed"] = False
                row["shadowed_by"] = {"db_alias": winner["db_alias"], "definition": winner["definition"]}
                # Flag the surviving row too, so a collision is visible from
                # either end of the pair rather than only from the loser's.
                winner["shadows"].append({"db_alias": alias, "definition": definition.name})
            tools.append(row)

    return {
        "tool_name_max_len": TOOL_NAME_MAX_LEN,
        "db_aliases": aliases,
        "summary": {
            "definitions": len(tools),
            "exposed": sum(1 for t in tools if t["exposed"]),
            "shadowed": sum(1 for t in tools if not t["exposed"]),
            "truncated": sum(1 for t in tools if t["truncated"]),
        },
        "tools": tools,
    }
