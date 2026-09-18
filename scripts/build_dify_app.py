"""Build a Dify Chatflow (DSL) that answers E&O questions through the API Hub web API.

Dify has no skill mechanism and does not need MCP: it imports tools from an
OpenAPI document, which API Hub serves at ``GET /api/v1/DW/tools/openapi.json``
(one operation per definition, see ``app/services/tool_openapi.py``). This emits
the app that sits on top of those tools: Start -> Agent (function calling, the
fourteen ``query_dw_eo_*`` tools, the five E&O skills folded into its instruction)
-> Answer.

The checked-in copy at ``docs/dify/eo-analyst.yml`` carries two placeholders,
for the same reason ``build_openwebui_model`` does — a plausible wrong value
imports cleanly and fails at the first message:

- the API tool provider id, which Dify assigns when the tool is created and
  which differs on every instance;
- the model, which must be one the instance actually has.

Render a ready-to-import file for one instance with both filled in::

    python -m scripts.build_dify_app                           # rewrite the checked-in export
    python -m scripts.build_dify_app --check                   # fail if out of date (CI)
    python -m scripts.build_dify_app --stdout \\
        --provider-id <uuid> --model gb10/qwen3.8-27b > eo-analyst.yml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from scripts.build_openwebui_model import (
    PREAMBLE as OPENWEBUI_PREAMBLE,
)
from scripts.build_openwebui_model import (
    SKILL_ORDER,
    _demote_headings,
    _drop_argument_slots,
    _read_skill,
    _resolve_cross_references,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "dify" / "eo-analyst.yml"

DSL_VERSION = "0.7.0"  # Dify 1.17.x (api/constants/dsl_version.py)
APP_NAME = "E&O Analyst"

# The fourteen E&O definitions, by the operationId API Hub gives them. Must match
# what GET /DW/tools/openapi.json?group=eo lists for an eo_reader credential;
# tests/test_dify_app_export.py checks it against the tool names the skills use.
EO_TOOLS = [
    "query_dw_eo_max_frozen_date",
    "query_dw_eo_frozen_dates",
    "query_dw_eo_dimensions",
    "query_dw_eo_summary",
    "query_dw_eo_detail",
    "query_dw_eo_red_items",
    "query_dw_eo_group_diff",
    "query_dw_eo_item_diff",
    "query_dw_eo_item_history",
    "query_dw_eo_trends",
    "query_dw_eo_data_quality",
    "query_dw_eo_unrecoverable",
    "query_dw_eo_feedback",
    "query_dw_eo_feedback_summary",
]

PROVIDER_ID_PLACEHOLDER = "CHANGE_ME_API_TOOL_PROVIDER_ID"
MODEL_PLACEHOLDER = "CHANGE_ME"
MODEL_PROVIDER = "langgenius/openai_api_compatible/openai_api_compatible"
# Shown in the Agent node's tool list; Dify resolves the tool by provider id, not by this.
PROVIDER_SHOW_NAME = "apihub_eo"

AGENT_STRATEGY_PLUGIN = "langgenius/agent:0.0.47@b14b5a5259094510a272e5b7facd09aa63352511c13fabcde36a4cac57d1da94"
MODEL_PLUGIN = (
    "langgenius/openai_api_compatible:0.0.66@51801b51069b2578c3fcf6d38edc45c45efd8b7ef4fdb25f096bd84d4f95a53d"
)

# A monthly review is ~10 tool calls (periods, two trend cuts, red items per site,
# quality twice, feedback); the strategy's default of 3 stops mid-way. Not higher:
# every iteration resends every earlier tool result, and on 2026-09-15 a run that
# paged eo_dimensions four times hit the 131k context window at iteration ~6.
MAX_ITERATIONS = 12
MEMORY_WINDOW = 20

# Shown instead of a raw PluginInvokeError when the Agent node fails — in practice
# almost always a context-window overflow from a question that needs too many rows.
FAILURE_ANSWER = (
    "抱歉，這個問題這次沒能完成：查詢需要的資料量超過模型一次能處理的範圍"
    "（或查詢過程發生錯誤）。\n\n"
    "可以這樣縮小範圍再問一次：\n"
    "- 指定站別（OU）、BU、PID 或客戶群，例如「只看 SP 站」「只看 Charter」\n"
    "- 指定兩個明確的凍結日期，例如「2026-09-01 對 2026-08-01」\n"
    "- 需要全站明細或自行統計時，請改用 `scripts/extract_query.py` 抽成檔案\n\n"
    "若縮小範圍後仍然失敗，請把問題與時間回報給管理者。"
)

# The opening sentence of FAILURE_ANSWER, used as the if-else probe for "the Agent
# raised and error_strategy replaced `text`". Matching on the text itself is the only
# signal available: the node's status is not exposed to a downstream condition.
FAILURE_MARKER = "抱歉，這個問題這次沒能完成"

# The Agent can also fail *without* raising, which `error_strategy` cannot see: the
# model returns an empty completion, the strategy reports success, and the Answer
# node renders an empty string. Measured 2026-09-17 — glm-5.3-flash spent all 8192
# completion tokens on reasoning in round 1 (finish_reason "length", no content, not
# one tool call) and Dify recorded the run as succeeded with a 0-character answer.
EMPTY_ANSWER = (
    "抱歉，這次沒有產生回答：模型在寫出答案前就用完了單次輸出額度"
    "（通常是推理佔滿了預算），因此回傳了空白內容。\n\n"
    "請直接再問一次 —— 同樣的問題常常第二次就會成功。\n"
    "若連續多次都是空白，請把問題與發生時間回報給管理者。"
)

START_ID = "1757900000001"
AGENT_ID = "1757900000002"
ANSWER_ID = "1757900000003"
IFELSE_ID = "1757900000004"
EMPTY_ANSWER_ID = "1757900000005"
ERROR_ANSWER_ID = "1757900000006"

_DEPLOYMENT_HEADING = "# How this deployment differs from a shell"
_KNOWLEDGE_HEADING = "# Operating knowledge"

DIFY_DEPLOYMENT = """\
# How this deployment differs from a shell

