"""Caller-specified ordering for query results.

A definition carries its own ``order_by`` so that pagination is stable — without
one, page 2 is not guaranteed to exclude what page 1 showed. That default answers
"give me the rows"; it does not answer "give me the largest five", which is the
question an analyst actually asks.

Sorting cannot be done after the fact. Rows are paginated in SQL, so ordering
applied to the returned page would rank 100 arbitrary rows, not the 140,803 the
period holds. Hence the caller's ordering goes into the same wrapper the
definition's does (``dialect.wrap_paginated`` and friends), which every engine
already implements.

Two input shapes, because the callers differ:

- ``[{"column": "AMT", "direction": "DESC"}]`` — the REST shape, matching how a
  definition stores its own ordering.
- ``["-AMT", "Item_No"]`` — a leading ``-`` for descending. This is what the tool
  and MCP surfaces advertise: a model writes a flat string far more reliably than
  a nested object, and it costs fewer tokens.

Column names are validated against the same identifier whitelist the definition's
own ordering uses. The name still has to exist in the result — that is checked by
the database, since the Hub cannot know a definition's output columns without
running it — so an unknown column surfaces as a 400 from the router rather than
being silently dropped.
"""

import re
from typing import Any

# Same whitelist as app/schemas/sql_definition.py::OrderBySchema and
# query_service._ORDER_COLUMN_RE. Identifiers only: no dots, quotes, spaces or
# parentheses ever reach the SQL string.
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_DIRECTIONS = ("ASC", "DESC")


def _clean(column: str, direction: str) -> dict[str, str]:
    column = column.strip()
    direction = (direction or "ASC").strip().upper()
    if not _COLUMN_RE.match(column):
        raise ValueError(
            f"Invalid order_by column: {column!r}. Use a bare column name from the result "
            "(letters, digits and underscore only)."
        )
    if direction not in _DIRECTIONS:
        raise ValueError(f"Invalid order_by direction: {direction!r}. Use ASC or DESC.")
    return {"column": column, "direction": direction}


def parse_order_by(value: Any) -> list[dict[str, str]]:
    """Normalise a caller's ordering into ``[{"column", "direction"}]``.

    Accepts the object form, the ``"-COL"`` string form, and ``"COL DESC"``.
    Returns ``[]`` for ``None``/empty, which means "keep the definition's own
    ordering" — the parameter is opt-in and omitting it changes nothing.
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    if not isinstance(value, list):
        raise ValueError("order_by must be a list of column names or {column, direction} objects")

    items: list[dict[str, str]] = []
    for entry in value:
        if isinstance(entry, str):
            text = entry.strip()
            if not text:
                continue
            direction = "ASC"
            if text.startswith("-"):
                direction, text = "DESC", text[1:]
            elif text.startswith("+"):
                text = text[1:]
            else:
                parts = text.split()
                # "AMT DESC" — the form a model writes when it is thinking in SQL.
                if len(parts) == 2:
                    text, direction = parts[0], parts[1]
            items.append(_clean(text, direction))
        elif isinstance(entry, dict):
            column = entry.get("column")
            if not isinstance(column, str):
                raise ValueError(f"Invalid order_by entry: {entry!r}; 'column' must be a string")
            items.append(_clean(column, str(entry.get("direction") or "ASC")))
        else:
            raise ValueError(f"Invalid order_by entry: {entry!r}")
    return items


# Driver messages for "that column is not in the result". Each engine words it
# differently and none of them is a client error by default — without this the
# caller gets a 500 for what is simply a typo.
_UNKNOWN_COLUMN_MARKERS = (
    "invalid column name",  # MSSQL
    "invalid identifier",  # Oracle ORA-00904
    "does not exist",  # PostgreSQL: column "x" does not exist
    "unknown column",  # MySQL-ish, for completeness
)


def looks_like_unknown_column(error: Exception) -> bool:
    """Whether a driver error is the database rejecting an ordering column."""
    text = str(error).lower()
    return any(marker in text for marker in _UNKNOWN_COLUMN_MARKERS)
