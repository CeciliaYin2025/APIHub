"""Create ``eo_group_diff`` and widen ``eo_item_diff``, in the platform store.

Two changes, one purpose: answer "which project moved most, and why" in two tool
calls instead of nine.

``eo_group_diff`` is the sibling ``eo_item_diff`` never had. ``eo_item_diff``
ranks *items* between two periods server-side; nothing ranked *groups* — PID,
project, BU, customer or site — so an agent asked for "the project whose E&O grew
most" had to pull two full ``eo_summary`` pages and do the arithmetic in its head,
one 100-row page at a time. Measured 2026-09-17 on the Charter question: 9 tool
calls over 5 rounds, 962s, 256,579 tokens. Ranking in SQL makes page 1 the answer.

``eo_item_diff`` gains the physical measures beside the E&O ones. It already
returned obsolete OH and obsolete OPO; a question like "show the top five
components with OH, OPO and Allocated compared" still needed two more
``eo_detail`` calls and an in-head join across two periods. Nine more columns
remove that step. They are additive — every existing caller keeps its 20 columns
and can narrow with ``columns``.

Currency note, because this script is where someone would be tempted to "fix" it:
``ALL_OH_AMT`` and ``ALLOCATED_AMT`` are summed as-is. Measured 2026-09-18, every
amount column in these sources is already TWD and multiplying by
``TWD_CURRENCY_RATE`` inflates a CNY site 4.66x. See the *Currency* section of the
``eo-data-dictionary`` skill.

Why it writes to the database rather than POSTing to ``/queries/definitions``:
definition CRUD is superuser-only and no superuser credential is stored for PRD.
Writing the row directly is how ``eo_item_diff`` (id 42) was created; the cost is
that the registry does **not** reload itself, so ``apihub-api`` must be restarted
afterwards. The script says so on success.

Usage (inside the apihub-api container, which has the POSTGRES_* settings)::

    docker exec -i apihub-api python - < scripts/apply_eo_group_diff.py          # diff
    docker exec -i apihub-api python - < scripts/apply_eo_group_diff.py --apply  # write
    docker compose restart apihub-api

Exit codes: 0 nothing to do / applied, 1 differences found in dry-run, 2 error.
"""

from __future__ import annotations

import argparse
import difflib
import sys

DB_ALIAS = "DW"
GROUP_NAME = "eo"

# ── eo_group_diff ───────────────────────────────────────────────────────────

GROUP_DIFF_NAME = "eo_group_diff"

