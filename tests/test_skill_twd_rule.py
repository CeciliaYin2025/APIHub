"""Pin the rule that every reported E&O figure comes from a `_TWD` column.

Five summary columns have **no** `_TWD` twin and carry each row's local currency:
`ALL_OH_AMT`, `UNAVAILABLE_OH_AMT`, `WIP_AMT`, `TP75_Amt`, `PLANNED_ALLOCATED_AMT`.
Summing one of them across sites adds pesos to yuan to dollars and produces a
total that looks perfectly ordinary and means nothing.

Observed 2026-09-17: ranking Charter's projects on `ALL_OH_AMT` put `DXR` top at
+683,864,680 — that period its ALL_OH was PHP 1,727,604,368 + CNY 2,729,814 +
TWD 171. Ranked on `OBSOLETE_OHOPO_ALL_AMT_TWD` the answer is `DX8` +365,891,158,
and DXR's E&O had in fact *fallen* 291,181,564. Wrong project, wrong direction,
and nothing about the number looked suspect — which is why this is a test and not
a comment.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / ".claude" / "skills" / "eo-analysis" / "SKILL.md"
APPENDIX = REPO_ROOT / "docs" / "eo-mcp-setup.md"

# The columns a caller may not sum across sites. Each is named in eo_summary's own
# Returns: text as having no _TWD twin.
LOCAL_CURRENCY_COLUMNS = [
    "ALL_OH_AMT",
    "UNAVAILABLE_OH_AMT",
    "WIP_AMT",
    "TP75_Amt",
    "PLANNED_ALLOCATED_AMT",
]


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_the_rule_is_stated_as_an_obligation(skill_text):
    # "always" is the word that makes this actionable; a softer "prefer TWD" is
    # what produced the wrong answer in the first place.
    assert "Rank, total and compare in TWD — always." in skill_text


def test_the_rule_names_every_column_without_a_twd_twin(skill_text):
    for column in LOCAL_CURRENCY_COLUMNS:
        assert column in skill_text, f"{column} must be named, or the rule cannot be applied"


def test_the_rule_carries_the_case_that_produced_it(skill_text):
    # A rule with the real figures attached survives editing better than an
    # abstract warning about currencies.
    for fact in ("683,864,680", "1,727,604,368", "DX8", "365,891,158"):
        assert fact in skill_text, f"the rule lost its evidence: {fact}"


def test_the_rule_says_what_to_do_when_a_local_column_is_genuinely_needed(skill_text):
    # Forbidding the column outright would be wrong — sometimes it is the answer.
    # The rule has to offer the per-site alternative instead of a flat ban.
    section = skill_text.split("## Reading the data correctly", 1)[1]
    assert "per site with the currency named" in section


def test_the_appendix_carries_the_same_rule():
    # docs/eo-mcp-setup.md reproduces the skills verbatim for readers who cannot
    # clone the repo; test_docs_skill_sync guards the whole body byte-for-byte,
    # this names the one rule so a partial re-copy cannot drop it silently.
    assert "Rank, total and compare in TWD — always." in APPENDIX.read_text(encoding="utf-8")
