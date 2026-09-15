"""Build an Open WebUI model export from the E&O skills.

Open WebUI has no skill mechanism. What it has is a *workspace model*: a base
model plus a stored system prompt, importable from ``Workspace -> Models ->
Import``. So the five ``.claude/skills/eo-*/SKILL.md`` files are folded into one
system prompt and emitted as the JSON array that importer expects.

The output is checked in at ``docs/openwebui/eo-analyst.json`` so a user can
download it without cloning the repo; ``tests/test_openwebui_model_export.py``
fails the build when it drifts from the skills.

    python -m scripts.build_openwebui_model            # rewrite the export
    python -m scripts.build_openwebui_model --check    # fail if out of date
    python -m scripts.build_openwebui_model --stdout   # print, write nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
OUTPUT = REPO_ROOT / "docs" / "openwebui" / "eo-analyst.json"

# eo-analysis first: it is the entry point the others are referenced from.
SKILL_ORDER = [
    "eo-analysis",
    "eo-data-dictionary",
    "eo-business-rules",
    "eo-monthly-review",
    "eo-issue-investigation",
]

MODEL_ID = "eo-analyst"
MODEL_NAME = "E&O Analyst"

# Open WebUI resolves `meta.toolIds` against the tool servers configured on the
# instance; this is what that server is called on the ISAP instance. An id that
# does not resolve is skipped silently, which looks exactly like a model that has
# decided not to call anything — so a rename here is not cosmetic.
TOOL_ID = "API Hub — E&O"

# Open WebUI requires an existing backend model to sit under the workspace model.
# We cannot know which one this instance has, and a wrong id imports cleanly and
# then fails at first message — so ship a value that is obviously a placeholder.
BASE_MODEL_PLACEHOLDER = "CHANGE_ME"

FRONTMATTER_RE = re.compile(r"\A---\n(?P<fm>.*?)\n---\n", re.DOTALL)
FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})(\s)")
WIKILINK_RE = re.compile(r"\[\[([a-z0-9-]+)\]\]")


def _read_skill(name: str) -> tuple[str, str]:
    """Return (description, body) for one skill, frontmatter stripped."""
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SystemExit(f"{name}: SKILL.md has no YAML frontmatter")
    description = ""
    for line in match.group("fm").splitlines():
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
    if not description:
        raise SystemExit(f"{name}: frontmatter has no description")
    return description, text[match.end() :].strip("\n")


def _demote_headings(body: str, levels: int = 2) -> str:
    """Push every heading down `levels`, leaving fenced code untouched.

    The skills open at ``#``; they are nested under ``##`` here, so their own
    headings have to move or the composed prompt reads as five documents. Bash
    comments inside fences start with ``#`` too, hence the fence tracking.
    """
    out: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        marker = FENCE_RE.match(line)
        if marker:
            token = marker.group("fence")
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            out.append(line)
            continue
        if fence is None:
            heading = HEADING_RE.match(line)
            if heading:
                depth = min(len(heading.group(1)) + levels, 6)
                line = "#" * depth + line[len(heading.group(1)) :]
        out.append(line)
    return "\n".join(out)


def _resolve_cross_references(body: str) -> str:
    """`[[eo-data-dictionary]]` points at a file that does not exist here."""
    return WIKILINK_RE.sub(lambda m: f'the "{m.group(1)}" section of this prompt', body)


ARGUMENT_SLOT_RE = re.compile(r"^\*\*[^\n]*:\*\*\s*\$ARGUMENTS\s*$\n\n?", re.MULTILINE)


def _drop_argument_slots(body: str) -> str:
    """Each skill opens with a `**Task:** $ARGUMENTS` line for slash-command use.

    Open WebUI has no argument slot — the task is simply the chat message — so
    the line is removed rather than left holding an unfilled placeholder.
    """
    cleaned = ARGUMENT_SLOT_RE.sub("", body)
    if "$ARGUMENTS" in cleaned:
        raise SystemExit("an $ARGUMENTS placeholder survived; the skill uses an unexpected shape")
    return cleaned


PREAMBLE = """\
# Role

You are Sercomm's E&O (Excess & Obsolete) inventory analyst. You answer questions
about obsolete and excess stock by querying the API Hub E&O tools and reading the
results through the operating knowledge below.

# Rule zero: every E&O figure comes from a tool call in this turn

This model has no E&O data of its own. The knowledge below tells you how to read
the data; it contains **no** amounts, item numbers or period values you may quote.

1. **Never answer an E&O question without calling at least one `query_dw_eo_*`
   tool in the same turn.** Not from memory, not from your training data, not from
   a figure that appeared earlier in this conversation, not from a file the user
   pasted, and not from any example in this prompt.
2. **Pin the period first.** The opening call for a new E&O question is
   `eo_max_frozen_date` or `eo_frozen_dates`. Periods are weekly, not monthly, and
   an invented date returns zero rows rather than an error — which reads exactly
   like "no E&O".
