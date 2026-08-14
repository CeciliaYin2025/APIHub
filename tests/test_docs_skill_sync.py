"""Guard the copy of the eo-analysis skill embedded in the setup guide.

``docs/eo-mcp-setup.md`` reproduces ``.claude/skills/eo-analysis/SKILL.md`` in
full so a Hermes (or any non-Claude-Code) user can paste it without cloning the
repo. A copy drifts silently: editing the skill and forgetting the appendix
leaves readers following stale guidance with no way to tell. These tests fail
the build instead.

If a change here is deliberate, update the appendix in the doc to match — do not
relax the assertion.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / ".claude" / "skills" / "eo-analysis" / "SKILL.md"
DOC = REPO_ROOT / "docs" / "eo-mcp-setup.md"

# Four backticks: the skill itself contains ```bash blocks, so a three-backtick
# fence would terminate at the first of them and truncate the appendix.
APPENDIX_RE = re.compile(r"^````markdown\n(.*?)^````$", re.DOTALL | re.MULTILINE)


def _appendix() -> str:
    match = APPENDIX_RE.search(DOC.read_text(encoding="utf-8"))
    assert match, "no ````markdown appendix block found in docs/eo-mcp-setup.md"
    return match.group(1)


def test_skill_and_doc_both_exist():
    assert SKILL.is_file(), f"missing {SKILL}"
    assert DOC.is_file(), f"missing {DOC}"


def test_appendix_matches_skill_exactly():
    assert _appendix() == SKILL.read_text(encoding="utf-8"), (
        "docs/eo-mcp-setup.md appendix A has drifted from "
        ".claude/skills/eo-analysis/SKILL.md — re-copy the file into the appendix"
    )


def test_appendix_keeps_the_frontmatter():
    """Without the frontmatter the pasted copy is not a loadable skill."""
    appendix = _appendix()
    assert appendix.startswith("---\nname: eo-analysis\n"), "appendix lost its frontmatter"
    assert "description:" in appendix.split("---")[1]


def test_appendix_keeps_the_inner_code_fences():
    """A three-backtick outer fence would swallow these; make sure they survived."""
    assert _appendix().count("```") == SKILL.read_text(encoding="utf-8").count("```")


def test_doc_points_at_the_skill_as_the_source_of_truth():
    """The appendix must say it is a copy, or readers will edit the wrong file."""
    doc = DOC.read_text(encoding="utf-8")
    assert ".claude/skills/eo-analysis/SKILL.md" in doc
