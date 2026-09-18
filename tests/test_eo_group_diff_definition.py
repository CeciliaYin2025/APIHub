"""Pin the two definitions in ``scripts/apply_eo_group_diff.py``.

The definitions live in the platform database, so nothing in CI executes them.
These tests check the rules that fail *silently* in production instead: a CTE or
an inline ORDER BY that only breaks once the Hub wraps the SQL in a subquery, a
bind that no parameter declares, an ordering column that is not in the result, a
tool name too long to stay readable to a model.

Two are specific to this change:

- Neither SQL may mention ``TWD_CURRENCY_RATE``. Every amount column in these
  sources is already TWD (measured 2026-09-18); multiplying by the rate inflates a
  CNY site 4.66x. The old data dictionary said the opposite, so this is exactly
  the "fix" a future reader is likely to attempt.
- ``eo_item_diff`` keeps all twenty of its original output columns. The nine new
  ones are additive; dropping or renaming an old one would break every caller
  that names columns explicitly.
"""

import re

import pytest

from scripts.apply_eo_group_diff import (
    DB_ALIAS,
    GROUP_DIFF_NAME,
    GROUP_DIFF_ORDER_BY,
    GROUP_DIFF_PARAMS,
    GROUP_DIFF_RESULT_DESCRIPTION,
    GROUP_DIFF_SQL,
    ITEM_DIFF_NAME,
    ITEM_DIFF_RESULT_DESCRIPTION,
    ITEM_DIFF_SQL,
)

# app/services/query_service.py::_ORDER_COLUMN_RE
ORDER_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\Z")

BOTH_SQL = pytest.mark.parametrize(
    "sql", [GROUP_DIFF_SQL, ITEM_DIFF_SQL], ids=[GROUP_DIFF_NAME, ITEM_DIFF_NAME]
)

# The parameters eo_item_diff was deployed with. Widening its SELECT must not
# change its signature — a new bind would make every existing call a 400.
ITEM_DIFF_BINDS = {"bu", "ou", "pid", "status", "customer", "frozen_date", "compare_date"}

# eo_item_diff's twenty output columns before this change.
ITEM_DIFF_ORIGINAL_COLUMNS = [
    "Site",
    "Item_No",
    "Item_Description",
    "BU_NO",
    "BU_NO_N",
    "PID",
    "PID_N",
    "CUSTOMERGROUPNAME",
    "CUSTOMERGROUP_N",
    "CUR_FROZEN_DATE",
    "CMP_FROZEN_DATE",
    "STATUS",
    "CUR_EO_OHOPO_AMT_TWD",
    "CMP_EO_OHOPO_AMT_TWD",
    "DELTA_EO_OHOPO_AMT_TWD",
    "ABS_DELTA_EO_OHOPO_AMT_TWD",
    "CUR_EO_OH_AMT_TWD",
    "CMP_EO_OH_AMT_TWD",
    "CUR_EO_OPO_AMT_TWD",
    "CMP_EO_OPO_AMT_TWD",
]


def _binds(sql: str) -> set[str]:
    return set(re.findall(r":([a-z_][a-z0-9_]*)", sql))


# ── rules that break execution ──────────────────────────────────────────────


@BOTH_SQL
def test_no_trailing_semicolon(sql):
    # The Hub wraps every definition as "SELECT ... FROM (<sql>) sub_".
    assert not sql.rstrip().endswith(";")


@BOTH_SQL
def test_no_inline_order_by(sql):
    # Illegal in a derived table on MSSQL unless it accompanies TOP, which
    # neither of these uses. Ordering belongs in the definition's order_by.
    assert not re.search(r"\bORDER\s+BY\b", sql, re.IGNORECASE)


@BOTH_SQL
def test_no_cte(sql):
    # WITH ... SELECT is illegal inside the subquery wrapper; use nested derived
    # tables (both of these stack three levels).
    assert not re.match(r"\s*WITH\b", sql, re.IGNORECASE)


def test_group_diff_binds_match_its_params():
    assert _binds(GROUP_DIFF_SQL) == set(GROUP_DIFF_PARAMS)


