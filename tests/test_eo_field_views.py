"""The canonical E&O field dictionary and the MC / Sales view split."""

import pytest

from app.models.user import Permission, Role, User
from app.services import eo_field_views as v


# ── dictionary integrity ─────────────────────────────────────────────────────


def test_no_duplicate_db_fields():
    names = [s.db_field.upper() for s in v.FIELD_DICTIONARY]
    assert len(names) == len(set(names)), "a db_field appears twice"


def test_every_field_belongs_to_at_least_one_view():
    assert all(s.views for s in v.FIELD_DICTIONARY)


def test_sales_fields_always_carry_a_sales_label():
    for spec in v.FIELD_DICTIONARY:
        if v.SALES in spec.views:
            assert spec.sales_label, f"{spec.db_field} is in the sales view with no sales label"


def test_mc_only_fields_have_no_sales_label():
    for spec in v.FIELD_DICTIONARY:
        if spec.mc_only:
            assert spec.sales_label is None, f"{spec.db_field} is MC-only but has a sales label"


def test_mc_sees_everything_sales_sees():
    assert v.columns_for_view(v.SALES) < v.columns_for_view(v.MC)


def test_the_loss_evaluation_block_is_mc_only():
    """Section 2's whole point — Sales must never see the loss figures."""
    sales = v.columns_for_view(v.SALES)
    for field in (
        "OPO_unrecoverable",
        "OH_unrecoverable",
        "Obsolete_OPO_EST_Loss_Amt",
        "Obsolete_All_OH_EST_Loss_Amt",
        "SALES_OBSOLETE_OPO_AMT",
        "SALES_OBSOLETE_OH_ALL_AMT",
        "Cust_Liable_Amt",
        "TARGET_AMT",
        "UNRECOVERABLE_AMT",
        "BUYDOWN_AMT",
        "RESERVED_AMT",
    ):
        assert field.upper() not in sales, f"{field} leaked into the sales view"


def test_excess_and_stock_structure_are_mc_only():
    sales = v.columns_for_view(v.SALES)
    for field in ("EXCESS_OHOPO_AMT_52W_TWD", "TP80_Amt", "ALL_OH_AMT", "UNAVAILABLE_OH_AMT", "LOCK_QTY", "MSIC"):
        assert field.upper() not in sales


# ── label resolution — the Obsolete/E&O rename ───────────────────────────────


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Obsolete All OH Amt", "OBSOLETE_OH_ALL_AMT_TWD"),
        ("E&O OH (All) Amt", "OBSOLETE_OH_ALL_AMT_TWD"),
        ("Obsolete OPO Amt", "OBSOLETE_OPO_AMT_TWD"),
        ("E&O OPO Amt", "OBSOLETE_OPO_AMT_TWD"),
        ("FG on the way Amt", "FG_Intransit_Amt_TWD"),
        ("FG In Transit Amt", "FG_Intransit_Amt_TWD"),
        ("FG Hub Stock Amt", "FG_Hub_Amt_TWD"),
        ("Hub OH Amt", "FG_Hub_Amt_TWD"),
        ("Non-TP70 & Non-TP75 OH Amt", "FACTORY_STOCK_AMT_TWD"),
        ("Due Date for Sales' Action", "ACTION_PLAN_DUE_DATE"),
        ("Sales AP Due Date", "ACTION_PLAN_DUE_DATE"),
    ],
)
def test_both_naming_systems_resolve_to_the_same_column(label, expected):
    assert [s.db_field for s in v.resolve_label(label)] == [expected]


def test_mc_and_sales_labels_agree_for_the_available_oh_column():
    """MC names it by what it is, Sales by what it excludes — same column."""
    mc = v.resolve_label("Available OH Amt (exclude TP75 & TP80 / TP70 & TP82)")
    sales = v.resolve_label("Non-TP70 & Non-TP75 OH Amt")
    assert mc == sales


def test_label_lookup_is_case_insensitive():
    assert v.resolve_label("e&o oh (all) amt")[0].db_field == "OBSOLETE_OH_ALL_AMT_TWD"


def test_db_field_also_resolves():
    assert v.resolve_label("obsolete_oh_all_amt_twd")[0].db_field == "OBSOLETE_OH_ALL_AMT_TWD"


def test_reused_label_returns_both_grains_rather_than_guessing():
    """'E&O OH + OPO Amt' is a PID-level column AND an item-level one."""
    fields = {s.db_field for s in v.resolve_label("E&O OH + OPO Amt")}
    assert fields == {"OBSOLETE_OHOPO_ALL_AMT_TWD", "ITEM_OBSOLETE_OHOPO_AMT"}


def test_unknown_label_returns_empty():
    assert v.resolve_label("Total Widgets") == []


# ── projection ───────────────────────────────────────────────────────────────

_COLUMNS = [
    "OU",
    "BU_NO",
    "PID",
    "OBSOLETE_OH_ALL_AMT_TWD",
    "OH_unrecoverable",
    "EXCESS_OHOPO_AMT_52W_TWD",
    "SUMMARY_ID",
]
_ROWS = [{c: i for i, c in enumerate(_COLUMNS)}]


