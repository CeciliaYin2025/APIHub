"""Guard the skill copies embedded in the setup guide.

``docs/eo-mcp-setup.md`` reproduces the E&O skills in full so a Hermes user — or
anyone on a client with no skill mechanism — can paste them without cloning the
repo. A copy drifts silently: editing a skill and forgetting the appendix leaves
readers following stale guidance with no way to tell. These tests fail the build
instead.

The appendices are generated from the source files, so a mismatch means the doc
was hand-edited or a skill changed afterwards. Re-copy rather than relaxing the
assertion.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
DOC = REPO_ROOT / "docs" / "eo-mcp-setup.md"

# Every skill the guide is expected to carry. Adding a name here without adding
# the appendix fails the build, which is the point.
EMBEDDED_SKILLS = [
    "eo-analysis",
    "eo-data-dictionary",
    "eo-business-rules",
    "eo-monthly-review",
    "eo-issue-investigation",
]

# Four backticks: the skills contain ```bash blocks, so a three-backtick fence
# would terminate at the first of them and truncate the appendix.
APPENDIX_RE = re.compile(
    r"^### 附錄 [A-H] — `(?P<name>[a-z0-9-]+)`\n.*?^````markdown\n(?P<body>.*?)^````$",
    re.DOTALL | re.MULTILINE,
)


def _appendices() -> dict[str, str]:
    return {m.group("name"): m.group("body") for m in APPENDIX_RE.finditer(DOC.read_text(encoding="utf-8"))}


def test_doc_exists():
    assert DOC.is_file(), f"missing {DOC}"


def test_every_expected_skill_has_an_appendix():
    assert sorted(_appendices()) == sorted(EMBEDDED_SKILLS)


@pytest.mark.parametrize("name", EMBEDDED_SKILLS)
def test_appendix_matches_its_skill_exactly(name):
    source = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    embedded = _appendices().get(name)
    assert embedded is not None, f"no appendix for {name}"
    # The generator strips trailing blank lines before fencing; compare on that basis.
    assert embedded.rstrip("\n") == source.rstrip("\n"), (
        f"appendix for {name} has drifted from .claude/skills/{name}/SKILL.md — re-copy it"
    )


@pytest.mark.parametrize("name", EMBEDDED_SKILLS)
def test_appendix_keeps_the_frontmatter(name):
    """Without the frontmatter the pasted copy is not a loadable skill."""
    embedded = _appendices()[name]
    assert embedded.startswith(f"---\nname: {name}\n"), f"{name}: appendix lost its frontmatter"
    assert "description:" in embedded.split("---")[1]


@pytest.mark.parametrize("name", EMBEDDED_SKILLS)
def test_appendix_keeps_the_inner_code_fences(name):
    """A three-backtick outer fence would swallow these; make sure they survived."""
    source = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    assert _appendices()[name].count("```") == source.count("```")


def test_doc_points_at_the_skills_as_the_source_of_truth():
    """The appendices must say they are copies, or readers will edit the wrong file."""
    assert ".claude/skills/<name>/SKILL.md" in DOC.read_text(encoding="utf-8")