# Three stacked derived tables — filter+tag, GROUP BY, compare — the shape
# eo_item_history established, because CTEs are illegal inside the subquery the
# Hub wraps every definition in. No trailing semicolon and no inline ORDER BY:
# ordering lives in order_by so the wrapper can apply it (see the
# new-sql-definition skill, rules 2 and 3a).
GROUP_DIFF_SQL = """
SELECT CASE WHEN :group_by = 'BU' THEN 'BU' WHEN :group_by = 'CUSTOMER' THEN 'CUSTOMER'
            WHEN :group_by = 'PROJECT' THEN 'PROJECT' WHEN :group_by = 'OU' THEN 'OU'
            WHEN :group_by = 'TOTAL' THEN 'TOTAL' ELSE 'PID' END AS GROUP_LEVEL,
       x.GROUP_KEY, x.CUR_FROZEN_DATE, x.CMP_FROZEN_DATE, x.STATUS, x.CUR_ROWS, x.CMP_ROWS,
       x.CUR_EO_AMT_TWD, x.CMP_EO_AMT_TWD, x.DELTA_EO_AMT_TWD, x.ABS_DELTA_EO_AMT_TWD,
       x.CUR_OH_AMT_TWD, x.CMP_OH_AMT_TWD, x.DELTA_OH_AMT_TWD,
       x.CUR_OPO_AMT_TWD, x.CMP_OPO_AMT_TWD, x.DELTA_OPO_AMT_TWD,
       x.CUR_ALLOCATED_AMT_TWD, x.CMP_ALLOCATED_AMT_TWD, x.DELTA_ALLOCATED_AMT_TWD
FROM (
  SELECT k.GROUP_KEY, k.CUR_FROZEN_DATE, k.CMP_FROZEN_DATE, k.CUR_ROWS, k.CMP_ROWS,
         CASE WHEN k.IN_COMPARE = 0 THEN 'NEW' WHEN k.IN_CURRENT = 0 THEN 'GONE' ELSE 'BOTH' END AS STATUS,
         k.CUR_EO_AMT_TWD, k.CMP_EO_AMT_TWD,
         k.CUR_EO_AMT_TWD - k.CMP_EO_AMT_TWD AS DELTA_EO_AMT_TWD,
         ABS(k.CUR_EO_AMT_TWD - k.CMP_EO_AMT_TWD) AS ABS_DELTA_EO_AMT_TWD,
         k.CUR_OH_AMT_TWD, k.CMP_OH_AMT_TWD, k.CUR_OH_AMT_TWD - k.CMP_OH_AMT_TWD AS DELTA_OH_AMT_TWD,
         k.CUR_OPO_AMT_TWD, k.CMP_OPO_AMT_TWD, k.CUR_OPO_AMT_TWD - k.CMP_OPO_AMT_TWD AS DELTA_OPO_AMT_TWD,
         k.CUR_ALLOCATED_AMT_TWD, k.CMP_ALLOCATED_AMT_TWD,
         k.CUR_ALLOCATED_AMT_TWD - k.CMP_ALLOCATED_AMT_TWD AS DELTA_ALLOCATED_AMT_TWD
  FROM (
    SELECT f.GROUP_KEY,
           MAX(CASE WHEN f.IS_CUR = 1 THEN f.FROZEN_DATE END) AS CUR_FROZEN_DATE,
           MAX(CASE WHEN f.IS_CUR = 0 THEN f.FROZEN_DATE END) AS CMP_FROZEN_DATE,
           MAX(f.IS_CUR) AS IN_CURRENT, MAX(1 - f.IS_CUR) AS IN_COMPARE,
           SUM(f.IS_CUR) AS CUR_ROWS, SUM(1 - f.IS_CUR) AS CMP_ROWS,
           SUM(CASE WHEN f.IS_CUR = 1 THEN f.EO_AMT ELSE 0 END) AS CUR_EO_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 0 THEN f.EO_AMT ELSE 0 END) AS CMP_EO_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 1 THEN f.OH_AMT ELSE 0 END) AS CUR_OH_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 0 THEN f.OH_AMT ELSE 0 END) AS CMP_OH_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 1 THEN f.OPO_AMT ELSE 0 END) AS CUR_OPO_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 0 THEN f.OPO_AMT ELSE 0 END) AS CMP_OPO_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 1 THEN f.ALLOC_AMT ELSE 0 END) AS CUR_ALLOCATED_AMT_TWD,
           SUM(CASE WHEN f.IS_CUR = 0 THEN f.ALLOC_AMT ELSE 0 END) AS CMP_ALLOCATED_AMT_TWD
    FROM (
      SELECT CASE WHEN :group_by = 'BU' THEN ep.BU_NO WHEN :group_by = 'CUSTOMER' THEN ep.CUSTOMERGROUPNAME
                  WHEN :group_by = 'PROJECT' THEN ep.ProjectName WHEN :group_by = 'OU' THEN ep.OU
                  WHEN :group_by = 'TOTAL' THEN 'ALL' ELSE ep.PID END AS GROUP_KEY,
             ep.FROZEN_DATE,
             CASE WHEN ep.FROZEN_DATE = ISNULL(:frozen_date,
                    (SELECT MAX(FROZEN_DATE) FROM INV_EO_PID_SUMMARY_F)) THEN 1 ELSE 0 END AS IS_CUR,
             ISNULL(ep.OBSOLETE_OHOPO_ALL_AMT_TWD, 0) AS EO_AMT,
             ISNULL(ep.ALL_OH_AMT, 0)                 AS OH_AMT,
             ISNULL(ep.OPO_AMT_TWD, 0)                AS OPO_AMT,
             ISNULL(ep.ALLOCATED_AMT, 0)              AS ALLOC_AMT
      FROM INV_EO_PID_SUMMARY_F ep
      WHERE ep.FROZEN_DATE IN (ISNULL(:frozen_date, (SELECT MAX(FROZEN_DATE) FROM INV_EO_PID_SUMMARY_F)), :compare_date)
        AND (:ou IS NULL OR ep.OU IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:ou, ',')))
        AND (:bu IS NULL OR ep.BU_NO IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:bu, ',')))
        AND (:pid IS NULL OR ep.PID IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:pid, ',')))
        AND (:customer IS NULL OR ep.CUSTOMERGROUPNAME IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:customer, ',')))
        AND (ISNULL(ep.FG_Intransit_Amt,0) + ISNULL(ep.FG_Hub_Amt,0)
           + ISNULL(ep.TP70_AMT,0) + ISNULL(ep.TP75_AMT,0)
           + ISNULL(ep.FACTORY_STOCK_AMT,0) + ISNULL(ep.OPO_AMT,0)
           + ISNULL(ep.ALLOCATED_AMT,0)
           + ISNULL(ep.EXCESS_OHOPO_AMT_26W,0) + ISNULL(ep.EXCESS_OHOPO_AMT_52W,0)
           + ISNULL(ep.OBSOLETE_OHOPO_AMT,0) + ISNULL(ep.OBSOLETE_OHOPO_PLUSAMT,0)
           + ISNULL(ep.WIP_AMT,0)) <> 0
    ) f
    GROUP BY f.GROUP_KEY
  ) k
  WHERE k.CUR_EO_AMT_TWD <> 0 OR k.CMP_EO_AMT_TWD <> 0
) x
WHERE (:status IS NULL OR x.STATUS IN (SELECT UPPER(LTRIM(RTRIM(value))) FROM STRING_SPLIT(:status, ',')))
""".strip()

