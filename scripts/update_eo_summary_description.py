"""Update the eo_summary definition's tool description for the MC / Sales views.

`description` + `result_description` are what the MCP server turns into the
`query_dw_eo_summary` tool description, i.e. the only thing an LLM reads before
choosing columns. Once the definition gained a `view` argument, that text needed
to say so — and to spell out that the two views use *different names for the
same column*, which is the single most common way to answer confidently about
the wrong figure.

Reads before it writes, and diffs. The stored text is edited by people through
the Admin UI, so it is not safe to assume the repo knows what is currently
there. Dry-run is the default; nothing is written without --apply.

Both fields are updated: `description` gains a sentence naming the `view`
argument, `result_description` gains the rename table, what the Sales view omits,
and the meaning of `view_downgraded`. The existing column enumeration and
currency caveats are preserved verbatim — they are accurate and the model needs
them; this merges into that text rather than replacing it.

Usage:
    APIHUB_URL=https://ia-dev.sercomm.com/apihub APIHUB_TOKEN=<superuser JWT> \\
        python -m scripts.update_eo_summary_description            # show the diff
        python -m scripts.update_eo_summary_description --apply    # write it

Requires a SUPERUSER credential — definition CRUD is superuser-only.
Exit codes: 0 nothing to do / applied, 1 differences found in dry-run, 2 error.
"""

import argparse
import difflib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

DB_ALIAS = "DW"
DEFINITION = "eo_summary"

NEW_DESCRIPTION = (
    "E&O (Excess & Obsolete) inventory SUMMARY. One row per OU + BU_NO + PID + CUSTOMERGROUPNAME "
    "(SUMMARY_ID is unique per row) — NOT per OU + BU + PID, which collapses ~4% of rows. "
    "Adds PID_Ref_Cost_TWD (falls back to PID_Ref_Cost * TWD_CURRENCY_RATE). All-zero rows are "
    "excluded to match the official report. Takes a `view` argument (mc / sales): the two audiences "
    "use DIFFERENT NAMES for the same columns — see Returns."
)

NEW_RESULT_DESCRIPTION = """73 columns, one row per OU + BU_NO + PID + customer group. \
Keys/labels: SUMMARY_ID, Frozen_Date, OU, BU, BU_NO, PID, ProjectName, AliasProjectNAme, CustomerName, \
CUSTOMERGROUPNAME, CUSTOMER_NUMBER, MSIC. Amount families: stock (FG_Intransit_Amt, FG_Hub_Amt, TP70_AMT, \
TP75_Amt, TP80_Amt, FACTORY_STOCK_AMT, WIP_AMT, ALL_OH_AMT, UNAVAILABLE_OH_AMT); commitment (OPO_AMT, \
ALLOCATED_AMT, PLANNED_ALLOCATED_AMT, PLANNED_AMT); excess (EXCESS_{OH,OPO,OHOPO}_AMT_{26W,52W}); \
obsolete (OBSOLETE_{OH,OPO,OHOPO}_AMT plus _PLUSAMT / _ALL_AMT / _TP70 / _TP75 variants). 25 columns \
carry a _TWD suffix, converted from CURRENCY_CODE via TWD_CURRENCY_RATE; the rest are local currency. \
NO _TWD twin exists for PLANNED_ALLOCATED_AMT, TP75_Amt, UNAVAILABLE_OH_AMT, ALL_OH_AMT, WIP_AMT — do not \
assume one. Also PID_Ref_Cost, PID_Ref_Cost_F and the derived PID_Ref_Cost_TWD, LOCK_QTY, LAST_UPDATE_DATE.

TWO AUDIENCE VIEWS, selected by the `view` argument. The same row is published to MC and to Sales UNDER \
DIFFERENT COLUMN NAMES, and a user quotes whichever one they were shown — resolve the name before answering.

view='mc' (default) — the full "MC E&O Summary by Site" report, i.e. every column above.
view='sales' — the narrower set in the "E&O Summary - Sales" workbook.

RENAMES — same column, two names (Sales renames "Obsolete" to "E&O" throughout):
  OBSOLETE_OH_ALL_AMT_TWD     MC "Obsolete All OH Amt"       = Sales "E&O OH (All) Amt"
  OBSOLETE_OPO_AMT_TWD        MC "Obsolete OPO Amt"          = Sales "E&O OPO Amt"
  OBSOLETE_OHOPO_ALL_AMT_TWD  MC "Obsolete All OH+OPO Amt"   = Sales "E&O OH + OPO Amt"
  FG_Intransit_Amt_TWD        MC "FG on the way Amt"         = Sales "FG In Transit Amt"
  FG_Hub_Amt_TWD              MC "FG Hub Stock Amt"          = Sales "Hub OH Amt"
  FACTORY_STOCK_AMT_TWD       MC "Available OH Amt (exclude TP75 & TP80 / TP70 & TP82)" \
= Sales "Non-TP70 & Non-TP75 OH Amt"
  ACTION_PLAN_DUE_DATE        MC "Due Date for Sales' Action" = Sales "Sales AP Due Date"
  ALLOCATED_AMT_TWD + PLANNED_AMT_TWD  MC shows "Allocated Amt (C)" and "(E)" separately; Sales shows \
only the combined "Allocated Amt".

MC ONLY — absent from view='sales': the loss evaluation (OH_unrecoverable, OPO_unrecoverable, the two \
EST_Loss columns, the two Sales'-Reply columns, Cust_Liable_Amt); the unrecoverable block \
(UNRECOVERABLE_AMT, BUYDOWN_AMT, RESERVED_AMT, Balance); all six EXCESS_* columns; the _TP70 / _TP75 / \
_PLUSAMT / Non_TP70&TP75 obsolete breakdown; TP80_Amt, ALL_OH_AMT, UNAVAILABLE_OH_AMT, Total INV Amt; \
LOCK_QTY; MSIC; TARGET_AMT (Meeting Goals); and the identity columns SUMMARY_ID, CustomerName, \
CUSTOMER_NUMBER, LAST_UPDATE_DATE. Sales DOES get CURRENCY_CODE and TWD_CURRENCY_RATE, because TP75_Amt and \
WIP_AMT have no _TWD twin and are unreadable without them.

The response reports the `view` actually applied and lists `masked_columns`. `view_downgraded: true` \
means the CALLER'S OWN PERMISSION (eo:read:scoped) narrowed the request — those columns are restricted \
for that caller, NOT missing from the data. Never report them as absent or zero.

BUYDOWN_AMT and RESERVED_AMT are BU-level values repeated on every customer-group row; summing them \
across rows overstates RESERVED by ~10.6x. Take one value per BU_NO."""


