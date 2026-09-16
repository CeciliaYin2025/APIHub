---
name: eo-analysis
description: Query and analyse Sercomm E&O (Excess & Obsolete) inventory data through the API Hub MCP tools, with local caching of closed frozen periods. Use for any question about E&O amounts, obsolete/excess stock, red-flag items, item attribution (why an item sits under a BU/PID/customer), Action Plan feedback, or E&O trends.
---

# E&O Analysis

Answer E&O questions using the thirteen `query_dw_eo_*` MCP tools, reusing locally
cached results for closed periods instead of re-running slow queries.

**Question / task:** $ARGUMENTS

## Before anything else: pin the period

E&O snapshots are **weekly** (usually Thursday), plus an extra one at month-end.
They are *not* monthly, and older documentation that says "monthly close basis"
is wrong.

- `query_dw_eo_max_frozen_date` — the single latest date.
- `query_dw_eo_frozen_dates` — the list, newest first; defaults to the last 3
  periods (= last 3 weeks), `periods=0` for all 71.

Never invent a date. A date that is not an actual frozen date returns **zero
rows, not an error** — which reads exactly like "no E&O", and is the single
easiest way to produce a confidently wrong answer.

`eo_feedback` is the exception: its periods are roughly **monthly** and do not
line up with the weekly snapshot dates. Omit `frozen_date` there to get its own
latest period.

## Tool map

| Question | Tool |
|---|---|
| Which periods exist? | `eo_frozen_dates`, `eo_max_frozen_date` |
| What are the valid OU / BU / PID / customer / project codes? | `eo_dimensions` |
| How much E&O by OU+BU+PID+customer group? | `eo_summary` |
| Item-level detail for a period | `eo_detail` |
| Which items are red-flagged, and what changed vs another period? | `eo_red_items` |
| Which items' E&O amount moved most between two periods (for a customer / BU / PID / site)? | `eo_item_diff` |
| Why is this item attributed to X? When did it move? | `eo_item_history` |
| How is E&O trending month to month? | `eo_trends` |
| How much E&O has no PID / no customer / a TBC BU? | `eo_data_quality` |
| Unrecoverable with buy-down and reserve | `eo_unrecoverable` |
| What did the owner say about this item? | `eo_feedback` |
| PID-level MC highlight notes | `eo_feedback_summary` |

Always resolve codes with `eo_dimensions` before filtering. `BU_NO` looks like
`S3B`, `PID` like `DYT` — a guessed value silently returns nothing.

**Rank with `order_by`, never with a contrived filter.** Every tool takes an
`order_by` argument: `["-OBSOLETE_OHOPO_AMT_TWD"]` sorts largest-first in the
database, so page 1 with `page_size=5` *is* the top five. Sorting happens over the
whole period, not over the page you fetched.

Do not approximate a ranking by bending another parameter. Setting
`eo_item_diff`'s `compare_date` to a far-away period so the delta resembles the
absolute amount looks clever and gets the amounts right, but that tool reports
`BU_NO`, `PID` and `CUSTOMERGROUPNAME` as the MIN **across both periods** — so the
attribution you report belongs to the older period, and it is wrong without
looking wrong. (Seen on 2026-09-16: two of five rows carried the wrong BU and PID.)

**Ask for the columns you will read.** Every tool takes a `columns` argument;
without it a row arrives with all its columns — 89 of them on `eo_detail`, about
2.6 KB per row, so one page of 100 rows is ~258 KB. Naming the five or six
columns an answer needs costs a fraction of that and leaves room to keep working.
Take the names from the tool's `Returns:` text; an unknown name is rejected with
400 and the available columns are listed, so a typo costs one call, not a wrong
answer.

**Never page through a tool to search for something.** Every page you fetch stays
in the context for the rest of the question, and a handful of 100-row pages of
`eo_dimensions` or `eo_detail` is enough to overflow it — the run then fails with
no answer at all. Fetch at most two pages of any one tool per question. If what
you need is not there, narrow the filter instead: to find a customer's items, look
the customer group up with `eo_dimensions` `dim='CUSTOMER'` (sorted by amount, so it
is on page 1) and pass it as a filter. For "which items changed most between two
periods", use `eo_item_diff`: it ranks server-side, so page 1 *is* the answer. If a
question genuinely needs thousands of rows, say so and hand over the
`scripts/extract_query.py` command rather than walking pages.

## Caching closed periods

Frozen periods are immutable once closed, so re-querying one can only return the
same rows. The slow tools (`eo_item_history` ~35s, `eo_red_items` ~14s across all
sites) are worth caching; use `scripts/eo_cache.py` rather than repeating them.