GROUP_DIFF_PARAMS = {
    "group_by": {
        "type": "string",
        "required": False,
        "description": (
            "What to rank: 'PID' (default), 'PROJECT' (ProjectName), 'BU' (BU_NO), "
            "'CUSTOMER' (CUSTOMERGROUPNAME), 'OU' (site), or 'TOTAL' for one company-wide row. "
            "Case-sensitive; any other value is treated as 'PID'."
        ),
    },
    "frozen_date": {
        "type": "date",
        "required": False,
        "description": "The later period. Omit for the latest. Must be an actual frozen date.",
    },
    "compare_date": {
        "type": "date",
        "required": True,
        "description": (
            "The earlier period to compare against. Must be an actual frozen date — call "
            "eo_frozen_dates; a date that is not one makes every group look NEW."
        ),
    },
    "ou": {
        "type": "string",
        "required": False,
        "description": "Site filter, comma-separated (DW, OND, SC, SI, SN, SP, TN1). Omit for all sites.",
    },
    "bu": {
        "type": "string",
        "required": False,
        "description": "Division (BU_NO) filter, comma-separated. Omit for all.",
    },
    "pid": {
        "type": "string",
        "required": False,
        "description": "Project PID filter, comma-separated. Omit for all.",
    },
    "customer": {
        "type": "string",
        "required": False,
        "description": (
            "CUSTOMERGROUPNAME filter, comma-separated, exact match (e.g. 'Charter'). Resolve the "
            "name with eo_dimensions dim='CUSTOMER' first. Blank customer groups cannot be "
            "selected. Omit for all customers."
        ),
    },
    "status": {
        "type": "string",
        "required": False,
        "description": (
            "Keep only these STATUS values, comma-separated, case-insensitive: 'NEW' (the group "
            "carries E&O only in the current period), 'GONE' (only in the compare period), 'BOTH'. "
            "Omit for all three."
        ),
    },
}

