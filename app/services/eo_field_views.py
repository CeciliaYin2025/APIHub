"""Canonical E&O field dictionary: db_field × MC label × Sales label × view.

Two different audiences read the same `INV_EO_PID_SUMMARY_F` row:

- **MC view** — the online report "MC E&O Summary by Site" (`EO_Summary_v4.0.cpt`).
  Full technical names, including the TP70/TP75 exclusion conditions, the E&O
  breakdowns, and the loss-evaluation block.
- **Sales view** — the Excel sent to Sales ("E&O Summary - Sales", columns A–X).
  Fewer columns, plainer names, and — the rule that catches everyone —
  **"Obsolete" is renamed "E&O"**.

So `OBSOLETE_OH_ALL_AMT_TWD` is called "Obsolete All OH Amt" by MC and
"E&O OH (All) Amt" by Sales. Same column, two names. A user quoting either one
must resolve to the same field, which is what `resolve_label` is for.

## Why the Sales view is an allowlist

`eo_summary` is `SELECT ep.*`, so it returns every column the table has —
including ones neither view lists (raw QTY columns, `SUMMARY_ID`, the non-TWD
twins). Defining Sales as "everything except the MC-only list" would leak each
of those, and would leak any column added to the table later. The Sales view is
therefore a positive allowlist: a column reaches a Sales caller only by being
named here.

A few entries are `_plumbing`: columns neither report prints, but which a Sales
caller needs because an API response has none of the context a rendered workbook
has. They carry no display label — there is none to carry.

## Matching is case-insensitive

`SELECT ep.*` returns the table's own casing, which is inconsistent in the
source (`FG_Intransit_Amt`, `OBSOLETE_OH_ALL_AMT_TWD`, `ProjectName`,
`AliasProjectNAme`). Every lookup normalises to upper case; never compare these
names with `==`.

Sources: `EO_Summary_v4.0.cpt` for the MC labels, the 2026-02-26 Sales workbook
(columns A–X) for the Sales labels. Columns Y onward in that workbook
(`Management Mandate`, `Category`, `Rax's reply`, `Type`, and the dated notes)
are MC/IT annotations typed into Excel for issue tracking — they are not report
columns and not database fields, so they are deliberately absent here.
"""

from dataclasses import dataclass
from typing import Any

MC = "mc"
SALES = "sales"
VIEWS = (MC, SALES)
DEFAULT_VIEW = MC

# Definitions this dictionary governs. Others are returned untouched — the
# spec covers the E&O *summary*, and silently masking a definition nobody has
# mapped would drop columns with no way to tell it was deliberate.
GOVERNED_DEFINITIONS = frozenset({"eo_summary"})


@dataclass(frozen=True)
class FieldSpec:
    """One column, under both names, with the views that may see it."""

    db_field: str
    mc_label: str | None
    sales_label: str | None
    group: str
    views: frozenset[str]
    note: str | None = None

    @property
    def mc_only(self) -> bool:
        return SALES not in self.views


def _both(db_field: str, mc_label: str, sales_label: str, group: str, note: str | None = None) -> FieldSpec:
    return FieldSpec(db_field, mc_label, sales_label, group, frozenset(VIEWS), note)


def _mc_only(db_field: str, mc_label: str, group: str, note: str | None = None) -> FieldSpec:
    return FieldSpec(db_field, mc_label, None, group, frozenset({MC}), note)


def _plumbing(db_field: str, note: str) -> FieldSpec:
    """A column both views need but neither report prints, so it has no label.

    The reports are rendered per site, so currency is implicit on the page. An
    API response has no such context: a Sales caller receives TP75_Amt and
    WIP_AMT, which have no _TWD twin and are therefore in local currency, and
    without the code and the rate those two numbers cannot be interpreted or
    converted. Carrying them is not a leak — a rate is not an E&O figure.
    """
    return FieldSpec(db_field, None, None, "plumbing", frozenset(VIEWS), note)