def _headers() -> dict:
    token = os.environ.get("APIHUB_TOKEN")
    api_key = os.environ.get("APIHUB_API_KEY")
    if token:
        return {"Authorization": f"Bearer {token}"}
    if api_key:
        return {"X-API-Key": api_key}
    print("Set APIHUB_TOKEN (superuser JWT) or APIHUB_API_KEY", file=sys.stderr)
    raise SystemExit(2)


def _diff(label: str, old: str, new: str) -> bool:
    old, new = (old or "").strip(), new.strip()
    if old == new:
        print(f"{label}: already up to date")
        return False
    print(f"\n--- {label} ---")
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile="current", tofile="proposed", lineterm=""
    ):
        print(line)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the change (default: dry run)")
    args = parser.parse_args()

    base = os.environ.get("APIHUB_URL")
    if not base:
        print("Set APIHUB_URL", file=sys.stderr)
        return 2

    with httpx.Client(base_url=base.rstrip("/"), headers=_headers(), timeout=30.0) as client:
        try:
            resp = client.get(f"/api/v1/{DB_ALIAS}/queries/definitions/{DEFINITION}")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"Could not read the definition: {e}", file=sys.stderr)
            return 2
        current = resp.json()

        print(f"Read {DEFINITION} from {base} (db_alias={DB_ALIAS})\n")
        changed = _diff("result_description", current.get("result_description", ""), NEW_RESULT_DESCRIPTION)

        payload: dict = {}
        if changed:
            payload["result_description"] = NEW_RESULT_DESCRIPTION

        if _diff("description", current.get("description", ""), NEW_DESCRIPTION):
            payload["description"] = NEW_DESCRIPTION

        if not payload:
            print("\nNothing to change.")
            return 0

        if not args.apply:
            print(f"\nDry run — would update: {', '.join(sorted(payload))}. Re-run with --apply.")
            return 1

        try:
            put = client.put(f"/api/v1/{DB_ALIAS}/queries/definitions/{DEFINITION}", json=payload)
            put.raise_for_status()
        except httpx.HTTPError as e:
            print(f"Update failed: {e}", file=sys.stderr)
            return 2

        print(f"\nApplied: {', '.join(sorted(payload))}.")
        print("The SQL registry reloads on update; MCP clients pick the new text up on their next")
        print("tools/list (within MCP_WATCH_INTERVAL_SECONDS on stdio, or on reconnect over HTTP).")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
