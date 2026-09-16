"""Unit tests for caller-specified ordering (app/services/order_selection.py).

Ordering has to reach the SQL wrapper, so the column name is interpolated into
the statement: the identifier whitelist here is the only thing between a caller
and an injected clause. The rest of these tests cover the two input shapes —
objects for REST, "-COL" strings for tools and MCP, because a model writes a flat
string far more reliably than a nested object.
"""

import pytest

from app.services.order_selection import looks_like_unknown_column, parse_order_by


@pytest.mark.parametrize("value", [None, "", []])
def test_empty_means_keep_the_definitions_own_order(value):
    assert parse_order_by(value) == []


def test_object_form():
    assert parse_order_by([{"column": "AMT", "direction": "desc"}]) == [{"column": "AMT", "direction": "DESC"}]


def test_object_form_defaults_to_ascending():
    assert parse_order_by([{"column": "Item_No"}]) == [{"column": "Item_No", "direction": "ASC"}]


def test_string_shorthand_with_leading_minus():
    assert parse_order_by(["-AMT", "Item_No"]) == [
        {"column": "AMT", "direction": "DESC"},
        {"column": "Item_No", "direction": "ASC"},
    ]


def test_string_shorthand_accepts_sql_style_and_plus():
    assert parse_order_by(["AMT DESC", "+Site"]) == [
        {"column": "AMT", "direction": "DESC"},
        {"column": "Site", "direction": "ASC"},
    ]


def test_comma_separated_string_is_accepted():
    # Models send "a, b" for an array often enough to be worth handling.
    assert parse_order_by("-AMT, Item_No") == [
        {"column": "AMT", "direction": "DESC"},
        {"column": "Item_No", "direction": "ASC"},
    ]


@pytest.mark.parametrize(
    "value",
    [
        ["AMT; DROP TABLE x"],
        ["AMT DESC, Item_No"],  # two keys in one entry would smuggle a comma into the clause
        ["sub_.AMT"],
        ['AMT"'],
        ["(SELECT 1)"],
        [{"column": "AMT", "direction": "DESC; --"}],
        [{"column": 42}],
        [42],
    ],
)
def test_anything_that_is_not_a_bare_identifier_is_rejected(value):
    with pytest.raises(ValueError):
        parse_order_by(value)


def test_error_tells_the_caller_what_shape_is_expected():
    with pytest.raises(ValueError) as excinfo:
        parse_order_by(["not a column"])

    assert "bare column name" in str(excinfo.value)


@pytest.mark.parametrize(
    "message",
    [
        "Invalid column name 'NOPE'.",  # MSSQL
        'ORA-00904: "NOPE": invalid identifier',  # Oracle
        'column "nope" does not exist',  # PostgreSQL
    ],
)
def test_driver_errors_for_a_missing_column_are_recognised(message):
    assert looks_like_unknown_column(Exception(message))


def test_an_unrelated_driver_error_is_not_mistaken_for_a_bad_column():
    assert not looks_like_unknown_column(Exception("Timeout expired before the operation completed"))