# ── Section 1: present in both views ─────────────────────────────────────────
_SHARED: tuple[FieldSpec, ...] = (
    _both("OU", "OU", "OU", "identity"),
    _both("BU", "BU", "BU", "identity"),
    _both("BU_NO", "Division Code", "Division Code", "identity"),
    _both("CUSTOMERGROUPNAME", "Customer Group Name", "Customer Group Name", "identity"),
    _both(
        "ProjectName",
        "Project Name",
        "Project Name",
        "identity",
        "MC shows ProjectName and AliasProjectNAme as two columns; Sales keeps one.",
    ),
    _both(
        "AliasProjectNAme",
        "Project Name",
        "Project Name",
        "identity",
        "Second of MC's two Project Name columns. Note the source spelling: NAme.",
    ),
    _both("PID", "PID", "PID", "identity"),
    _both(
        "PID_Ref_Cost_TWD",
        "PID Ref.Cost",
        "PID Ref.Cost",
        "cost",
        "Computed by the eo_summary definition as PID_Ref_Cost x TWD_CURRENCY_RATE.",
    ),
    _both("PID_Ref_Cost", "PID Ref.Cost", "PID Ref.Cost", "cost", "Pre-conversion, local currency."),
    _both("FG_Intransit_Amt_TWD", "FG on the way Amt", "FG In Transit Amt", "stock", "Renamed between views."),
    _both("FG_Hub_Amt_TWD", "FG Hub Stock Amt", "Hub OH Amt", "stock", "Renamed between views."),
    _both("WIP_AMT", "WIP Amt", "WIP Amt", "stock", "No _TWD twin — local currency."),
    _both("TP75_Amt", "TP75 Amt", "TP75 Amt", "stock", "No _TWD twin — local currency."),
    _both("TP70_Amt_TWD", "TP70 & TP82 Amt", "TP70&TP82 Amt", "stock", "Spacing differs only."),
    _both(
        "FACTORY_STOCK_AMT_TWD",
        "Available OH Amt (exclude TP75 & TP80 / TP70 & TP82)",
        "Non-TP70 & Non-TP75 OH Amt",
        "stock",
        "Same column: MC names it by what it is, Sales by what it excludes.",
    ),
    _both("OPO_AMT_TWD", "OPO Amt", "OPO Amt", "commitment"),
    _both(
        "ALLOCATED_AMT_TWD",
        "Allocated Amt (C)",
        "Allocated Amt",
        "commitment",
        "MC splits allocated into (C) and (E) plus a total; Sales shows only the total.",
    ),
    _both(
        "PLANNED_AMT_TWD",
        "Allocated Amt (E)",
        "Allocated Amt",
        "commitment",
        "The (E) half of MC's split. Sales sees it only inside the total.",
    ),
    _both(
        "OBSOLETE_OH_ALL_AMT_TWD",
        "Obsolete All OH Amt",
        "E&O OH (All) Amt",
        "obsolete",
        "Obsolete -> E&O. The single most-confused rename.",
    ),
    _both("OBSOLETE_OPO_AMT_TWD", "Obsolete OPO Amt", "E&O OPO Amt", "obsolete", "Obsolete -> E&O."),
    _both("OBSOLETE_OHOPO_ALL_AMT_TWD", "Obsolete All OH+OPO Amt", "E&O OH + OPO Amt", "obsolete", "Obsolete -> E&O."),
    _both(
        "FEEDBACK",
        "MC's Highlight",
        "MC's Highlight",
        "feedback",
        "Joined from INV_EO_PID_Summary_Feedback_R where type='MC'.",
    ),
    _both("Item_No", "Item No", "Item No", "feedback", "Joined from ds_sales_reply — the top 3 red-flag items."),
    _both(
        "ITEM_OBSOLETE_OHOPO_AMT",
        "Obsolete All OH+OPO Amt",
        "E&O OH + OPO Amt",
        "feedback",
        "Item-level figure from ds_sales_reply: OBSOLETE_OHOPO_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD "
        "+ OBSOLETE_OH_TP75_AMT. Shares its display name with the PID-level column — check the grain.",
    ),
    _both(
        "ACTION_PLAN_OH",
        "Sales' Action Plan for OH",
        "Sales' Action Plan for OH",
        "feedback",
        "Joined from INV_EO_PID_DETAIL_Feedback_R.",
    ),
    _both(
        "ACTION_PLAN_OPO",
        "Sales' Action Plan for OPO",
        "Sales' Action Plan for OPO",
        "feedback",
        "Joined from INV_EO_PID_DETAIL_Feedback_R.",
    ),
    _both(
        "ACTION_PLAN_DUE_DATE",
        "Due Date for Sales' Action",
        "Sales AP Due Date",
        "feedback",
        "Abbreviated in the Sales workbook.",
    ),
    _plumbing(
        "CURRENCY_CODE",
        "Needed to read TP75_Amt and WIP_AMT, which the Sales view carries and which have no "
        "_TWD twin. Not printed by either report — the workbooks are per site, so currency is "
        "implicit there and explicit here.",
    ),
    _plumbing(
        "TWD_CURRENCY_RATE",
        "The rate the _TWD columns were converted at; lets a caller convert the local-currency columns the same way.",
    ),
    _both(
        "FROZEN_DATE",
        "Frozen Date",
        "Frozen Date",
        "identity",
        "MC has it as a data column; the Sales workbook prints it in the sheet header instead. "
        "An API response has no header, so it is returned in both views — without it a row "
        "cannot be dated.",
    ),
)

