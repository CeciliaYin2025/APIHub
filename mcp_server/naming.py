"""Derivation of MCP tool names from (db_alias, SQL definition name).

Pure string logic with no third-party imports, split out of ``catalog.py`` so the
FastAPI app can compute the same names for its read-only MCP tools view without
pulling in the ``mcp`` SDK or the HTTP client. ``catalog.py`` is the only
producer of real tool names; anything else that derives a name must import from
here rather than re-implementing the rules, or the view would drift from what
the MCP server actually serves.
"""

import re

# Anthropic/MCP tool names must match ^[a-zA-Z0-9_-]{1,64}$ — hence both the
# character-class scrub below and the hard length cap.
TOOL_NAME_MAX_LEN = 64

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize(text: str) -> str:
    return _NAME_RE.sub("_", text).strip("_").lower()


def untruncated_tool_name(db_alias: str, definition_name: str) -> str:
    """The tool name before the length cap — lets callers detect truncation."""
    return f"query_{sanitize(db_alias)}_{sanitize(definition_name)}"


def tool_name(db_alias: str, definition_name: str) -> str:
    return untruncated_tool_name(db_alias, definition_name)[:TOOL_NAME_MAX_LEN]
