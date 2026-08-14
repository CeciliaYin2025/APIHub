---
name: eo-issue-investigation
description: Investigate a single E&O item or PID — why it is attributed to a given BU/PID/customer, when that changed, what the owner said, and draft a reply. Use for MC Issue List questions like "why is this item under X", "when did this move to us", "this is not ours", or disputes about E&O ownership.
---

# E&O Issue Investigation

Trace one item's attribution history and produce an evidence-backed answer.

**Item / PID / question:** $ARGUMENTS

Load [[eo-data-dictionary]] for column meanings, [[eo-business-rules]] for the
attribution logic behind a change.

## What you are actually being asked

Most Issue List questions reduce to one of four, and they need different evidence:

| Question | Evidence |
|---|---|
| "Why is this under my BU?" | The change point where `BU_NO` moved, and any note explaining it |
| "When did this become ours?" | The first period it appears under that BU |
| "This is not ours" | Whether attribution moved by system logic or by hand |
| "Why is this obsolete at all?" | Amount history, not attribution history |

Establish which before pulling data — the last one does not need
`eo_item_history` at all.

## Step 1 — locate the item

If you have an exact `Item_No`, skip ahead. Otherwise narrow first:

```
eo_dimensions(dim="PID", bu=<bu>)                  # find the PID
eo_detail(frozen_date=<latest>, pid=<pid>, ou=<site>)
```

Never guess a code. `BU_NO` looks like `S3B`, `PID` like `DYT`, and a wrong value
returns **zero rows rather than an error** — which reads as "no such problem".

## Step 2 — attribution history

```
eo_item_history(item_no=<item>, ou=<site>, periods=12)
```

**Always pass `ou`.** It is the difference between ~3s and ~35s; the underlying
view has no index on `Item_No`.

Read `IS_CHANGE_POINT` and `CHANGED_FIELDS` newest-first. The distinction in
`CHANGED_FIELDS` matters and is easy to misreport:

| Label | Means |
|---|---|
| `BU_NO` / `PID` / `CUSTOMER` | The **value** changed — a genuine re-attribution |
| `BU_NO_N` / `PID_N` / `CUSTOMER_N` | Only the **number of distinct values** changed |

A count change is not a re-attribution. Saying "the PID changed" when `PID_N` went
2→3 and the value never moved sends the owner looking for something that did not
happen.

`PREV_BU_NO` / `PREV_PID` / `PREV_CUSTOMER_NUMBER` give the before-values, so a
change point reads directly as `was → is`.

When `*_N > 1` the displayed value is the `MIN` of several — the item sits under
more than one BU/PID/customer in that period. Say so rather than presenting one.

Widen with `periods=0` only if no change point appears in the default window.

## Step 3 — what the owner recorded

```
eo_feedback(item_no=<item>, manual_only="Y")       # omit frozen_date
eo_feedback(item_no=<item>)                        # if the above is empty
eo_feedback_summary(pid=<pid>)                     # PID-level MC notes
```

Omit `frozen_date`: feedback periods are **monthly** and do not align with the
weekly snapshots, so a snapshot date returns nothing.

The free-text `_MANUAL` columns carry the real explanation; the coded
`ACTION_PLAN_OH` / `_OPO` values only say what was planned. Quote the note.

`eo_feedback_summary` holds **2 rows in total** — an empty result means nobody
recorded a PID-level note, never that there is no issue. When it does hit, the
content is exactly what this step wants: transfer document numbers and named
instructions to change a BU.

## Step 4 — classify the cause

Match the change point against the known attribution rules in
[[eo-business-rules]]. Anchor on evidence, in this order:

1. A free-text note or document number naming the change — strongest
2. A change point whose shape matches a known rule (e.g. customer dropping to
   blank in the same period a BU was corrected by hand)
3. Timing alone — weakest. State it as a hypothesis, not a cause

If nothing matches, say the cause is undetermined and name what would settle it.
An invented cause is worse than an open question here: it goes into a reply to
another department.

## Step 5 — draft the reply

Structure:

- **Item and scope** — `Item_No`, `Site`, current `BU_NO` / `PID` / customer, and
  the period you read
- **What changed and when** — each change point as `date: was → is`, with the
  `CHANGED_FIELDS` labels read correctly
- **Evidence** — the note, document number or instruction, quoted
- **Assessment** — cause if established, or what is still open
- **Ask** — the specific decision or confirmation needed

Keep it factual. The reply crosses departments; a hedge is cheaper than a
retraction.

## Traps specific to this workflow

- **Zero rows is not an answer.** A non-existent frozen date, a mistyped
  `Item_No`, or a wrong `ou` all return empty. Confirm the item exists in the
  latest period before concluding anything about its history
- **The detail source has no unique key** — ~390 duplicate rows per period.
  `eo_item_history` aggregates per period for you, but raw `eo_detail` does not
- **`REMOVED` from the red list is not disposal.** Another item may simply have
  outranked it within the same Site+PID
- **Amounts:** several columns have no `_TWD` twin and are in local currency. Do
  not add them into a TWD total — see [[eo-data-dictionary]]
