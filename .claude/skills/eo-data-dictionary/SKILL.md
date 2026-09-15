---
name: eo-data-dictionary
description: Field-level reference for Sercomm E&O data — what each amount family means, which columns are safe to sum, the code taxonomies (INDICATOR, ACTION_PLAN, TBC), the real grain of each source, and the period cadences. Load when interpreting E&O column names, deciding how to aggregate, or checking whether a figure means what it appears to mean.
---

# E&O Data Dictionary

Reference for the columns behind the `query_dw_eo_*` tools. Every fact here was
measured against the live warehouse rather than read off a schema — the source
column names are frequently misleading, and several long-standing descriptions
turned out to be wrong.

**Looking up:** $ARGUMENTS

Measurements are from frozen period **2026-08-01** unless stated. Counts drift;
the shapes and relationships do not.

## Periods

Two different cadences. Confusing them silently returns zero rows.

| Source | Cadence | Count | Range |
|---|---|---|---|
| `INV_EO_PID_SUMMARY_F` (summary, detail, trends, quality, red items) | **Weekly**, usually Thursday, plus an extra at month-end | 71 | 2025-01-02 → 2026-08-01 |
| `INV_EO_UNRECOVERABLE_V` | Weekly, wider history | 86 | 2024-03-04 → 2026-08-01 |
| `INV_EO_PID_DETAIL_Feedback_R` (feedback) | **Roughly monthly**, does not align with the above | 15 | 2025-06-26 → 2026-08-01 |

`2025-10-31` exists in feedback but has **no matching E&O snapshot**. Documentation
calling the frozen date a "monthly close basis" is wrong; 52 of 70 gaps are 7 days.

A `frozen_date` that is not an actual frozen date returns **zero rows, not an
error**. Resolve with `eo_frozen_dates` first.

## Grain — what one row means

| Source | Grain | Verified |
|---|---|---|
| `INV_EO_PID_SUMMARY_F` | OU + BU_NO + PID + CUSTOMERGROUPNAME | 1,774 rows = 1,774 distinct combos = 1,774 distinct `SUMMARY_ID` |
| Detail TVF | **No unique key exists** | 138,986 rows vs 138,600 distinct on a nine-column key — ~390 genuine duplicates |
| `INV_EO_UNRECOVERABLE_V` | BU_NO + CUSTOMERGROUPNAME (`OU` is always the literal `'ALL'`) | 354 rows = 354 combos |
| `INV_EO_PID_DETAIL_Feedback_R` | SITE + BU_NO + PID + ITEM_NO + CUSTOMER_NUMBER, **not unique** | 51,921 rows vs 51,842 combos — 79 duplicates |

Grouping summary by OU + BU + PID (without customer group) collapses to 1,424
combos — it silently merges ~20% of rows. Always SUM; never assume one row per item.

## Two audiences, two names for the same column

The same `INV_EO_PID_SUMMARY_F` row is published twice, and **the two use
different display names**. Getting this wrong is the single most common way to
answer confidently about the wrong column.

- **MC view** — the online report "MC E&O Summary by Site" (`EO_Summary_v4.0.cpt`).
  Internal technical names, including the TP70/TP75 exclusion conditions, and the
  loss-evaluation block.
- **Sales view** — the Excel sent to Sales ("E&O Summary - Sales", columns A–X).
  Fewer columns, plainer names, and the rule that catches everyone:
  **"Obsolete" is renamed "E&O"**.

So a user who says "E&O OH (All) Amt" and a user who says "Obsolete All OH Amt"
are asking about **the same column**, `OBSOLETE_OH_ALL_AMT_TWD`. Always answer
using the db_field.

### The renames (everything else keeps its name)

