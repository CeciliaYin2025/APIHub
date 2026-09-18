"""Guard the Dify Chatflow export (docs/dify/eo-analyst.yml).

Same drift problem as the Open WebUI export — the five skills are copied into
the Agent node's instruction — plus Dify-specific ways to import cleanly and
then fail: a tool name that does not match an API Hub operationId, or an
instruction that Dify's template engine rewrites.

Regenerate rather than relaxing an assertion::

    python -m scripts.build_dify_app
"""

import re

import pytest
import yaml

from scripts.build_dify_app import (
    AGENT_ID,
    ANSWER_ID,
    EMPTY_ANSWER,
    EMPTY_ANSWER_ID,
    EO_TOOLS,
    ERROR_ANSWER_ID,
    FAILURE_MARKER,
    IFELSE_ID,
    MODEL_PLACEHOLDER,
    OUTPUT,
    PROVIDER_ID_PLACEHOLDER,
    START_ID,
    render,
)
from scripts.build_openwebui_model import SKILL_ORDER, SKILLS_DIR


@pytest.fixture(scope="module")
def dsl():
    assert OUTPUT.is_file(), f"missing {OUTPUT} — run: python -m scripts.build_dify_app"
    return yaml.safe_load(OUTPUT.read_text(encoding="utf-8"))


def _nodes(dsl):
    return {n["id"]: n["data"] for n in dsl["workflow"]["graph"]["nodes"]}


def _agent(dsl):
    return _nodes(dsl)[AGENT_ID]


def test_checked_in_export_is_current():
    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        f"{OUTPUT.name} has drifted from the skills — run: python -m scripts.build_dify_app"
    )


def test_is_a_chatflow_for_dify_1_17(dsl):
    assert dsl["kind"] == "app"
    assert dsl["version"] == "0.7.0"
    assert dsl["app"]["mode"] == "advanced-chat"


def test_graph_streams_the_agent_then_guards_both_failure_modes(dsl):
    types = {i: d["type"] for i, d in _nodes(dsl).items()}
    assert sorted(types.values()) == ["agent", "answer", "answer", "answer", "if-else", "start"]
    edges = {(e["source"], e["target"]) for e in dsl["workflow"]["graph"]["edges"]}
    assert edges == {
        (START_ID, AGENT_ID),
        (AGENT_ID, ANSWER_ID),
        (ANSWER_ID, IFELSE_ID),
        (IFELSE_ID, EMPTY_ANSWER_ID),
        (IFELSE_ID, ERROR_ANSWER_ID),
    }
    # Answer sits directly downstream of the Agent so the reply streams as it is
    # produced. Moving the if-else between them would hold every token back until
    # the agent finished, which on a multi-minute run is a blank screen.
    assert _nodes(dsl)[ANSWER_ID]["answer"] == f"{{{{#{AGENT_ID}.text#}}}}"


def test_an_empty_agent_output_is_caught_by_the_first_case(dsl):
    # error_strategy only fires on an exception. A model that returns an empty
    # completion is reported as *success*, so without this branch the user gets a
    # blank reply while every status field still reads healthy (2026-09-17).
    case = _nodes(dsl)[IFELSE_ID]["cases"][0]
    assert case["case_id"] == "true"
    assert case["conditions"][0]["variable_selector"] == [AGENT_ID, "text"]
    assert case["conditions"][0]["comparison_operator"] == "empty"


def test_a_raised_agent_is_caught_by_the_second_case(dsl):
    # The other half of the same problem: on an exception error_strategy writes
    # FAILURE_ANSWER into `text`, but Answer has already streamed, so the guidance
    # never reaches the user (measured 2026-09-16/17: replies of 331, 8, 5 and 4
    # characters while the fallback sat in the node's outputs). Matching on the text
    # is the only available signal — a node's status is not visible to a condition.
    case = _nodes(dsl)[IFELSE_ID]["cases"][1]
    assert case["case_id"] == "errored"
    assert case["conditions"][0]["comparison_operator"] == "contains"
    assert case["conditions"][0]["value"] == FAILURE_MARKER
    assert FAILURE_MARKER in _agent(dsl)["default_value"][0]["value"], (
        "the probe must be a substring of the text error_strategy actually writes"
    )


def test_empty_is_tested_before_errored(dsl):
    # A failure that produced no text at all could match either case; Dify takes the
    # first match, and the empty notice is the more specific message of the two.
    assert [c["case_id"] for c in _nodes(dsl)[IFELSE_ID]["cases"]] == ["true", "errored"]


def test_each_branch_edge_carries_its_own_case_handle(dsl):
    # Dify routes on edge_source_handle == the selected case_id; an edge left on the
    # default "source" handle imports cleanly and then never fires.
    handles = {e["target"]: e["sourceHandle"] for e in dsl["workflow"]["graph"]["edges"] if e["source"] == IFELSE_ID}
    assert handles == {EMPTY_ANSWER_ID: "true", ERROR_ANSWER_ID: "errored"}


