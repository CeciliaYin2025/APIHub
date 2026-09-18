"""Pin the rule that a delta ranking must be reconciled against the net change.

Observed 2026-09-18 on the Charter question. Ranked by E&O increase, three
projects rose: DX8 +365.9M, EAC +155.3M, DY1 +17.2M — a gross +538.4M, which the
answer presented as "the whole story" of the period. Charter's actual total went
1,632.9M → 1,875.0M: a net **+242.1M**, because DXR fell 291.2M. The gross figure
overstated the deterioration by more than double, and DXR's fall was sitting in
the same answer's footnote, never connected to the headline.

The mechanism is structural, not a slip: `order_by` on a delta returns only the
rows that moved the way you sorted, so the losers are off the bottom of page 1.
Anyone who sums what they were shown describes a different period than the one
they were asked about.

The same answer also quoted a headline total of 1,857.0M from `eo_dimensions`
(which returns `OBSOLETE_OHOPO_AMT_TWD`, excluding TP70/TP75) while every line
item was built on `OBSOLETE_OHOPO_ALL_AMT_TWD` (1,875.0M). Two metrics in one
table, 18M apart. That half of the rule is pinned here too.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / ".claude" / "skills" / "eo-analysis" / "SKILL.md"
APPENDIX = REPO_ROOT / "docs" / "eo-mcp-setup.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_the_rule_is_stated_as_an_obligation(skill_text):
    # "Report both" is what makes it actionable; a softer "consider the decliners"
    # is what the failing answer already did — in a footnote.
    assert "Gross increase is not net change. Report both" in skill_text
    assert 'never call the gainers\n  "the whole story."' in skill_text


def test_the_rule_says_what_to_actually_do(skill_text):
    assert "also total the\n  population and say which number is which" in skill_text


def test_the_rule_explains_why_ranking_hides_the_decliners(skill_text):
    # Without the mechanism the rule reads as a style note and gets dropped.
    assert "the ones that fell are off the bottom of the page" in skill_text


def test_the_rule_carries_the_case_that_produced_it(skill_text):
    for fact in ("538.4M", "1,632.9M", "1,875.0M", "242.1M", "291.2M"):
        assert fact in skill_text, f"the rule lost its evidence: {fact}"


def test_the_same_column_rule_names_both_columns(skill_text):
    # The failure was mixing the _ALL_ variant with the one eo_dimensions prints.
    assert "A total must come from the same column you ranked on." in skill_text
    assert "`eo_dimensions`\n  returns `OBSOLETE_OHOPO_AMT_TWD`" in skill_text
    assert "OBSOLETE_OHOPO_ALL_AMT_TWD" in skill_text
    assert "1,857.0M vs\n  1,875.0M" in skill_text


def test_the_appendix_carries_the_same_rule():
    # docs/eo-mcp-setup.md reproduces the skills verbatim; test_docs_skill_sync
    # guards the whole body, this names the one rule so a partial re-copy cannot
    # drop it silently.
    text = APPENDIX.read_text(encoding="utf-8")
    assert "Gross increase is not net change. Report both" in text
    assert "A total must come from the same column you ranked on." in text