# Biggest movers in either direction first, matching eo_item_diff. For "grew the
# most" the caller passes order_by ["-DELTA_EO_AMT_TWD"]; the result_description
# says so, because ABS ordering puts a large decrease at the top too.
GROUP_DIFF_ORDER_BY = [
    {"column": "ABS_DELTA_EO_AMT_TWD", "direction": "DESC"},
    {"column": "GROUP_KEY", "direction": "ASC"},
]

GROUP_DIFF_DESCRIPTION = (
    "Rank PIDs, projects, BUs, customer groups or sites by how much their E&O amount changed "
    "between two frozen periods, and show the OH / OPO / Allocated behind the change in the same "
    "row. Use it for \"which project/BU/customer moved most between period A and B\": the ranking "
    "is done server-side, so page 1 with page_size=5 IS the top five — do NOT pull two eo_summary "
    "pages and subtract them yourself. eo_item_diff is the same question one level down, at item "
    "level. About 2s."
)

GROUP_DIFF_RESULT_DESCRIPTION = """20 columns, one row per group that carries E&O in either period, \
sorted by ABS_DELTA_EO_AMT_TWD descending — biggest mover in EITHER direction first. For "grew the \
most" pass order_by ["-DELTA_EO_AMT_TWD"]; for "fell the most" ["DELTA_EO_AMT_TWD"].

GROUP_LEVEL echoes the group_by you passed; GROUP_KEY is the value (a PID, ProjectName, BU_NO, \
CUSTOMERGROUPNAME, OU, or the literal 'ALL' for TOTAL). Unlike eo_item_diff there is NO min/count \
attribution here — a group key is the thing being grouped, so it is exact.

Periods: CUR_FROZEN_DATE, CMP_FROZEN_DATE (NULL when the group has no row in that period). \
CUR_ROWS / CMP_ROWS count the eo_summary rows behind each side. STATUS: NEW (no row in the compare \
period), GONE (no row in the current period), BOTH; filter with the status parameter.

Amounts, ALL TWD — every amount column in this source is already TWD, suffix or not, so these are \
summed as-is and must never be multiplied by TWD_CURRENCY_RATE:
  CUR_/CMP_/DELTA_EO_AMT_TWD          E&O: OBSOLETE_OHOPO_ALL_AMT_TWD, MC "Obsolete All OH+OPO Amt" \
= Sales "E&O OH + OPO Amt". This is the ranking metric and reconciles to eo_summary for the same filter.
  CUR_/CMP_/DELTA_OH_AMT_TWD          physical on hand: ALL_OH_AMT
  CUR_/CMP_/DELTA_OPO_AMT_TWD         open PO: OPO_AMT_TWD
  CUR_/CMP_/DELTA_ALLOCATED_AMT_TWD   allocated: ALLOCATED_AMT
ABS_DELTA_EO_AMT_TWD is the absolute E&O delta, the default sort key.

E&O and physical on-hand are DIFFERENT METRICS and rank differently — on 2026-09-17 Charter's \
"biggest increase" was DXR by OH_AMT but DX8 by EO_AMT, where DXR's E&O had fallen. Say which \
column you ranked on, and prefer the E&O one unless the user asked about physical stock.

Rows whose twelve stock/commitment amounts are all zero are excluded, matching the official report \
and eo_summary. A customer/ou/bu/pid filter is applied before grouping, so a group shared with other \
customers shows only the filtered share."""

# ── eo_item_diff: nine more columns ─────────────────────────────────────────

ITEM_DIFF_NAME = "eo_item_diff"