| db_field | MC label | Sales label |
|---|---|---|
| `OBSOLETE_OH_ALL_AMT_TWD` | Obsolete All OH Amt | **E&O OH (All) Amt** |
| `OBSOLETE_OPO_AMT_TWD` | Obsolete OPO Amt | **E&O OPO Amt** |
| `OBSOLETE_OHOPO_ALL_AMT_TWD` | Obsolete All OH+OPO Amt | **E&O OH + OPO Amt** |
| `FG_Intransit_Amt_TWD` | FG on the way Amt | **FG In Transit Amt** |
| `FG_Hub_Amt_TWD` | FG Hub Stock Amt | **Hub OH Amt** |
| `FACTORY_STOCK_AMT_TWD` | Available OH Amt (exclude TP75 & TP80 / TP70 & TP82) | **Non-TP70 & Non-TP75 OH Amt** |
| `ACTION_PLAN_DUE_DATE` | Due Date for Sales' Action | **Sales AP Due Date** |
| `ALLOCATED_AMT_TWD` + `PLANNED_AMT_TWD` | Allocated Amt **(C)** and **(E)**, plus a total | **Allocated Amt** (total only) |
| `ProjectName` / `AliasProjectNAme` | Project Name, **two columns** | Project Name, **one** |

`FROZEN_DATE` is a data column in MC and a **sheet header** in the Sales
workbook — the API returns it in both views, because a row with no period is
meaningless.

### What Sales does NOT get

The Sales view has no loss evaluation, no excess ageing, and no stock structure
beyond the headline buckets. If a user is reading the Sales workbook they cannot
see any of:

| Group | Columns |
|---|---|
| Loss evaluation | `OH_unrecoverable`, `OPO_unrecoverable`, `Obsolete_All_OH_EST_Loss_Amt`, `Obsolete_OPO_EST_Loss_Amt`, `SALES_OBSOLETE_OH_ALL_AMT`, `SALES_OBSOLETE_OPO_AMT`, `Cust_Liable_Amt` |
| Unrecoverable block | `UNRECOVERABLE_AMT`, `BUYDOWN_AMT`, `RESERVED_AMT`, Balance |
| Excess ageing | the six `EXCESS_{OH,OPO,OHOPO}_AMT_{26W,52W}_TWD` |
| E&O breakdown | the `_TP70` / `_TP75` / `_PLUSAMT` / `Non_TP70 & TP75` variants |
| Stock structure | `TP80_Amt`, `ALL_OH_AMT`, `UNAVAILABLE_OH_AMT`, Total INV Amt |
| Reference | `LOCK_QTY`, `MSIC` |
| Goal | `TARGET_AMT` (Meeting Goals) |

`WIP_AMT` and `TP75_Amt` are in the Sales view and have **no `_TWD` twin** — they
are local currency. Sales therefore also carries `CURRENCY_CODE` and
`TWD_CURRENCY_RATE`, which neither report prints: a workbook is rendered per site
so its currency is implicit, an API response has no such context.

### Asking for a view

`query_dw_eo_summary` takes a **`view`** argument: `mc` (default, everything) or
`sales`. The response reports `view`, and `masked_columns` when anything was
dropped. A caller granted `eo:read:scoped` is served `sales` whatever they ask
for, with `view_downgraded: true` — so if a number a user expects is absent,
check that flag before assuming the data is missing.

The machine-readable mapping is the `eo://data-dictionary` MCP resource, served
from `GET /api/v1/eo/field-dictionary`. Canonical source:
`app/services/eo_field_views.py`.

Columns Y onward in the Sales workbook (`Management Mandate`, `Category`,
`Rax's reply`, `Type`, dated notes like `20260315lc`) are **not report columns** —
they are MC/IT annotations typed into Excel for issue tracking, and exist in no
database.

## Amount families

Both summary and detail carry the same conceptual families. Names differ between
the two sources, so check which tool you are reading.

| Family | Meaning |
|---|---|
| **Stock on hand** | `FG_Intransit`, `FG_Hub`, `TP70`, `TP75`, `TP80`, `FACTORY_STOCK`, `WIP`, `ALL_OH`, `UNAVAILABLE_OH` |
| **Commitment** | `OPO` (open purchase order), `ALLOCATED`, `PLANNED_ALLOCATED`, `PLANNED` |
| **Excess** | `EXCESS_{OH,OPO,OHOPO}_AMT_{26W,52W}` — excess against a 26- or 52-week demand horizon |
| **Obsolete** | `OBSOLETE_{OH,OPO,OHOPO}_AMT`, with `_PLUSAMT`, `_ALL_AMT`, `_TP70`, `_TP75`, `_NON70` variants |
| **Unrecoverable** | `unrecoverable_oh`, `OH_UNRECOVERABLE_AMT`, `unrecoverable_opo`, `OPO_UNRECOVERABLE_AMT` (detail only) |

