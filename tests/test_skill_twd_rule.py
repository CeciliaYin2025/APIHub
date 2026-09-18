"""Pin what the 2026-09-17 Charter incident actually taught us.

It was recorded at the time as a currency bug: five summary columns have no
`_TWD` twin, so summing one across sites was said to add pesos to yuan. Measured
on 2026-09-18, that premise is false. Every amount column in these sources is
already TWD:

- All 24 base/`_TWD` pairs in `INV_EO_PID_SUMMARY_F` are equal on rows whose
  `CURRENCY_CODE` is not TWD, and a full-table scan for a row where they differ
  returns zero rows, in every period sampled from 2025-06 to 2026-09.
- 586 items stocked at both a TWD site and a CNY site show a `Unit_Price` ratio
  clustered at 1.0 (472 of 586 within ±25%), not at the 4.66 CNY→TWD rate.
- DXR's on-hand, described then as PHP 1,727,604,368 + CNY 2,729,814 + TWD 171,
  adds to 1,730,334,353 — exactly the column's TWD total. The "currencies" were a
  GROUP BY on a metadata field.

The real error was ranking on `ALL_OH_AMT` (physical on-hand) while answering a
question about E&O, and never saying which metric was being used. Both numbers
were TWD and each was right for its own metric. So the rule this file pins is
about naming the metric, plus the hard prohibition that replaces the old one:
never multiply an amount by `TWD_CURRENCY_RATE`.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / ".claude" / "skills" / "eo-analysis" / "SKILL.md"
DICTIONARY = REPO_ROOT / ".claude" / "skills" / "eo-data-dictionary" / "SKILL.md"
APPENDIX = REPO_ROOT / "docs" / "eo-mcp-setup.md"

# Columns that were wrongly listed as local currency. Each must now be named as
# TWD and summable, or a future reader will reinstate the conversion.
FORMERLY_LOCAL_COLUMNS = [
    "ALL_OH_AMT",
    "UNAVAILABLE_OH_AMT",
    "WIP_AMT",
    "TP75_Amt",
    "PLANNED_ALLOCATED_AMT",
]


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dictionary_text() -> str:
    return DICTIONARY.read_text(encoding="utf-8")


def test_the_suffix_rule_is_stated_as_a_fact_not_a_preference(skill_text):
    assert "Every amount column is already TWD, suffix or no suffix." in skill_text


def test_multiplying_by_the_rate_is_forbidden(skill_text, dictionary_text):
    # This is the prohibition that replaces "only trust _TWD". Applying the rate
    # to a CNY site's figures inflates them 4.66x, which is how the old advice
    # would have been implemented.
    assert "Never multiply any\n  amount column by `TWD_CURRENCY_RATE`" in skill_text
    assert "Never multiply an amount\nby `TWD_CURRENCY_RATE`" in dictionary_text


def test_the_formerly_local_columns_are_named_as_summable(skill_text):
    for column in FORMERLY_LOCAL_COLUMNS:
        assert column in skill_text, f"{column} must be named, or the correction cannot be applied"
    assert "are TWD and **are** summable across sites" in skill_text


def test_the_metric_rule_replaces_the_currency_rule(skill_text):
    # The actual lesson: physical on-hand and E&O are different questions.
    assert "name the metric you ranked on" in skill_text
    assert "State the column you ranked on" in skill_text


def test_the_rule_carries_the_evidence_that_overturned_it(skill_text):
    # A correction without its measurement gets reverted by the next reader who
    # remembers the old rule.
    for fact in ("586 items", "472 of 586", "4.66", "1,730,334,353"):
        assert fact in skill_text, f"the correction lost its evidence: {fact}"


def test_both_rankings_are_kept_so_the_ambiguity_is_visible(skill_text):
    # Keeping both figures is what makes "name your metric" concrete.
    for fact in ("407,885,463", "365,891,158", "291,181,564"):
        assert fact in skill_text, f"the incident lost a figure: {fact}"


def test_the_dictionary_carries_the_measurement(dictionary_text):
    assert "### Currency — every amount is already TWD" in dictionary_text
    assert "The `_TWD` suffix marks nothing." in dictionary_text
    assert "returns **zero rows**" in dictionary_text


def test_the_appendix_carries_the_same_rule():
    # docs/eo-mcp-setup.md reproduces the skills verbatim for readers who cannot
    # clone the repo; test_docs_skill_sync guards the whole body byte-for-byte,
    # this names the one rule so a partial re-copy cannot drop it silently.
    text = APPENDIX.read_text(encoding="utf-8")
    assert "Every amount column is already TWD, suffix or no suffix." in text
    assert "### Currency — every amount is already TWD" in text