def test_item_diff_signature_is_unchanged():
    assert _binds(ITEM_DIFF_SQL) == ITEM_DIFF_BINDS


# ── naming and ordering ─────────────────────────────────────────────────────


def test_tool_name_stays_readable():
    # mcp_server/naming.py truncates query_<alias>_<name> at 64 characters.
    assert len(DB_ALIAS) + len(GROUP_DIFF_NAME) <= 57
    assert f"query_{DB_ALIAS}_{GROUP_DIFF_NAME}".lower() == "query_dw_eo_group_diff"


def test_order_by_columns_are_valid_identifiers_and_in_the_result():
    assert GROUP_DIFF_ORDER_BY, "without a deterministic sort, pagination is unstable"
    for item in GROUP_DIFF_ORDER_BY:
        column = item["column"]
        assert ORDER_COLUMN_RE.match(column), column
        assert item["direction"] in ("ASC", "DESC")
        # The outermost projection selects x.<COL>, so the wrapper can sort on it.
        assert f"x.{column}" in GROUP_DIFF_SQL, f"{column} is not an output column"


# ── the currency correction ─────────────────────────────────────────────────


@BOTH_SQL
def test_amounts_are_never_multiplied_by_the_rate(sql):
    # Measured 2026-09-18: every amount column in these sources is already TWD.
    # Applying TWD_CURRENCY_RATE inflates a CNY site 4.66x. See the Currency
    # section of the eo-data-dictionary skill.
    assert "TWD_CURRENCY_RATE" not in sql


def test_both_result_descriptions_say_the_amounts_are_already_twd():
    for text in (GROUP_DIFF_RESULT_DESCRIPTION, ITEM_DIFF_RESULT_DESCRIPTION):
        assert "already TWD" in text
        assert "TWD_CURRENCY_RATE" in text, "the prohibition has to be stated where the model reads it"


# ── backward compatibility and coverage of the new columns ──────────────────


@pytest.mark.parametrize("column", ITEM_DIFF_ORIGINAL_COLUMNS)
def test_item_diff_keeps_its_original_columns(column):
    assert f"k.{column}" in ITEM_DIFF_SQL or f"AS {column}" in ITEM_DIFF_SQL


@pytest.mark.parametrize("prefix", ["CUR_", "CMP_", "DELTA_"])
@pytest.mark.parametrize("measure", ["ALL_OH_AMT_TWD", "OPO_AMT_TWD", "ALLOCATED_AMT_TWD"])
def test_item_diff_carries_the_physical_measures(prefix, measure):
    assert f"{prefix}{measure}" in ITEM_DIFF_SQL


@pytest.mark.parametrize("prefix", ["CUR_", "CMP_", "DELTA_"])
@pytest.mark.parametrize("measure", ["EO_AMT_TWD", "OH_AMT_TWD", "OPO_AMT_TWD", "ALLOCATED_AMT_TWD"])
def test_group_diff_carries_every_measure_on_both_sides(prefix, measure):
    assert f"{prefix}{measure}" in GROUP_DIFF_SQL


def test_group_diff_supports_every_documented_group_level():
    for level in ("BU", "CUSTOMER", "PROJECT", "OU", "TOTAL"):
        assert f"'{level}'" in GROUP_DIFF_SQL
    # PID is the ELSE branch rather than a literal test, so assert the column.
    assert "ELSE ep.PID END AS GROUP_KEY" in GROUP_DIFF_SQL
    assert "'PID'" in GROUP_DIFF_PARAMS["group_by"]["description"]


def test_group_diff_description_points_away_from_the_manual_subtraction():
    # The whole point: stop pulling two eo_summary pages and subtracting them.
    from scripts.apply_eo_group_diff import GROUP_DIFF_DESCRIPTION

    assert "eo_summary" in GROUP_DIFF_DESCRIPTION
    assert "page 1" in GROUP_DIFF_DESCRIPTION
    assert "order_by" in GROUP_DIFF_RESULT_DESCRIPTION