# Identical to the deployed definition except for three measures carried up
# through the three levels: physical OH, open PO and allocated, so a "top five
# components, with OH/OPO/Allocated compared" question needs no eo_detail calls.
ITEM_DIFF_SQL = """
SELECT x.* FROM (
SELECT k.Site, k.Item_No, k.Item_Description, k.BU_NO, k.BU_NO_N, k.PID, k.PID_N,
       k.CUSTOMERGROUPNAME, k.CUSTOMERGROUP_N,
       k.CUR_FROZEN_DATE, k.CMP_FROZEN_DATE,
       CASE WHEN k.IN_COMPARE = 0 THEN 'NEW' WHEN k.IN_CURRENT = 0 THEN 'GONE' ELSE 'BOTH' END AS STATUS,
       k.CUR_EO_OHOPO_AMT_TWD, k.CMP_EO_OHOPO_AMT_TWD,
       k.CUR_EO_OHOPO_AMT_TWD - k.CMP_EO_OHOPO_AMT_TWD AS DELTA_EO_OHOPO_AMT_TWD,
       ABS(k.CUR_EO_OHOPO_AMT_TWD - k.CMP_EO_OHOPO_AMT_TWD) AS ABS_DELTA_EO_OHOPO_AMT_TWD,
       k.CUR_EO_OH_AMT_TWD, k.CMP_EO_OH_AMT_TWD, k.CUR_EO_OPO_AMT_TWD, k.CMP_EO_OPO_AMT_TWD,
       k.CUR_ALL_OH_AMT_TWD, k.CMP_ALL_OH_AMT_TWD,
       k.CUR_ALL_OH_AMT_TWD - k.CMP_ALL_OH_AMT_TWD AS DELTA_ALL_OH_AMT_TWD,
       k.CUR_OPO_AMT_TWD, k.CMP_OPO_AMT_TWD,
       k.CUR_OPO_AMT_TWD - k.CMP_OPO_AMT_TWD AS DELTA_OPO_AMT_TWD,
       k.CUR_ALLOCATED_AMT_TWD, k.CMP_ALLOCATED_AMT_TWD,
       k.CUR_ALLOCATED_AMT_TWD - k.CMP_ALLOCATED_AMT_TWD AS DELTA_ALLOCATED_AMT_TWD
FROM (
  SELECT f.Site, f.Item_No,
         MAX(f.Item_Description) AS Item_Description,
         MIN(f.BU_NO) AS BU_NO, COUNT(DISTINCT f.BU_NO) AS BU_NO_N,
         MIN(f.PID) AS PID, COUNT(DISTINCT f.PID) AS PID_N,
         MIN(f.CUSTOMERGROUPNAME) AS CUSTOMERGROUPNAME, COUNT(DISTINCT f.CUSTOMERGROUPNAME) AS CUSTOMERGROUP_N,
         MAX(CASE WHEN f.IS_CUR = 1 THEN f.Frozen_Date END) AS CUR_FROZEN_DATE,
         MAX(CASE WHEN f.IS_CUR = 0 THEN f.Frozen_Date END) AS CMP_FROZEN_DATE,
         MAX(f.IS_CUR) AS IN_CURRENT,
         MAX(1 - f.IS_CUR) AS IN_COMPARE,
         SUM(CASE WHEN f.IS_CUR = 1 THEN f.EO_OHOPO ELSE 0 END) AS CUR_EO_OHOPO_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 0 THEN f.EO_OHOPO ELSE 0 END) AS CMP_EO_OHOPO_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 1 THEN f.EO_OH ELSE 0 END) AS CUR_EO_OH_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 0 THEN f.EO_OH ELSE 0 END) AS CMP_EO_OH_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 1 THEN f.EO_OPO ELSE 0 END) AS CUR_EO_OPO_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 0 THEN f.EO_OPO ELSE 0 END) AS CMP_EO_OPO_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 1 THEN f.OH_ALL ELSE 0 END) AS CUR_ALL_OH_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 0 THEN f.OH_ALL ELSE 0 END) AS CMP_ALL_OH_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 1 THEN f.OPO_ALL ELSE 0 END) AS CUR_OPO_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 0 THEN f.OPO_ALL ELSE 0 END) AS CMP_OPO_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 1 THEN f.ALLOC_ALL ELSE 0 END) AS CUR_ALLOCATED_AMT_TWD,
         SUM(CASE WHEN f.IS_CUR = 0 THEN f.ALLOC_ALL ELSE 0 END) AS CMP_ALLOCATED_AMT_TWD
  FROM (
    SELECT v.Frozen_Date, v.Site, v.Item_No, v.Item_Description, v.BU_NO, v.PID, v.CUSTOMERGROUPNAME,
           CASE WHEN v.Frozen_Date = ISNULL(:frozen_date,
                  (SELECT MAX(FROZEN_DATE) FROM INV_EO_PID_SUMMARY_F)) THEN 1 ELSE 0 END AS IS_CUR,
           ISNULL(v.OBSOLETE_OHOPO_AMT_TWD, 0) + ISNULL(v.OBSOLETE_OH_TP70_AMT_TWD, 0)
             + ISNULL(v.OBSOLETE_OH_TP75_AMT, 0) AS EO_OHOPO,
           ISNULL(v.OBSOLETE_OH_ALL_AMT, 0) AS EO_OH,
           ISNULL(v.OBSOLETE_OPO_AMT_TWD, 0) AS EO_OPO,
           ISNULL(v.ALL_OH_AMT, 0) AS OH_ALL,
           ISNULL(v.OPO_AMT_TWD, 0) AS OPO_ALL,
           ISNULL(v.ALLOCATED_AMT, 0) AS ALLOC_ALL
    FROM INV_EO_PID_Detail_Unrecoverable_V v
    WHERE v.Frozen_Date IN (ISNULL(:frozen_date, (SELECT MAX(FROZEN_DATE) FROM INV_EO_PID_SUMMARY_F)), :compare_date)
      AND (:ou IS NULL OR v.Site IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:ou, ',')))
      AND (:bu IS NULL OR v.BU_NO IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:bu, ',')))
      AND (:pid IS NULL OR v.PID IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:pid, ',')))
      AND (:customer IS NULL OR v.CUSTOMERGROUPNAME IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(:customer, ',')))
  ) f
  GROUP BY f.Site, f.Item_No
) k
WHERE k.CUR_EO_OHOPO_AMT_TWD <> 0 OR k.CMP_EO_OHOPO_AMT_TWD <> 0
) x
WHERE (:status IS NULL OR x.STATUS IN (SELECT UPPER(LTRIM(RTRIM(value))) FROM STRING_SPLIT(:status, ',')))
""".strip()