# ── Section 2: MC view only ──────────────────────────────────────────────────
_MC_ONLY: tuple[FieldSpec, ...] = (
    _mc_only("TP80_Amt", "TP80 Amt", "stock"),
    _mc_only(
        "UNAVAILABLE_OH_AMT",
        "Unavailable OH Amt (exclude TP75 & TP80 / TP70 & TP82)",
        "stock",
        "Subtotal row ds_TP80AMT in the report.",
    ),
    _mc_only("ALL_OH_AMT", "All OH Amt", "stock", "No _TWD twin — local currency."),
    _mc_only(
        "TOTAL_INV_AMT",
        "Total INV Amt",
        "stock",
        "Report-side formula (TP75 + TP70 + Available + WIP ...), not a stored column.",
    ),
    _mc_only("LOCK_QTY", "WO/PO lock Qty for APS", "reference"),
    _mc_only("MSIC", "Non Work Order transaction(Y/N)", "reference"),
    _mc_only("OBSOLETE_OH_AMT_TWD", "Obsolete Non_TP70 & TP75 OH Amt", "obsolete"),
    _mc_only("OBSOLETE_OHOPO_AMT_TWD", "Obsolete Non_TP70 & TP75 OH + OPO Amt", "obsolete"),
    _mc_only("OBSOLETE_OH_TP70_AMT_TWD", "Obsolete TP70 OH Amt", "obsolete"),
    _mc_only("OBSOLETE_OH_TP75_AMT_TWD", "Obsolete TP75 OH Amt", "obsolete"),
    _mc_only("OBSOLETE_OH_PLUSAMT_TWD", "Obsolete Plus Non_TP70 & TP75 OH Amt", "obsolete"),
    _mc_only("OBSOLETE_OPO_PLUSAMT_TWD", "Obsolete Plus OPO Amt", "obsolete"),
    _mc_only("OBSOLETE_OHOPO_PLUSAMT_TWD", "Obsolete Plus Non_TP70 & TP75 OH + OPO Amt", "obsolete"),
    _mc_only("EXCESS_OH_AMT_26W_TWD", "Excess 26WK OH Amt", "excess"),
    _mc_only("EXCESS_OPO_AMT_26W_TWD", "Excess 26WK OPO Amt", "excess"),
    _mc_only("EXCESS_OHOPO_AMT_26W_TWD", "Excess 26WK OH + OPO Amt", "excess"),
    _mc_only("EXCESS_OH_AMT_52W_TWD", "Excess 52WK OH Amt", "excess"),
    _mc_only("EXCESS_OPO_AMT_52W_TWD", "Excess 52WK OPO Amt", "excess"),
    _mc_only("EXCESS_OHOPO_AMT_52W_TWD", "Excess 52WK OH + OPO Amt", "excess"),
    _mc_only(
        "SALES_OBSOLETE_OPO_AMT",
        "Obsolete OPO Amt (Sales' Reply)",
        "loss",
        "From ufn_GetMCUnrecoverableBase. Marked 'new requirement for MC' in the source SQL.",
    ),
    _mc_only("OPO_unrecoverable", "Obsolete OPO Actual Loss Amt", "loss"),
    _mc_only("Obsolete_OPO_EST_Loss_Amt", "Obsolete OPO EST. Loss Amt", "loss"),
    _mc_only("SALES_OBSOLETE_OH_ALL_AMT", "Obsolete All OH Amt (Sales' Reply)", "loss"),
    _mc_only("OH_unrecoverable", "Obsolete All OH Actual Loss Amt", "loss"),
    _mc_only("Obsolete_All_OH_EST_Loss_Amt", "Obsolete All OH EST. Loss Amt", "loss"),
    _mc_only(
        "Cust_Liable_Amt",
        "Customer Liable Amt (RESERVE_TWD)",
        "loss",
        "From INV_DOS_SUMMARY_EDIT_V; feeds the reserve calculation.",
    ),
    _mc_only("TARGET_AMT", "Meeting Goals for Obsolete All OH+OPO Amt", "goal", "Feedback_R where type='SALES'."),
    _mc_only(
        "UNRECOVERABLE_AMT", "Unrecoverable Amt", "unrecoverable", "INV_EO_UNRECOVERABLE_V block — MC only, in full."
    ),
    _mc_only(
        "BUYDOWN_AMT",
        "Buy Down Amt",
        "unrecoverable",
        "BU-level value repeated on every customer-group row; never SUM across rows.",
    ),
    _mc_only(
        "RESERVED_AMT",
        "Reserved Amt",
        "unrecoverable",
        "BU-level value repeated on every customer-group row; never SUM across rows.",
    ),
    _mc_only(
        "BALANCE_AMT",
        "Balance",
        "unrecoverable",
        "Report-side figure. There is no stored balance column and the obvious arithmetic does not reproduce it.",
    ),
)