def test_the_empty_branch_tells_the_user_what_to_do(dsl):
    text = _nodes(dsl)[EMPTY_ANSWER_ID]["answer"]
    assert text == EMPTY_ANSWER
    assert "再問一次" in text, "a blank reply needs a next step, not just an apology"


def test_the_failure_branch_re_emits_the_agent_text(dsl):
    # Deliberately not a second copy of FAILURE_ANSWER: re-reading `text` delivers
    # whatever error_strategy actually wrote, once the first Answer has finished
    # streaming. A hard-coded copy would drift from default_value.
    assert _nodes(dsl)[ERROR_ANSWER_ID]["answer"] == f"{{{{#{AGENT_ID}.text#}}}}"


def test_agent_strategy_plugin_is_declared_as_a_dependency(dsl):
    # Without the strategy plugin the Agent node has nothing to run; declaring it
    # makes Dify offer the install on import instead of failing at the first message.
    agent = _agent(dsl)
    deps = [d["value"]["marketplace_plugin_unique_identifier"] for d in dsl["dependencies"]]
    assert agent["plugin_unique_identifier"] in deps
    assert agent["agent_strategy_name"] == "function_calling"


def test_tools_are_the_eo_operations(dsl):
    tools = _agent(dsl)["agent_parameters"]["tools"]["value"]
    assert [t["tool_name"] for t in tools] == EO_TOOLS
    assert len(set(EO_TOOLS)) == 14
    assert all(t["type"] == "api" and t["enabled"] for t in tools)
    assert all(t["parameters"] == {} for t in tools), "a pinned parameter would apply to every call"


def test_every_tool_in_the_skill_tool_map_is_attached():
    # A skill telling the model to call a tool the agent does not have produces
    # exactly the "answered without data" failure rule zero forbids. The tool map
    # in eo-analysis is the authoritative list the skills route questions through.
    text = (SKILLS_DIR / "eo-analysis" / "SKILL.md").read_text(encoding="utf-8")
    tool_map = text.split("## Tool map", 1)[1].split("\n## ", 1)[0]
    listed = {f"query_dw_{m}" for m in re.findall(r"`(eo_[a-z_]+)`", tool_map)}
    assert len(listed) == 14, f"tool map shape changed: {sorted(listed)}"
    assert listed == set(EO_TOOLS)


def test_agent_failure_yields_guidance_not_a_stack_trace(dsl):
    # A context-window overflow otherwise surfaces as a raw PluginInvokeError. The
    # default value must land on the variable the Answer node reads.
    agent = _agent(dsl)
    assert agent["error_strategy"] == "default-value"
    assert agent["default_value"] == [{"key": "text", "type": "string", "value": agent["default_value"][0]["value"]}]
    assert "縮小範圍" in agent["default_value"][0]["value"]


def test_iteration_cap_bounds_context_growth(dsl):
    # Every iteration resends all earlier tool results; 20 let one question reach
    # the model's 131k window.
    assert _agent(dsl)["agent_parameters"]["maximum_iterations"]["value"] <= 12


def test_placeholders_are_obvious(dsl):
    agent = _agent(dsl)
    assert {t["provider_name"] for t in agent["agent_parameters"]["tools"]["value"]} == {PROVIDER_ID_PLACEHOLDER}
    assert agent["agent_parameters"]["model"]["value"]["model"] == MODEL_PLACEHOLDER


def test_render_fills_instance_values():
    filled = yaml.safe_load(render(provider_id="abc-123", model="gb10/qwen3.8-27b"))
    agent = _agent(filled)
    assert {t["provider_name"] for t in agent["agent_parameters"]["tools"]["value"]} == {"abc-123"}
    assert agent["agent_parameters"]["model"]["value"]["model"] == "gb10/qwen3.8-27b"


def test_instruction_has_no_dify_variable_syntax(dsl):
    # Dify substitutes {{#node.var#}} in agent inputs; a stray one would be
    # silently replaced with an empty string in the middle of the knowledge.
    assert "{{#" not in _agent(dsl)["agent_parameters"]["instruction"]["value"]


@pytest.mark.parametrize("name", SKILL_ORDER)
def test_every_skill_body_survives_into_the_instruction(name, dsl):
    prompt = _agent(dsl)["agent_parameters"]["instruction"]["value"]
    body = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8").split("---\n", 2)[-1]
    plain = [
        line
        for line in body.splitlines()
        if line.strip() and not line.startswith("#") and "[[" not in line and "$ARGUMENTS" not in line
    ]
    assert len(plain) > 20, f"{name}: nothing left to compare — the skill's shape changed"
    missing = [line for line in plain if line not in prompt]
    assert not missing, f"{name}: {len(missing)} line(s) absent from the export, first: {missing[0]!r}"


def test_instruction_keeps_rule_zero_and_dify_deployment_notes(dsl):
    prompt = _agent(dsl)["agent_parameters"]["instruction"]["value"]
    assert "Never answer an E&O question without calling at least one" in prompt
    assert "cannot run shell commands" in prompt
    assert "Dify API tools" in prompt
    assert "API Hub — E&O" not in prompt, "the Open WebUI tool-server note leaked into the Dify build"