ITEM_DIFF_RESULT_DESCRIPTION = """29 columns, one row per Site + Item_No that carries E&O in either \
period, sorted by ABS_DELTA_EO_OHOPO_AMT_TWD descending. Identity: Site, Item_No, Item_Description. \
Attribution across both periods: BU_NO, PID, CUSTOMERGROUPNAME are the MIN when the item sits under \
several, with BU_NO_N / PID_N / CUSTOMERGROUP_N counting how many distinct values the item really \
has. WHEN ANY OF THOSE COUNTS IS ABOVE 1 YOU MUST SAY SO in the answer (print the count, or write \
'spread across N BUs') — the amount is the item's total and is correct, but the attribution beside \
it is only the lowest of several, and a reader who is not told will take it as the owner. Periods: \
CUR_FROZEN_DATE, CMP_FROZEN_DATE (NULL when the item has no row in that period). STATUS: NEW (no row \
in the compare period), GONE (no row in the current period), BOTH (rows in both — one side's amount \
can still be 0); filter it with the status parameter.

Amounts, ALL TWD — every amount column in this source is already TWD, suffix or not, so none of \
them may be multiplied by TWD_CURRENCY_RATE. E&O: CUR_/CMP_EO_OHOPO_AMT_TWD = OBSOLETE_OHOPO_AMT_TWD \
+ OBSOLETE_OH_TP70_AMT_TWD + OBSOLETE_OH_TP75_AMT summed over the item's rows — the item-level "E&O \
OH + OPO Amt", which sums exactly to eo_summary's OBSOLETE_OHOPO_ALL_AMT_TWD for the same filter; \
DELTA_EO_OHOPO_AMT_TWD (current minus compare) and ABS_DELTA_EO_OHOPO_AMT_TWD; split into \
CUR_/CMP_EO_OH_AMT_TWD (OBSOLETE_OH_ALL_AMT) and CUR_/CMP_EO_OPO_AMT_TWD (OBSOLETE_OPO_AMT_TWD).

The PHYSICAL measures beside the E&O ones, each with CUR_ / CMP_ / DELTA_: ALL_OH_AMT_TWD (physical \
on hand, ALL_OH_AMT), OPO_AMT_TWD (open PO), ALLOCATED_AMT_TWD (allocated). These answer "show OH, \
OPO and Allocated for these components across the two periods" without a second eo_detail call. Note \
EO_OH is the obsolete part of on-hand while ALL_OH is the whole of it — they are different columns \
and ALL_OH is the larger.

A customer filter keeps only that customer's rows, so an item shared with other customers shows only \
this customer's share. To rank groups rather than items — which PID, project, BU or customer moved \
most — use eo_group_diff instead."""