FIELD_DICTIONARY: tuple[FieldSpec, ...] = _SHARED + _MC_ONLY

# ── Indexes (all upper-cased; see the module docstring) ──────────────────────
_BY_DB_FIELD: dict[str, FieldSpec] = {spec.db_field.upper(): spec for spec in FIELD_DICTIONARY}

_BY_LABEL: dict[str, list[FieldSpec]] = {}
for _spec in FIELD_DICTIONARY:
    for _label in (_spec.mc_label, _spec.sales_label):
        if _label:
            _BY_LABEL.setdefault(_label.upper(), []).append(_spec)

_VIEW_COLUMNS: dict[str, frozenset[str]] = {
    view: frozenset(spec.db_field.upper() for spec in FIELD_DICTIONARY if view in spec.views) for view in VIEWS
}


def normalise_view(view: str | None) -> str:
    """Return a valid view name. Raises ValueError on anything else."""
    if view is None:
        return DEFAULT_VIEW
    candidate = view.strip().lower()
    if candidate not in VIEWS:
        raise ValueError(f"Unknown view '{view}'. Valid views: {', '.join(VIEWS)}")
    return candidate


def columns_for_view(view: str) -> frozenset[str]:
    """Upper-cased db_field names visible in *view*."""
    return _VIEW_COLUMNS[normalise_view(view)]


def spec_for_field(db_field: str) -> FieldSpec | None:
    return _BY_DB_FIELD.get(db_field.upper())


