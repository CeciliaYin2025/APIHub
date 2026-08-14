---
name: eo-business-rules
description: Why E&O attribution changes — the logic that assigns an item to a BU/PID/customer, known rule changes and their effective dates, and the observed failure patterns behind Issue List disputes. Load when explaining a change point found by eo_item_history, or judging whether an attribution move was system logic or manual intervention.
---

# E&O Attribution Rules

Why an item is booked where it is, and why that changes between periods.
[[eo-issue-investigation]] is the workflow; this is the reasoning it draws on.

**Case in hand:** $ARGUMENTS

> **This document is incomplete.** The rule history below is what is currently
> written down and verified. The root-cause classification distilled from closed
> Issue List cases is **not yet captured here** — see "Missing" at the end.
> Until it is, classify only against the rules stated below and say plainly when
> a case matches none of them. Do not invent a category: this feeds replies sent
> to other departments.

## How attribution works

An E&O row carries three attribution fields, assigned independently and able to
change independently:

| Field | What it identifies |
|---|---|
| `BU_NO` | The division that owns the exposure |
| `PID` | The project the material was bought for |
| `Customer_Number` / `CUSTOMERGROUPNAME` | The end customer behind that project |

They move independently, which is why `eo_item_history` reports `CHANGED_FIELDS`
per field rather than a single "reattributed" flag. A BU change with the PID
unchanged, and a PID change with the BU unchanged, have different causes.

## Known rule changes

Each of these changes attribution for periods after its effective date. When a
change point lands on one of these dates, the rule is the likely cause — but
confirm against the item's own evidence before saying so.

| Effective | Change |
|---|---|
| **2025-08-07** | Attribution basis moved from **Using Assembly** to **Top Item**. Items previously attributed via the assembly that consumed them are attributed to the top-level item instead. Expect BU/PID moves clustered on the first period after this date |

> Further rule changes belong here as they are made, with their effective date
> and the direction of the resulting move. A change without a dated entry here
> is indistinguishable from a data error when someone reads the history months
> later.

## Known failure patterns

Observed behaviours that produce disputed attribution. These are causes, not
categories — the classification table is still missing.

### Manual BU correction drops the customer

When a TBC or misassigned `BU_NO` is corrected by hand, the customer fields are
frequently not updated with it, leaving the row attributed to a BU with no
customer.

**Verified in the data** (2026-08-01): `CUSTOMER_MISSING` and
`CUSTOMERGROUP_MISSING` track each other almost exactly across every site — SN
4,601 vs 4,847 rows, SP 7,240 vs 7,251, SC 4,182 vs 4,638. The same records are
missing both, which is what a dropped customer looks like rather than two
independent gaps.

Signature in `eo_item_history`: `CHANGED_FIELDS` shows `BU_NO` while
`CUSTOMER_NUMBER` goes blank in the same period.

### Orders entered but not written back

Order status `ENTERED` does not push back against the E&O position, so material
can show as excess or obsolete while an order that would consume it exists.

Signature: the amount falls sharply in a later period with no attribution change
and no action plan — the order landed rather than anything being disposed of.

### Excess material parked under TBC

Material with no clear owner is parked with `BU_NO = 'TBC'` pending a decision.
`TBC` appears **only** in `BU_NO` — never in PID or customer — so a blank PID and
a `TBC` BU are separate conditions with separate causes.

**Verified**: 1,702 rows carry `BU_NO = 'TBC'` (1.2% of rows) but they concentrate
value — SP alone holds 1,462 of them at 313.8M TWD, 3.6% of that site's obsolete
amount.

## Reading the value share, not the row share

Unattributed material skews heavily to high value, so row counts understate the
problem by roughly an order of magnitude. On 2026-08-01, SN was **6.5% of rows**
missing a PID but **54.9% of its obsolete amount**; SP 8.3% of rows and 32.1% of
amount.

Any statement about how much E&O is unattributed must quote the amount share.
`eo_data_quality` returns both — use `PCT_OF_SITE_OBSOLETE_AMT`.

## Evidence ranking

When attributing a cause, prefer in this order:

1. **A written instruction** — a note in `eo_feedback` or `eo_feedback_summary`
   naming a transfer or submission document, or a named person's instruction.
   Strongest, and the only kind that settles a dispute
2. **A rule change on the matching date** — a change point landing on a dated
   entry above
3. **A signature match** — the change's shape matches a known failure pattern
4. **Timing alone** — a hypothesis. Label it as one

Nothing above the line? Say the cause is undetermined and name what would settle
it. An open question is recoverable; a wrong cause in a cross-department reply is
not.

## Missing

To be supplied by MC / the Issue List owner:

- **The root-cause classification** used when closing Issue List cases — the
  category names, what distinguishes each, and what evidence closes one. Without
  it, investigations produce a narrative but no consistent classification
- **Further rule-change dates** beyond 2025-08-07
- **The definition of "balance"** in the unrecoverable report. No source column
  provides it and the obvious arithmetic does not hold (`UNREC − BUYDOWN −
  RESERVED` is negative on 153 of 354 rows), so `eo_unrecoverable` deliberately
  derives nothing

Related: [[eo-data-dictionary]] for field semantics, [[eo-analysis]] for the
general reading rules.