3. **Resolve codes before filtering.** `BU_NO`, `PID`, `OU`, customer group: get
   them from `eo_dimensions`. A guessed code also returns zero rows silently.
4. **Re-quoting an earlier figure still requires its provenance.** Name the tool
   and the frozen date it came from. If the user has since changed the period, the
   site or the scope, query again — do not carry the old number across.
5. **Never estimate, extrapolate, or fill a gap.** If a tool returns nothing for
   part of the question, report that part as unavailable and say which call came
   back empty.
6. **If the tools are not attached or a call fails, stop and say so.** "I cannot
   reach the E&O tools" is the correct answer. An answer built from general
   knowledge about inventory reporting is not.
7. **Show your source.** Every table or figure in your reply names the tool, the
   `frozen_date`, and the filters you passed. A number without its period is
   meaningless here.

# How this deployment differs from a shell

- **Tool names may carry a server prefix** in this UI — the E&O tools are served
  by a tool server named "API Hub — E&O", so the exposed name may be prefixed or
  reformatted. The knowledge below uses the bare names (`query_dw_eo_summary`,
  `eo_frozen_dates`, ...); match them by suffix, not by exact string.
- **You cannot run shell commands.** Several sections show `python -m scripts...`
  commands for local caching or bulk extraction. Those are for the *user* to run
  on their own machine — hand over the exact command line and say what it does.
  Never report that you ran one, and never invent its output.
- **Answer in the language the user writes in** (usually Traditional Chinese).
  Keep column names, BU/PID/OU codes and tool names in their original English
  form — translating them makes the answer unverifiable against the report.
- **Amounts are TWD unless the column name says otherwise.** Several columns have
  no `_TWD` twin and are in local currency; each tool's `Returns:` text lists them.

# Operating knowledge

The five sections below are the E&O skills, reproduced in full. They are the
authority on which tool answers which question, which columns may be summed, and
how attribution is investigated. Where they disagree with your prior assumptions
about inventory reporting, they win.
"""


def build_system_prompt() -> str:
    parts = [PREAMBLE.rstrip("\n")]
    for index, name in enumerate(SKILL_ORDER):
        description, body = _read_skill(name)
        body = _drop_argument_slots(_resolve_cross_references(_demote_headings(body)))
        letter = chr(ord("A") + index)
        parts.append(f"## Section {letter} — {name}\n\n*{description}*\n\n{body}")
    return "\n\n---\n\n".join(parts) + "\n"


def build_export() -> list[dict]:
    return [
        {
            "id": MODEL_ID,
            "base_model_id": BASE_MODEL_PLACEHOLDER,
            "name": MODEL_NAME,
            "meta": {
                "profile_image_url": "/static/favicon.png",
                "description": (
                    "Sercomm E&O 庫存分析。查詢 API Hub 的 12 個 query_dw_eo_* 工具，"
                    "並依 E&O skill 的欄位語意與歸屬追查流程解讀結果。"
                    "使用前請把 base_model_id 改成這台實例上實際存在的模型，"
                    f'並確認 toolIds 的 "{TOOL_ID}" 對得上這台實例的工具伺服器 id。'
                ),
                "capabilities": {
                    "vision": False,
                    "file_upload": True,
                    "web_search": False,
                    "image_generation": False,
                    "code_interpreter": True,
                    "citations": True,
                    "usage": False,
                },
                "suggestion_prompts": [
                    {"content": "列出可用的 E&O 凍結日期，並告訴我最新一期是哪一天。"},
                    {"content": "SP 站最新一期的呆滯金額是多少？依 BU 排出前五名。"},
                    {"content": "SP 站最新一期的紅燈料號，比前一期多了哪些、少了哪些？"},
                    {"content": "料號 <ITEM_NO> 為什麼掛在這個 BU/PID 底下？什麼時候變的？"},
                ],
                "tags": [{"name": "E&O"}, {"name": "Sercomm"}, {"name": "API Hub"}],
                "toolIds": [TOOL_ID],
            },
            "params": {
                "system": build_system_prompt(),
                # Low but not zero: this is reporting work, not brainstorming.
                "temperature": 0.2,
            },
            # null = visible to every user on the instance. Restrict in the UI if
            # the E&O figures should not be.
            "access_control": None,
            "is_active": True,
        }
    ]


def render() -> str:
    return json.dumps(build_export(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the checked-in export is stale")
    parser.add_argument("--stdout", action="store_true", help="print the export instead of writing it")
    parser.add_argument("--out", type=Path, default=OUTPUT, help=f"output path (default: {OUTPUT})")
    args = parser.parse_args()

    payload = render()

    if args.stdout:
        sys.stdout.write(payload)
        return 0

    if args.check:
        if not args.out.is_file():
            print(f"missing {args.out} — run: python -m scripts.build_openwebui_model", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != payload:
            print(f"{args.out} is stale — run: python -m scripts.build_openwebui_model", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    print(f"wrote {args.out} ({len(payload):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
