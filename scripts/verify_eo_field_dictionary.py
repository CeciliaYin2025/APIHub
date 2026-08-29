"""Check the E&O field dictionary against the columns the warehouse really returns.

`app/services/eo_field_views.py` was transcribed from the report definitions
(`EO_Summary_v4.0.cpt` and the Sales workbook), not generated from the schema.
That matters because the Sales view is an **allowlist**: a db_field spelled
wrongly there does not raise — the column simply never reaches a Sales caller,
and nobody notices until someone asks where a number went.

This runs `eo_summary` for one row and reports both directions:

- **missing** — a db_field the dictionary claims exists but the query did not
  return. If it is in the Sales view, Sales callers are silently losing it.
- **unmapped** — a column the query returned that the dictionary does not
  mention. Harmless for MC (which passes everything through) but it means the
  column is invisible to Sales and unresolvable by display name.

Some "missing" entries are expected and are listed in EXPECTED_ABSENT: the
report-side formulas (`Total INV Amt`) and the columns joined in by the report
rather than the base table (feedback, action plans, the unrecoverable block).
They are in the dictionary so a user quoting the label can still resolve it.

Usage:
    APIHUB_URL=https://ia-dev.sercomm.com/apihub APIHUB_API_KEY=ahk_... \\
        python -m scripts.verify_eo_field_dictionary [--frozen-date YYYY-MM-DD]

Exit codes: 0 clean, 1 discrepancies found, 2 usage/connection error.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.services import eo_field_views as v  # noqa: E402

# Dictionary entries that are legitimately not columns of the eo_summary result:
# report-side formulas, and figures the .cpt joins from other sources.
EXPECTED_ABSENT = {
    "TOTAL_INV_AMT",  # report formula
    "BALANCE_AMT",  # report formula over the unrecoverable block
    "FEEDBACK",  # INV_EO_PID_Summary_Feedback_R
    "ITEM_NO",  # ds_sales_reply
    "ITEM_OBSOLETE_OHOPO_AMT",  # ds_sales_reply
    "ACTION_PLAN_OH",  # INV_EO_PID_DETAIL_Feedback_R
    "ACTION_PLAN_OPO",
    "ACTION_PLAN_DUE_DATE",
    "SALES_OBSOLETE_OPO_AMT",  # ufn_GetMCUnrecoverableBase
    "OPO_UNRECOVERABLE",
    "OBSOLETE_OPO_EST_LOSS_AMT",
    "SALES_OBSOLETE_OH_ALL_AMT",
    "OH_UNRECOVERABLE",
    "OBSOLETE_ALL_OH_EST_LOSS_AMT",
    "CUST_LIABLE_AMT",  # INV_DOS_SUMMARY_EDIT_V
    "TARGET_AMT",  # Feedback_R type='SALES'
    "UNRECOVERABLE_AMT",  # INV_EO_UNRECOVERABLE_V
    "BUYDOWN_AMT",
    "RESERVED_AMT",
}


def _auth_headers() -> dict:
    api_key = os.environ.get("APIHUB_API_KEY")
    token = os.environ.get("APIHUB_TOKEN")
    if api_key:
        return {"X-API-Key": api_key}
    if token:
        return {"Authorization": f"Bearer {token}"}
    print("Set APIHUB_API_KEY or APIHUB_TOKEN", file=sys.stderr)
    raise SystemExit(2)


def _live_columns(base: str, headers: dict, frozen_date: str | None) -> list[str]:
    with httpx.Client(base_url=base.rstrip("/"), headers=headers, timeout=60.0) as client:
        if not frozen_date:
            resp = client.post(
                "/api/v1/DW/queries/execute",
                json={"query_name": "eo_max_frozen_date", "params": {}, "fetch_mode": "one"},
            )
            resp.raise_for_status()
            frozen_date = str(resp.json()["data"][0]["MAX_FROZEN_DATE"])[:10]
            print(f"Using latest frozen date: {frozen_date}")

        # One row is enough: `columns` describes the result, not the page.
        resp = client.post(
            "/api/v1/DW/queries/execute",
            json={
                "query_name": "eo_summary",
                "params": {"frozen_date": frozen_date},
                "fetch_mode": "page",
                "page": 1,
                "page_size": 1,
                "view": "mc",
            },
        )
        resp.raise_for_status()
        return resp.json()["columns"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-date", help="Period to sample. Defaults to the latest.")
    args = parser.parse_args()

    base = os.environ.get("APIHUB_URL")
    if not base:
        print("Set APIHUB_URL", file=sys.stderr)
        return 2

    try:
        live = _live_columns(base, _auth_headers(), args.frozen_date)
    except httpx.HTTPError as e:
        print(f"Could not reach the API: {e}", file=sys.stderr)
        return 2

    live_upper = {c.upper(): c for c in live}
    mapped = {s.db_field.upper(): s for s in v.FIELD_DICTIONARY}

    missing = sorted(set(mapped) - set(live_upper) - EXPECTED_ABSENT)
    unmapped = sorted(set(live_upper) - set(mapped))

    print(f"\neo_summary returned {len(live)} columns; dictionary maps {len(mapped)}.\n")

    if missing:
        print(f"MISSING — mapped but not returned ({len(missing)}):")
        for name in missing:
            spec = mapped[name]
            flag = "  <-- IN THE SALES VIEW, silently dropped" if not spec.mc_only else ""
            print(f"  {spec.db_field:<34} {spec.mc_label}{flag}")
        print()

    if unmapped:
        print(f"UNMAPPED — returned but not in the dictionary ({len(unmapped)}):")
        print("  (invisible to the Sales view and unresolvable by display name)")
        for name in unmapped:
            print(f"  {live_upper[name]}")
        print()

    if not missing and not unmapped:
        print("Clean: every mapped field exists and every returned column is mapped.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
