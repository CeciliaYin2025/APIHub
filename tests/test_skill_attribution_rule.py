"""Pin the rule that item-level attribution must be reported as one of several.

`eo_item_diff` and `eo_item_history` aggregate to one row per item and show
`BU_NO` / `PID` / `CUSTOMERGROUPNAME` as the MIN, with a `_N` column saying how
many distinct values there really are. The amount beside them is the item's
total and is right; the attribution is not the owner, only the lowest of N.

Observed 2026-09-16: SP's largest item (549B000RMR0, 1.77bn TWD) spans 8 BUs and
7 PIDs, and the agent reported a bare `E1B`. Nothing about that answer looks
wrong to a reader, which is what makes it worth a test rather than a comment.
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
    assert "must say so whenever it is" in skill_text, (
        "the rule has to read as an obligation; a softer 'consider mentioning' is what it replaced"
    )


def test_the_rule_names_the_count_columns(skill_text):
    for column in ("BU_NO_N", "PID_N", "CUSTOMER_N"):
        assert column in skill_text, f"{column} must be named, or the model cannot act on the rule"


def test_the_rule_names_both_tools_it_applies_to(skill_text):
    section = skill_text.split("## Reading the data correctly", 1)[1]
    assert "eo_item_diff" in section and "eo_item_history" in section


def test_the_rule_carries_the_case_that_produced_it(skill_text):
    # A rule with a real number attached survives editing better than an abstract one.
    assert "549B000RMR0" in skill_text
    assert "8 BUs" in skill_text


def test_the_appendix_carries_the_same_rule(skill_text):
    # docs/eo-mcp-setup.md reproduces the skills for readers who cannot clone the
    # repo; test_docs_skill_sync guards the whole body, this names the one rule.
    assert "must say so whenever it is" in APPENDIX.read_text(encoding="utf-8")