# ── apply ───────────────────────────────────────────────────────────────────


def _diff(label: str, old: str, new: str) -> bool:
    old, new = (old or "").strip(), (new or "").strip()
    if old == new:
        print(f"  {label}: already up to date")
        return False
    print(f"\n--- {label} ---")
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile="current", tofile="proposed", lineterm=""
    ):
        print(line)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run)")
    args = parser.parse_args()

    from app.db.session import SessionLocal
    from app.models.sql_definition import SqlDefinitionModel

    session = SessionLocal()
    changed = False
    try:
        # eo_group_diff — insert or update.
        row = (
            session.query(SqlDefinitionModel)
            .filter_by(name=GROUP_DIFF_NAME, db_alias=DB_ALIAS)
            .one_or_none()
        )
        print(f"{GROUP_DIFF_NAME}: {'exists (id=%s)' % row.id if row else 'does not exist — will be created'}")
        fields = {
            "group_name": GROUP_NAME,
            "description": GROUP_DIFF_DESCRIPTION,
            "result_description": GROUP_DIFF_RESULT_DESCRIPTION,
            "sql": GROUP_DIFF_SQL,
            "permission": f"query:{GROUP_DIFF_NAME}",
            "params": GROUP_DIFF_PARAMS,
            "order_by": GROUP_DIFF_ORDER_BY,
            "enabled": True,
        }
        if row is None:
            changed = True
            if args.apply:
                session.add(SqlDefinitionModel(name=GROUP_DIFF_NAME, db_alias=DB_ALIAS, **fields))
        else:
            for key, value in fields.items():
                current = getattr(row, key)
                if isinstance(value, str):
                    if _diff(f"{GROUP_DIFF_NAME}.{key}", current, value):
                        changed = True
                        if args.apply:
                            setattr(row, key, value)
                elif current != value:
                    print(f"  {GROUP_DIFF_NAME}.{key}: {current!r} -> {value!r}")
                    changed = True
                    if args.apply:
                        setattr(row, key, value)

        # eo_item_diff — widen. Must already exist.
        item = (
            session.query(SqlDefinitionModel)
            .filter_by(name=ITEM_DIFF_NAME, db_alias=DB_ALIAS)
            .one_or_none()
        )
        if item is None:
            print(f"{ITEM_DIFF_NAME} is missing — refusing to guess", file=sys.stderr)
            return 2
        print(f"\n{ITEM_DIFF_NAME}: id={item.id}")
        for key, value in (("sql", ITEM_DIFF_SQL), ("result_description", ITEM_DIFF_RESULT_DESCRIPTION)):
            if _diff(f"{ITEM_DIFF_NAME}.{key}", getattr(item, key), value):
                changed = True
                if args.apply:
                    setattr(item, key, value)

        if not changed:
            print("\nNothing to change.")
            return 0

        if not args.apply:
            print("\nDry run — re-run with --apply to write.")
            return 1

        session.commit()
    except Exception as exc:  # noqa: BLE001 - surface the reason, roll back the write
        session.rollback()
        print(f"Failed: {exc}", file=sys.stderr)
        return 2
    finally:
        session.close()

    print("\nApplied.")
    print("The registry does NOT reload on a direct database write — restart the API to pick these up:")
    print("    docker compose restart apihub-api")
    print("Then re-fetch GET /api/v1/DW/tools/openapi.json?group=eo into the Dify tool provider,")
    print("which stores its own snapshot of the schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
