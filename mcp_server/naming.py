"""Derivation of MCP tool names from (db_alias, SQL definition name).

Pure string logic with no third-party imports, split out of ``catalog.py`` so the
FastAPI app can compute the same names for its read-only MCP tools view without
pulling in the ``mcp`` SDK or the HTTP client. ``catalog.py`` is the only
producer of real tool names; anything else that derives a name must import from
here rather than re-implementing the rules, or the view would drift from what
the MCP server actually serves.
"""

import re

# MCP tool names should be 1-128 characters. The scrub below keeps the name
# inside [a-zA-Z0-9_-]; the spec also permits ".", but folding it to "_" is what
# every existing tool name was built with, and switching now would rename them
# under clients that have them configured.
#
# The cap used to be 64, inherited from a superseded Anthropic tool-name limit.
# That was the main manufacturer of collisions: two definitions differing only
# in a suffix past character 64 truncated to the same name, and catalog.py then
# silently dropped the loser. 128 is what the current spec allows.
TOOL_NAME_MAX_LEN = 128

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize(text: str) -> str:
    return _NAME_RE.sub("_", text).strip("_").lower()


def untruncated_tool_name(db_alias: str, definition_name: str) -> str:
    """The tool name before the length cap — lets callers detect truncation."""
    return f"query_{sanitize(db_alias)}_{sanitize(definition_name)}"


def tool_name(db_alias: str, definition_name: str) -> str:
    return untruncated_tool_name(db_alias, definition_name)[:TOOL_NAME_MAX_LEN]
