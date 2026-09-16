"""Optional column projection for query results.

A definition is written once as ``SELECT *`` (or ``SELECT ep.*``) and serves
every caller, so a row carries every column the source has — 89 of them on
``eo_detail``, ~2.6 KB per row as JSON because each row repeats the column
names. A caller that needs five of those columns pays for all 89: 100 rows is
~258 KB, which is more than half of a 131k-token context window.

``columns`` lets the caller ask for the subset it will actually read. It is
**opt-in**: omitted (or empty), the result passes through untouched, byte for
byte, which is what every existing caller gets.

Projection happens after the audience view has been applied
(``app/services/eo_field_views.py``), never before. A column the view masked is
already gone from ``columns`` by then, so asking for it lands in the same
"unknown column" error as a typo — the caller cannot use this parameter to
discover, let alone read, what the view hid from them.
"""

from typing import Any

# Long column lists are the norm here; an error that dumps 89 names is unreadable
# and, on a tool call, expensive. Name enough to spot a typo and say how many more.
_MAX_LISTED = 40


def select_columns(
    requested: list[str] | None,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return *columns*/*rows* narrowed to *requested*.

    Matching is case-insensitive because the warehouse's own casing is
    inconsistent (``OU``, ``Frozen_Date``, ``unrecoverable_oh`` all coexist), and
    a caller copying a name out of a report should not have to guess. The output
    keeps the caller's order, so the response is predictable rather than
    dependent on the source table's column order.

    Raises ``ValueError`` naming the unknown columns; the router turns that into
    a 400. Duplicates and blanks in *requested* are ignored rather than rejected:
    they are harmless and rejecting them only breaks generated callers.
    """
    if not requested:
        return columns, rows

    available = {c.casefold(): c for c in columns}
    kept: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()

    for name in requested:
        key = str(name).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        actual = available.get(key)
        if actual is None:
            unknown.append(str(name).strip())
        else:
            kept.append(actual)

    if unknown:
        listed = ", ".join(columns[:_MAX_LISTED])
        if len(columns) > _MAX_LISTED:
            listed += f", ... ({len(columns)} columns in total)"
        raise ValueError(f"Unknown column(s): {', '.join(unknown)}. Available: {listed}")

    return kept, [{c: row.get(c) for c in kept} for row in rows]