`OH` = on hand, `OPO` = open PO, `OHOPO` = the two combined. `TP70` / `TP75` /
`TP80` are stock-status buckets, not amounts of their own family.

### Currency — the `_TWD` suffix is not universal

Most amounts have a `_TWD` twin converted from `CURRENCY_CODE` via
`TWD_CURRENCY_RATE`. **Some do not**, and assuming otherwise mixes currencies.

- `INV_EO_PID_SUMMARY_F` — 25 `_TWD` columns. **No twin** for `PLANNED_ALLOCATED_AMT`,
  `TP75_Amt`, `UNAVAILABLE_OH_AMT`, `ALL_OH_AMT`, `WIP_AMT`
- Detail TVF — 21 `_TWD` columns, 26 `*QTY*` columns. **No twin** for `ALLOCATED_AMT`,
  `ALL_OH_AMT`, `UNAVAILABLE_OH_AMT`, `OH_UNRECOVERABLE_AMT`, `OPO_UNRECOVERABLE_AMT`,
  `WIP_AMT`
- **Already TWD despite the name** — `OBSOLETE_OH_TP75_AMT` and `OBSOLETE_OH_ALL_AMT`
  in the detail source have no `_TWD` suffix but hold TWD values. **Do not multiply
  them by `TWD_CURRENCY_RATE`.** Verified 2026-09-15 on the PHP / INR / CNY sites:
  `OBSOLETE_OH_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD + OBSOLETE_OH_TP75_AMT` equals
  `OBSOLETE_OH_ALL_AMT` exactly, and applying the rate to either side breaks it. The
  item-level "E&O OH + OPO Amt" (`OBSOLETE_OHOPO_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD +
  OBSOLETE_OH_TP75_AMT`, what `eo_item_diff` sums) reconciles to the summary's
  `OBSOLETE_OHOPO_ALL_AMT_TWD` to the cent. The `eo_detail` tool text still lists both
  as local currency; this entry supersedes it

Detail also carries `DOS_CURRENCY_RATE` alongside `TWD_CURRENCY_RATE`.

### Columns that must not be summed across rows

`INV_EO_UNRECOVERABLE_V` repeats a **BU-level** figure on every customer-group row
of that BU:

| Column | Constant within BU_NO | Row-SUM | True per-BU total | Inflation |
|---|---|---|---|---|
| `RESERVED_AMT` | 37 / 37 BU groups | 5,820,481,268 | 551,391,353 | **10.6x** |
| `BUYDOWN_AMT` | 31 / 37 | 840,916,048 | 658,602,954 | 1.3x |
| `Unrecoverable_AMT` | 16 / 37 — genuinely per row | 380,787,023 | — | safe |
| `OBSOLETE_OHOPO_ALL_AMT` | 1 / 37 — genuinely per row | — | — | safe |

To total the first two, take one value per `BU_NO` (e.g. `MAX` per BU, then sum).
`RESERVED_AMT` can also be **negative**.

### Verified identities

- `Unrecoverable_AMT = OH_unrecoverable + OPO_unrecoverable` — exact on all 354
  rows. Adding the total to its parts double-counts
- `OBSOLETE_OHOPO_ALL_AMT >= Unrecoverable_AMT` always; gross vs a subset, **not additive**
- **No balance / net column exists** in any source. `OBS_ALL − BD − RES` is negative
  on 122/354 rows, `UNREC − BD − RES` on 153/354. Do not derive one — ask the
  business owner for the definition

## Code taxonomies

### `INDICATOR` (detail, red items)

`'R'` or NULL. Computed by the Hub, not stored: within each **Site + PID**, the
top 5 items by `OBSOLETE_OHOPO_AMT_TWD + OBSOLETE_OH_TP70_AMT_TWD`, counting only
items above **10,000 TWD**. 2,493 flagged rows in a 138,986-row period.