def test_mc_view_passes_everything_through():
    cols, rows, masked = v.apply_view("eo_summary", list(_COLUMNS), list(_ROWS), "mc")
    assert cols == _COLUMNS
    assert masked == []


def test_sales_view_drops_mc_only_columns():
    cols, rows, masked = v.apply_view("eo_summary", list(_COLUMNS), list(_ROWS), "sales")
    assert cols == ["OU", "BU_NO", "PID", "OBSOLETE_OH_ALL_AMT_TWD"]
    assert set(masked) == {"OH_unrecoverable", "EXCESS_OHOPO_AMT_52W_TWD", "SUMMARY_ID"}
    assert set(rows[0]) == set(cols)


def test_sales_view_is_an_allowlist_not_a_denylist():
    """A column nobody has mapped must NOT reach Sales by default."""
    cols, _, masked = v.apply_view("eo_summary", ["OU", "SOME_NEW_COLUMN_ADDED_LATER"], [{}], "sales")
    assert cols == ["OU"]
    assert masked == ["SOME_NEW_COLUMN_ADDED_LATER"]


def test_projection_is_case_insensitive():
    """SELECT ep.* returns the table's own mixed casing."""
    cols, _, masked = v.apply_view("eo_summary", ["ou", "Obsolete_OH_All_Amt_TWD", "oh_unrecoverable"], [{}], "sales")
    assert cols == ["ou", "Obsolete_OH_All_Amt_TWD"]
    assert masked == ["oh_unrecoverable"]


def test_ungoverned_definition_is_never_masked():
    cols, rows, masked = v.apply_view("eo_detail", list(_COLUMNS), list(_ROWS), "sales")
    assert cols == _COLUMNS
    assert masked == []


def test_rows_keep_their_values():
    _, rows, _ = v.apply_view(
        "eo_summary", ["OU", "OH_unrecoverable"], [{"OU": "SP", "OH_unrecoverable": 999}], "sales"
    )
    assert rows == [{"OU": "SP"}]


def test_unknown_view_is_rejected():
    with pytest.raises(ValueError, match="Unknown view"):
        v.normalise_view("finance")


def test_none_means_default_view():
    assert v.normalise_view(None) == v.MC


# ── who gets which view ──────────────────────────────────────────────────────


def _user(*perms, superuser=False):
    role = Role(name="r", permissions=[Permission(resource=r, action=a) for r, a in perms])
    return User(username="u", is_active=True, is_superuser=superuser, roles=[role])


def test_caller_without_either_permission_keeps_todays_behaviour():
    """Backward compatibility: every existing caller holds only query:*."""
    user = _user(("query:eo_summary", "read"))
    assert v.effective_view(user, None) == ("mc", False)
    assert v.effective_view(user, "mc") == ("mc", False)


def test_query_wildcard_does_not_confer_the_full_grant():
    user = _user(("query:*", "read"))
    assert v.effective_view(user, "mc") == ("mc", False)


def test_scoped_caller_is_pinned_to_sales():
    user = _user(("eo:read:scoped", "read"))
    assert v.effective_view(user, None) == ("sales", True)
    assert v.effective_view(user, "mc") == ("sales", True)


def test_scoped_caller_asking_for_sales_is_not_marked_downgraded():
    user = _user(("eo:read:scoped", "read"))
    assert v.effective_view(user, "sales") == ("sales", False)


def test_full_grant_beats_scoped():
    user = _user(("eo:read:scoped", "read"), ("eo:read:full", "read"))
    assert v.effective_view(user, "mc") == ("mc", False)


def test_superuser_is_never_narrowed():
    user = _user(("eo:read:scoped", "read"), superuser=True)
    assert v.effective_view(user, "mc") == ("mc", False)


def test_anyone_may_narrow_themselves_to_sales():
    user = _user(("query:eo_summary", "read"))
    assert v.effective_view(user, "sales") == ("sales", False)


# ── payload ──────────────────────────────────────────────────────────────────


def test_payload_lists_every_field_by_default():
    assert len(v.dictionary_payload()) == len(v.FIELD_DICTIONARY)


def test_payload_can_be_filtered_to_a_view():
    sales = v.dictionary_payload("sales")
    assert {f["db_field"].upper() for f in sales} == set(v.columns_for_view("sales"))
    assert all(f["mc_only"] is False for f in sales)


def test_payload_carries_both_labels():
    entry = next(f for f in v.dictionary_payload() if f["db_field"] == "OBSOLETE_OH_ALL_AMT_TWD")
    assert entry["mc_label"] == "Obsolete All OH Amt"
    assert entry["sales_label"] == "E&O OH (All) Amt"


def test_verification_script_exclusions_all_name_real_fields():
    """A typo in EXPECTED_ABSENT would hide a genuinely missing column."""
    from scripts.verify_eo_field_dictionary import EXPECTED_ABSENT

    mapped = {s.db_field.upper() for s in v.FIELD_DICTIONARY}
    assert EXPECTED_ABSENT <= mapped
