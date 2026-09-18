"""Pin the drill-down rule: carry the filter you already know into the next call.

2026-09-16, measured: a run answered "Charter's biggest project and its top five
components" correctly through nine filtered calls — it had `DXR` — then fetched
two unfiltered `eo_detail` pages for the whole customer (100 rows x 89 columns,
~2.6 KB a row) and hit the 131k context window one step short of the answer.

The tools were not missing anything: `customer` had shipped that morning and the
agent used it on nine of eleven calls. Only the last step was wrong, which is why
this is a skill rule rather than an API change.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / ".claude" / "skills" / "eo-analysis" / "SKILL.md"
APPENDIX = REPO_ROOT / "docs" / "eo-mcp-setup.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_the_rule_is_present_and_imperative(skill_text):
    assert "every later call must carry it" in skill_text


def test_the_rule_names_the_closing_move(skill_text):
    # The fix is specific: pid *and* columns on the last hop, not "be careful".
    assert "`eo_detail` with `pid` **and** `columns` set" in skill_text


def test_the_rule_carries_the_measurement(skill_text):
    for fact in ("DXR", "89", "2.6 KB"):
        assert fact in skill_text, f"the rule lost its evidence: {fact}"


def test_the_tool_map_repeats_it_where_eo_detail_is_chosen(skill_text):
    tool_map = skill_text.split("## Tool map", 1)[1].split("\n## ", 1)[0]
    row = next(line for line in tool_map.splitlines() if "`eo_detail`" in line)
    assert "columns" in row and "pid" in row, "the tool map row must carry the warning too"


def test_the_appendix_carries_the_same_rule():
    assert "every later call must carry it" in APPENDIX.read_text(encoding="utf-8")