### `ACTION_PLAN_OH` / `ACTION_PLAN_OPO` (feedback)

The owner's chosen disposition. Paired with a free-text `_MANUAL` column holding
the human note (often Chinese); only ~11% of rows carry one.

| Code | Meaning |
|---|---|
| `A` | Get buy-down POs from the customer to cover E&O |
| `B-1` / `B-2` / `B-3` | Can be consumed — new demand / new projects / TC |
| `C` | Transfer to another site for consumption |
| `D` | Ship OH to the customer (with customer PO) |
| `E-1` / `E-2` | Move to TP70 / TP75 |
| `F` | Resell to brokers |
| `G-1` | Scrap OH (covered by customer PO or BU reserve) |
| `G-2` | Waiting for MC to provide scrap evidence |
| `O.Others` | Free text carries the real answer |

Values outside this list occur — a part number has been seen typed into the code
field. Do not assume the set is closed.

### Attribution quality markers

**There are no NULLs.** Missing attribution is an **empty string**, so test with
`LTRIM(RTRIM(x)) = ''`, never `IS NULL`. `TBC` appears **only in `BU_NO`**.

| Field | Blank rows | Share |
|---|---|---|
| `CUSTOMERGROUPNAME` | 19,080 | 13.7% |
| `Customer_Number` | 18,365 | 13.2% |
| `PID` | 9,090 | 6.5% |
| `BU_NO` = `TBC` | 1,702 | 1.2% |
| `BU_NO` blank | 8 | — |

**Row share badly understates the problem** because unattributed items skew to
high value. On the same period, SN has 6.5% of rows missing a PID but **54.9% of
its obsolete amount**; SP is 8.3% of rows and 32.1% of amount. Always quote the
amount share (`PCT_OF_SITE_OBSOLETE_AMT`), not the row share.

`CUSTOMER_MISSING` and `CUSTOMERGROUP_MISSING` track each other almost exactly —
the same records are missing both, consistent with customer being dropped when a
BU is corrected by hand.

## Other flags

| Column | Meaning |
|---|---|
| `NCNR_FLAG` | Non-cancellable / non-returnable — cannot be sent back to the supplier |
| `AGING_FLAG` | Aging bucket marker on the detail row |
| `MSIC` | 2-character classification code |
| `Organization_ID` | Numeric site identifier; `Site` is the readable form |
| `AliasProjectNAme` | Alternate project name (note the source's capitalisation) |

## Dimension cardinality

For sizing a query before running it:

| Dimension | Distinct values | Blank |
|---|---|---|
| OU | 7 — DW, OND, SC, SI, SN, SP, TN1 | 0 |
| BU (`BU_NO`) | 52 | 4 |
| CUSTOMER (`CUSTOMERGROUPNAME`) | 140 | 326 rows |
| PROJECT (`ProjectName`) | 615 | 257 rows |
| PID | 753 | 153 rows |

Resolve real values with `eo_dimensions` — codes are not guessable (`BU_NO` looks
like `S3B`, `PID` like `DYT`) and a wrong value returns nothing rather than an error.

## Source objects

| Object | Columns | Notes |
|---|---|---|
| `INV_EO_PID_SUMMARY_F` | 72 | Base table |
| `ufn_INV_EO_PID_Detail_Unrecoverable(date)` | 88 | TVF, one period per call |
| `INV_EO_PID_Detail_Unrecoverable_V` | 88 | Same columns, **all periods**, no parameter. Slow: no index on `Item_No`, so always bound by `Frozen_Date` |
| `INV_EO_UNRECOVERABLE_V` | 11 | Pre-aggregated across sites |
| `INV_EO_PID_DETAIL_Feedback_R` | 16 | 411,539 rows over 15 periods |
| `INV_EO_PID_Summary_Feedback_R` | 14 | **2 rows in total** — effectively unused |
| `ufn_GetMCUnrecoverableBase(date)` | 14 | TVF, not currently exposed as a tool |

Related: [[eo-analysis]] for tool selection and reporting rules,
[[eo-business-rules]] for why attribution changes happen.
