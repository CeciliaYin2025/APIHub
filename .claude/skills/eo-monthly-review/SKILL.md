---
name: eo-monthly-review
description: Run the monthly E&O review — trend movement, red-flag items added and removed, and attribution quality — and produce a written summary. Use when asked for a monthly or period E&O review, an E&O status report, "what changed this month", or a management summary of excess and obsolete inventory.
---

# E&O Monthly Review

Produce a period review: what moved, which items became critical, and how much of
the number cannot be trusted to its owner.

**Scope (site / BU / period, if given):** $ARGUMENTS

Load [[eo-data-dictionary]] when a column's meaning or safety is unclear;
[[eo-analysis]] carries the general reading rules.

## Step 0 — fix the periods first

```
eo_frozen_dates(periods=0)
```

Pick two: the period under review, and the one to compare against. State both in
the output. Everything downstream is meaningless without them.

Snapshots are **weekly**, so "this month" is 3–5 periods, not one. Two sensible
readings — say which you used:

- **Month-end to month-end** — `eo_trends` already does this, taking the last
  frozen date of each month
- **Latest vs previous week** — the freshest movement, but a week is not a month

If the current month is incomplete its representative period may be only days
after the previous month's. Check `FROZEN_DATE` against `PREV_FROZEN_DATE` in the
trends output before calling any percentage a monthly move.

## Step 1 — trend

```
eo_trends(group_by="TOTAL", months=6)          # the headline series
eo_trends(group_by="OU", months=6)             # where it moved
```

Report `OBSOLETE_OHOPO_AMT_TWD` with `MOM_OBSOLETE_DELTA_TWD` and
`MOM_OBSOLETE_PCT`. Name the sites driving the change rather than only the total.

Drill only where the total moved: `group_by="BU"` with an `ou` filter. Skip
`group_by="PID"` unless investigating something specific — 753 PIDs per period
does not belong in a review.

Excess and obsolete move for different reasons; if `EXCESS_OHOPO_AMT_52W_TWD`
diverges from the obsolete line, say so.

## Step 2 — red-flag items

```
eo_red_items(frozen_date=<current>, compare_date=<previous>, status="ADDED", ou=<site>)
eo_red_items(frozen_date=<current>, compare_date=<previous>, status="REMOVED", ou=<site>)
```

**Pass `ou`.** Without it a two-period diff scans every site (~14s versus ~2s).
Loop over sites rather than running it unfiltered.

`ADDED` is the actionable list — items that became top-5 unrecoverable-obsolete
within their Site+PID this period. Report the largest by
`CUR_UNRECOV_OBSOLETE_TWD` with `BU_NO`, `PID`, `Item_No` and the amount.

`REMOVED` needs care: a row leaving the list is not necessarily stock leaving the
building. It may have been consumed, or another item may simply have outranked it
in the same Site+PID. `CUR_UNRECOV_OBSOLETE_TWD` is 0 on those rows by
construction — do not read that as "written down to zero".

Counts alone are noise (~2,462 flagged rows per period). Lead with amount.

## Step 3 — attribution quality

```
eo_data_quality(frozen_date=<current>)
```

~29 rows, all sites, one call. Report **`PCT_OF_SITE_OBSOLETE_AMT`**, not
`PCT_OF_SITE_ROWS` — they diverge hard because unattributed items skew to high
value, and the row share makes a serious problem look minor.

The `ISSUE` rows overlap (a row missing both PID and customer counts under both),
so they do not sum to `ALL`. Never present them as a breakdown that adds up.

Compare against the previous period by calling it twice — quality trending the
wrong way while the total falls is a finding worth stating on its own.

## Step 4 — what the owners said

Only when step 2 surfaced something worth chasing:

```
eo_feedback(manual_only="Y", ou=<site>)        # omit frozen_date: its periods differ
```

~11% of rows carry a free-text note, and those are the ones with content. Quote
the note next to the item rather than paraphrasing a code.

Feedback periods are **monthly and do not align** with the weekly snapshots —
passing a weekly date returns zero rows. Omit `frozen_date` for its own latest.

## Reporting

Structure:

1. **Headline** — total obsolete for the period, the move versus comparison, both
   dates named
2. **Where it moved** — sites and BUs driving it, with amounts
3. **New critical items** — top `ADDED` red items by amount, with owner feedback
   where it exists
4. **Attribution quality** — amount share unattributed, per site, and whether it
   is improving
5. **What needs a decision** — items with no action plan, or a plan long past
   `ACTION_PLAN_DUE_DATE`

Rules that keep the report honest:

- Every figure carries its frozen period. A number without one is unusable
- Amounts are TWD **except** the columns with no `_TWD` twin — see
  [[eo-data-dictionary]] before mixing them into a total
- Say what you did not check. A review over one site is not a company review
- If a figure came from `scripts/eo_cache.py`, say which period and when it was
  cached