```bash
export APIHUB_URL=https://ia-dev.sercomm.com/apihub
export APIHUB_API_KEY=ahk_...            # or APIHUB_TOKEN=<JWT>

python -m scripts.eo_cache fetch --query eo_red_items \
    -p frozen_date=2026-06-26 -p compare_date=2026-05-29 -p ou=SP
python -m scripts.eo_cache status
python -m scripts.eo_cache clear --query eo_red_items
python -m scripts.eo_cache clear --all
```

**The latest period is never cached**, and the script enforces that itself — it
checks `eo_max_frozen_date` on every run and refuses to store a result whose
newest date parameter is not strictly older. Requests with no date parameter
(`eo_trends`, `eo_item_history`, a bare `eo_dimensions`) always sweep the latest
period and are always fetched live. Do not try to work around this; the latest
period is re-run and corrected upstream.

When to reach for the cache versus the MCP tool directly:

- **MCP tool** — the latest period, small results, anything interactive.
- **`eo_cache.py`** — a closed period you will query more than once, or a
  multi-step analysis that revisits the same period.
- **`scripts/extract_query.py`** — anything above ~50k rows. `eo_detail` for one
  period is ~139k rows and belongs in a file, never in a cache entry or a context
  window. The cache script refuses these and says so.

Force a refetch with `--refresh` for one entry, or `clear` to drop entries. Do
this whenever the user says the data looks stale, or after upstream reports a
period was re-run.

## Reading the data correctly

These are the traps that produce plausible, wrong answers. Each is documented in
the tool's own `Returns:` text — read it rather than assuming.

- **`eo_summary` serves two audiences under different names.** It takes a `view`
  argument: `mc` (default, the full report) or `sales` (the narrower set sent to
  Sales). The two rename the same columns — MC "Obsolete All OH Amt" is Sales
  "E&O OH (All) Amt" — so when a user quotes a column name, resolve it through
  the `eo-data-dictionary` skill before answering. The response reports which
  `view` was applied and lists `masked_columns`; `view_downgraded: true` means the
  caller's permissions narrowed it, not that data is missing.
- **`eo_summary` grain is OU + BU_NO + PID + CUSTOMERGROUPNAME.** Grouping by
  OU+BU+PID alone collapses ~4% of rows and double-counts amounts.
- **`eo_detail` has no unique key.** ~390 of 139k rows repeat even on a nine-column
  key. Aggregate with SUM; never assume one row per item.
- **`eo_unrecoverable`: `BUYDOWN_AMT` and `RESERVED_AMT` are BU-level values
  repeated on every customer-group row.** Summing them across rows overstates
  RESERVED by ~10.6x. Take one value per `BU_NO`. There is **no balance column**
  and the obvious arithmetic does not hold — do not derive one.
- **`eo_data_quality`: read `PCT_OF_SITE_OBSOLETE_AMT`, not `PCT_OF_SITE_ROWS`.**
  They diverge hard because unattributed items skew to high value — SN is 6.5% of
  rows but 54.9% of obsolete amount. Its ISSUE rows overlap and do not sum to `ALL`.
- **`eo_trends`: the newest month may be a few days after the previous one.**
  Month-end picks are compared pairwise, and the current month is usually
  incomplete. Check `FROZEN_DATE` against `PREV_FROZEN_DATE` before quoting a
  month-on-month percentage.
- **`eo_feedback_summary` is almost empty** (2 rows in total). An empty result
  means "nobody recorded anything here", never "no issue".
- **Missing attribution is an empty string, not NULL**, and `TBC` appears only in
  `BU_NO`.

## Investigating an attribution issue

The common MC question is "why is this item sitting under X?".

1. `eo_item_history` with `item_no` and **`ou`** — pass `ou`, it is the
   difference between ~3s and ~35s. Read `IS_CHANGE_POINT` and `CHANGED_FIELDS`.
2. In `CHANGED_FIELDS`, `BU_NO` / `PID` / `CUSTOMER` mean the **value** changed;
   `BU_NO_N` / `PID_N` / `CUSTOMER_N` mean only the **number of distinct values**
   changed. They are different findings — do not report a count change as a
   re-attribution.
3. `eo_feedback` with the same `item_no` and `manual_only='Y'` for what the owner
   wrote about it.
4. `eo_feedback_summary` for any PID-level MC note citing a transfer document or
   an instruction to change BU.

## Reporting

State the frozen period in every answer — a number without its period is
meaningless here. Amounts are TWD unless the column name says otherwise; several
columns have no `_TWD` twin and are in local currency, which each tool's
`Returns:` text lists explicitly. If a figure came from cache, say which period
and when it was cached.