- **The tools are Dify API tools backed by the API Hub web API.** They carry the
  exact names used below (`query_dw_eo_summary`, ...). Where the knowledge below
  says "MCP tool", read "tool" — the data, parameters and results are identical.
- **One page holds at most 100 rows.** Use `fetch_mode="count"` to size a result
  before paging, and filter (`ou`, `bu`, `pid`) rather than walking pages. If the
  answer needs thousands of rows, tell the user to extract a file with
  `scripts/extract_query.py` instead.
- **You cannot run shell commands.** Several sections show `python -m scripts...`
  commands for local caching or bulk extraction. Those are for the *user* to run
  on their own machine — hand over the exact command line and say what it does.
  Never report that you ran one, and never invent its output. There is no local
  cache here: every figure comes from a live tool call.
- **Answer in the language the user writes in** (usually Traditional Chinese).
  Keep column names, BU/PID/OU codes and tool names in their original English
  form — translating them makes the answer unverifiable against the report.
- **Amounts are TWD unless the column name says otherwise.** Several columns have
  no `_TWD` twin and are in local currency; each tool's `Returns:` text lists them.
"""


def _preamble() -> str:
    """Role + rule zero and the knowledge intro from the Open WebUI build, Dify's own deployment notes between.

    Shared rather than copied so rule zero cannot drift between the two exports.
    """
    head, sep, rest = OPENWEBUI_PREAMBLE.partition(_DEPLOYMENT_HEADING)
    _, sep2, knowledge = rest.partition(_KNOWLEDGE_HEADING)
    if not (sep and sep2):
        raise SystemExit("build_openwebui_model.PREAMBLE changed shape; update build_dify_app._preamble")
    return head + DIFY_DEPLOYMENT + "\n" + _KNOWLEDGE_HEADING + knowledge


def build_instruction() -> str:
    parts = [_preamble().rstrip("\n")]
    for index, name in enumerate(SKILL_ORDER):
        description, body = _read_skill(name)
        body = _drop_argument_slots(_resolve_cross_references(_demote_headings(body)))
        letter = chr(ord("A") + index)
        parts.append(f"## Section {letter} — {name}\n\n*{description}*\n\n{body}")
    return "\n\n---\n\n".join(parts) + "\n"


def _tool(name: str, provider_id: str) -> dict:
    return {
        "enabled": True,
        "extra": {"description": ""},
        # Empty: every argument is left for the model to fill (form "llm"). A value
        # here would pin that argument for every call.
        "parameters": {},
        "provider_name": provider_id,
        "provider_show_name": PROVIDER_SHOW_NAME,
        "settings": {},
        "tool_label": name,
        "tool_name": name,
        "type": "api",
    }


def _node(node_id: str, data: dict, x: int) -> dict:
    return {
        "data": data,
        "height": 120,
        "id": node_id,
        "position": {"x": x, "y": 280},
        "positionAbsolute": {"x": x, "y": 280},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }


def _edge(source: str, source_type: str, target: str, target_type: str, source_handle: str = "source") -> dict:
    # `source_handle` matters for if-else: Dify routes on edge_source_handle == the
    # selected case_id (or "false"), so a branch edge left as "source" never fires.
    return {
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": source_type, "targetType": target_type},
        "id": f"{source}-{source_handle}-{target}-target",
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


def build_dsl(provider_id: str = PROVIDER_ID_PLACEHOLDER, model: str = MODEL_PLACEHOLDER) -> dict:
    agent = {
        "type": "agent",
        "title": "E&O Agent",
        "desc": "",
        "selected": False,
        "agent_strategy_provider_name": "langgenius/agent/agent",
        "agent_strategy_name": "function_calling",
        "agent_strategy_label": "FunctionCalling",
        "plugin_unique_identifier": AGENT_STRATEGY_PLUGIN,
        "tool_node_version": "2",
        # On failure the node emits `text` = FAILURE_ANSWER and the flow still reaches
        # Answer, so the user gets guidance instead of a plugin stack trace.
        "error_strategy": "default-value",
        "default_value": [{"key": "text", "type": "string", "value": FAILURE_ANSWER}],
        "memory": {
            "query_prompt_template": "{{#sys.query#}}",
            "window": {"enabled": True, "size": MEMORY_WINDOW},
        },
        "agent_parameters": {
            "model": {
                "type": "constant",
                "value": {
                    "type": "model-selector",
                    "provider": MODEL_PROVIDER,
                    "model": model,
                    "model_type": "llm",
                    "mode": "chat",
                    # Low but not zero: this is reporting work, not brainstorming.
                    "completion_params": {"temperature": 0.2},
                },
            },
            "tools": {"type": "constant", "value": [_tool(name, provider_id) for name in EO_TOOLS]},
            "instruction": {"type": "constant", "value": build_instruction()},
            "query": {"type": "mixed", "value": "{{#sys.query#}}"},
            "maximum_iterations": {"type": "constant", "value": MAX_ITERATIONS},
        },
    }
    # Catches the failure `error_strategy` cannot see: an empty completion is reported
    # as success, so nothing raises and Answer renders "". Sits *after* Answer rather
    # than between Agent and Answer, so the normal reply still streams token by token
    # instead of being held back until a branch can be evaluated.
    ifelse = {
        "type": "if-else",
        "title": "Empty answer?",
        "desc": "",
        "cases": [
            {
                "case_id": "true",
                "logical_operator": "and",
                "conditions": [
                    {
                        "id": "agent-text-empty",
                        "variable_selector": [AGENT_ID, "text"],
                        "comparison_operator": "empty",
                    }
                ],
            },
            # The other half of the same problem. When the Agent *raises*,
            # error_strategy writes FAILURE_ANSWER into `text` — but Answer has
            # already streamed whatever the agent produced before the exception, so
            # the guidance never reaches the user. Measured 2026-09-16/17: answers of
            # 331, 8, 5 and 4 characters where the fallback was sitting in the node's
            # outputs the whole time. Re-emitting `text` downstream delivers it.
            # Order matters: `empty` is tested first, so a failure that produced no
            # text at all takes the empty branch rather than this one.
            {
                "case_id": "errored",
                "logical_operator": "and",
                "conditions": [
                    {
                        "id": "agent-text-is-fallback",
                        "variable_selector": [AGENT_ID, "text"],
                        "comparison_operator": "contains",
                        "value": FAILURE_MARKER,
                    }
                ],
            },
        ],
    }
    return {
        "app": {
            "description": (
                "Sercomm E&O 庫存分析。經 API Hub web API 查詢 14 個 query_dw_eo_* 工具，"
                "並依 E&O skill 的欄位語意與歸屬追查流程解讀結果。"
            ),
            "icon": "📦",
            "icon_background": "#E0F2FE",
            "mode": "advanced-chat",
            "name": APP_NAME,
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [
            {
                "current_identifier": None,
                "type": "marketplace",
                "value": {"marketplace_plugin_unique_identifier": plugin},
            }
            for plugin in (AGENT_STRATEGY_PLUGIN, MODEL_PLUGIN)
        ],
        "kind": "app",
        "version": DSL_VERSION,
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {"enabled": False},
                "opening_statement": "",
                "retriever_resource": {"enabled": False},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [
                    "列出可用的 E&O 凍結日期，並告訴我最新一期是哪一天。",
                    "SP 站最新一期的呆滯金額是多少？依 BU 排出前五名。",
                    "SP 站最新一期的紅燈料號，比前一期多了哪些、少了哪些？",
                    "料號 <ITEM_NO> 為什麼掛在這個 BU/PID 底下？什麼時候變的？",
                ],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": {
                "edges": [
                    _edge(START_ID, "start", AGENT_ID, "agent"),
                    _edge(AGENT_ID, "agent", ANSWER_ID, "answer"),
                    _edge(ANSWER_ID, "answer", IFELSE_ID, "if-else"),
                    # Both failure branches are wired; the "false" handle deliberately
                    # has no edge, so a run that answered normally simply ends here.
                    _edge(IFELSE_ID, "if-else", EMPTY_ANSWER_ID, "answer", source_handle="true"),
                    _edge(IFELSE_ID, "if-else", ERROR_ANSWER_ID, "answer", source_handle="errored"),
                ],
                "nodes": [
                    _node(START_ID, {"type": "start", "title": "Start", "desc": "", "variables": []}, 80),
                    _node(AGENT_ID, agent, 380),
                    _node(
                        ANSWER_ID,
                        {"type": "answer", "title": "Answer", "desc": "", "answer": f"{{{{#{AGENT_ID}.text#}}}}"},
                        680,
                    ),
                    _node(IFELSE_ID, ifelse, 980),
                    _node(
                        EMPTY_ANSWER_ID,
                        {"type": "answer", "title": "Empty answer notice", "desc": "", "answer": EMPTY_ANSWER},
                        1280,
                    ),
                    # Re-emits the Agent's `text`, which error_strategy has by now
                    # replaced with FAILURE_ANSWER. Answer (the first one) streamed
                    # before the exception landed, so this is what actually delivers
                    # the guidance instead of leaving a truncated reply.
                    _node(
                        ERROR_ANSWER_ID,
                        {
                            "type": "answer",
                            "title": "Failure notice",
                            "desc": "",
                            "answer": f"{{{{#{AGENT_ID}.text#}}}}",
                        },
                        1580,
                    ),
                ],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            },
        },
    }


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str):
    # Block style keeps the ~40k-character instruction reviewable in a diff.
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)


def render(provider_id: str = PROVIDER_ID_PLACEHOLDER, model: str = MODEL_PLACEHOLDER) -> str:
    return yaml.dump(
        build_dsl(provider_id, model),
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        width=10_000,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the checked-in export is stale")
    parser.add_argument("--stdout", action="store_true", help="print the DSL instead of writing it")
    parser.add_argument("--out", type=Path, default=OUTPUT, help=f"output path (default: {OUTPUT})")
    parser.add_argument("--provider-id", default=PROVIDER_ID_PLACEHOLDER, help="Dify API tool provider id")
    parser.add_argument("--model", default=MODEL_PLACEHOLDER, help="model name on the OpenAI-compatible provider")
    args = parser.parse_args()

    filled = args.provider_id != PROVIDER_ID_PLACEHOLDER or args.model != MODEL_PLACEHOLDER
    payload = render(args.provider_id, args.model)

    if args.stdout:
        sys.stdout.write(payload)
        return 0

    if args.check:
        expected = render()
        if not args.out.is_file():
            print(f"missing {args.out} — run: python -m scripts.build_dify_app", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != expected:
            print(f"{args.out} is stale — run: python -m scripts.build_dify_app", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0

    if filled and args.out == OUTPUT:
        # The checked-in copy must stay instance-neutral; an instance's ids belong in --stdout.
        print("--provider-id/--model are for --stdout or a different --out, not the checked-in export", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    print(f"wrote {args.out} ({len(payload):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