def resolve_label(text: str) -> list[FieldSpec]:
    """Map a display name (either view's) back to its field(s).

    Returns a list because MC and Sales both reuse one label at two grains:
    "E&O OH + OPO Amt" is the PID-level `OBSOLETE_OHOPO_ALL_AMT_TWD` in one
    column and the item-level `ITEM_OBSOLETE_OHOPO_AMT` in another. Handing back
    both, rather than picking, keeps the ambiguity visible to the caller.
    """
    key = text.strip().upper()
    if key in _BY_LABEL:
        return list(_BY_LABEL[key])
    # Fall back to db_field, so a caller may pass either kind of name.
    spec = _BY_DB_FIELD.get(key)
    return [spec] if spec else []


def is_governed(definition_name: str) -> bool:
    return definition_name in GOVERNED_DEFINITIONS


def apply_view(
    definition_name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    view: str,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Project *columns*/*rows* down to *view*.

    Returns ``(kept_columns, kept_rows, masked_columns)``. A definition this
    dictionary does not govern, or the MC view (which sees everything), passes
    straight through — including columns absent from the dictionary, because MC
    is the full report and the dictionary is not guaranteed to enumerate every
    column `SELECT ep.*` returns.

    For the Sales view the allowlist decides: a column reaches the caller only
    if the dictionary names it as visible to Sales.
    """
    view = normalise_view(view)
    if view == MC or not is_governed(definition_name):
        return columns, rows, []

    allowed = columns_for_view(view)
    kept = [c for c in columns if c.upper() in allowed]
    masked = [c for c in columns if c.upper() not in allowed]
    if not masked:
        return columns, rows, []

    kept_set = {c for c in kept}
    trimmed = [{k: v for k, v in row.items() if k in kept_set} for row in rows]
    return kept, trimmed, masked


def dictionary_payload(view_filter: str | None = None) -> list[dict[str, Any]]:
    """The dictionary as plain dicts, for the API and the MCP resource."""
    specs = FIELD_DICTIONARY
    if view_filter is not None:
        wanted = normalise_view(view_filter)
        specs = tuple(s for s in specs if wanted in s.views)
    return [
        {
            "db_field": s.db_field,
            "mc_label": s.mc_label,
            "sales_label": s.sales_label,
            "group": s.group,
            "views": sorted(s.views),
            "mc_only": s.mc_only,
            "note": s.note,
        }
        for s in specs
    ]


# ── Who gets which view ──────────────────────────────────────────────────────
# Two permissions, both with action "read":
#
#   eo:read:scoped  — this caller sees the Sales view and cannot opt out
#   eo:read:full    — this caller sees the MC view (explicit grant)
#
# A caller holding NEITHER keeps the behaviour they have today: the full MC
# view. That default is deliberate. Every existing caller — the twelve
# query_dw_eo_* MCP tools, the Admin UI, the API keys — predates this dictionary
# and holds only `query:eo_*`, so defaulting to masked would silently strip
# columns from all of them on deploy. Masking is opt-in per principal, the same
# reasoning that made `auth_source` default to 'local' rather than gate SSO.
#
# Note that a `query:*` wildcard does NOT confer either: the wildcard matches on
# a resource prefix, and these resources start with "eo:".

PERM_SCOPED = "eo:read:scoped"
PERM_FULL = "eo:read:full"
PERM_ACTION = "read"


def effective_view(user, requested: str | None) -> tuple[str, bool]:
    """Resolve the view this *user* actually gets.

    Returns ``(view, downgraded)``. ``downgraded`` is True when the caller asked
    for a wider view than their permissions allow — the request still succeeds
    at the narrower view rather than 403-ing, because these tools are driven by
    an LLM and a hard failure there produces retries rather than a corrected
    request. The response says which view was applied, so the narrowing is
    reported rather than silent.
    """
    from app.services.auth_service import has_permission  # local: avoids an import cycle

    asked = normalise_view(requested)

    if user is not None and getattr(user, "is_superuser", False):
        return asked, False
    if user is not None and has_permission(user, PERM_FULL, PERM_ACTION):
        return asked, False
    if user is not None and has_permission(user, PERM_SCOPED, PERM_ACTION):
        return SALES, asked != SALES
    return asked, False
