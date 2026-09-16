"""Unit tests for the optional column projection (app/services/column_selection.py).

The projection exists to keep wide rows out of an LLM's context: eo_detail
carries 89 columns at ~2.6 KB per row as JSON, so a page of 100 rows is ~258 KB
— over half a 131k-token window — while the handful of columns an answer needs
costs a fraction of that.

Two properties matter beyond "it narrows the row": omitting the parameter must
change nothing at all (91k calls a month rely on that), and a masked column must
not be distinguishable from a typo.
"""

import pytest

from app.services.column_selection import select_columns

COLUMNS = ["Site", "Item_No", "OBSOLETE_OHOPO_AMT_TWD", "unrecoverable_oh", "Frozen_Date"]
ROWS = [
    {
        "Site": "SP",
        "Item_No": "549B0010MR0",
        "OBSOLETE_OHOPO_AMT_TWD": 198349363,
        "unrecoverable_oh": 42,
        "Frozen_Date": "2026-09-01",
    }
]


@pytest.mark.parametrize("requested", [None, []])
def test_omitted_passes_through_untouched(requested):
    """The default path for every pre-existing caller: same objects, no copying."""
    columns, rows = select_columns(requested, COLUMNS, ROWS)

    assert columns is COLUMNS
    assert rows is ROWS


def test_keeps_requested_order_not_source_order():
    columns, rows = select_columns(["Item_No", "Site"], COLUMNS, ROWS)

    assert columns == ["Item_No", "Site"]
    assert rows == [{"Item_No": "549B0010MR0", "Site": "SP"}]


def test_matching_is_case_insensitive_but_output_keeps_source_casing():
    # The warehouse mixes casing (OU, Frozen_Date, unrecoverable_oh), so a caller
    # copying a name out of a report should not have to guess.
    columns, rows = select_columns(["frozen_date", "UNRECOVERABLE_OH"], COLUMNS, ROWS)

    assert columns == ["Frozen_Date", "unrecoverable_oh"]
    assert rows == [{"Frozen_Date": "2026-09-01", "unrecoverable_oh": 42}]


def test_blanks_and_duplicates_are_ignored():
    columns, _ = select_columns(["Site", " site ", "", "  ", "Item_No"], COLUMNS, ROWS)

    assert columns == ["Site", "Item_No"]


def test_unknown_column_raises_and_names_it():
    with pytest.raises(ValueError) as excinfo:
        select_columns(["Site", "NOPE"], COLUMNS, ROWS)

    message = str(excinfo.value)
    assert "NOPE" in message
    assert "Site" in message, "the error must list what is available, or the caller cannot self-correct"


def test_error_truncates_a_long_column_list_but_reports_the_count():
    wide = [f"C{i}" for i in range(89)]
    with pytest.raises(ValueError) as excinfo:
        select_columns(["missing"], wide, [])

    message = str(excinfo.value)
    assert "89 columns in total" in message
    assert message.count(",") < 89, "dumping all 89 names is unreadable and, on a tool call, expensive"


def test_a_column_the_view_masked_reads_as_unknown():
    """Masking runs first, so the masked column is simply not there.

    That is the point: the caller cannot tell "hidden from you" from "misspelled",
    so this parameter cannot be used to probe the Sales view's boundary.
    """
    sales_columns = ["Site", "OBSOLETE_OHOPO_AMT_TWD"]  # unrecoverable_oh already dropped
    with pytest.raises(ValueError) as excinfo:
        select_columns(["unrecoverable_oh"], sales_columns, ROWS)

    assert "unrecoverable_oh" in str(excinfo.value)


def test_missing_key_in_a_row_becomes_none_rather_than_raising():
    # The detail source is not uniform; a row that lacks a key must not fail the
    # whole request.
    rows = [{"Site": "SP"}, {"Site": "SN", "Item_No": "X"}]
    _, projected = select_columns(["Site", "Item_No"], ["Site", "Item_No"], rows)

    assert projected == [{"Site": "SP", "Item_No": None}, {"Site": "SN", "Item_No": "X"}]
