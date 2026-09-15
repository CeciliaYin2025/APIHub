"""Guard the Open WebUI model export.

``docs/openwebui/eo-analyst.json`` carries the five E&O skills inside a system
prompt so an Open WebUI user can import them without a skill mechanism. It is a
copy, and a copy drifts: editing a skill and forgetting the export leaves that
audience following stale guidance with nothing to tell them so.

Regenerate rather than relaxing an assertion::

    python -m scripts.build_openwebui_model
"""

import json

import pytest

from scripts.build_openwebui_model import OUTPUT, SKILL_ORDER, SKILLS_DIR, render


@pytest.fixture(scope="module")
def export():
    assert OUTPUT.is_file(), f"missing {OUTPUT} — run: python -m scripts.build_openwebui_model"
    return json.loads(OUTPUT.read_text(encoding="utf-8"))


def test_checked_in_export_is_current():
    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        f"{OUTPUT.name} has drifted from the skills — run: python -m scripts.build_openwebui_model"
    )


def test_import_shape(export):
    # Open WebUI's importer iterates a top-level array and posts each entry as a
    # ModelForm; an object at the root silently imports nothing.
    assert isinstance(export, list) and len(export) == 1
    model = export[0]
    for field in ("id", "name", "base_model_id", "meta", "params", "is_active"):
        assert field in model, f"ModelForm requires {field}"
    assert model["params"]["system"]


def test_tool_server_is_bound(export):
    # An empty or misspelled toolIds imports a model that answers E&O questions
    # with no data at all — silently, since Open WebUI drops ids it cannot
    # resolve. This is the exact string the ISAP instance uses, em dash included.
    assert export[0]["meta"]["toolIds"] == ["API Hub — E&O"]


def test_base_model_is_an_obvious_placeholder(export):
    # A plausible-looking id imports cleanly and fails at the first message, which
    # is a worse failure than one the user is told to fix before importing.
    assert export[0]["base_model_id"] == "CHANGE_ME"


@pytest.mark.parametrize("name", SKILL_ORDER)
def test_every_skill_body_survives_into_the_prompt(name, export):
    prompt = export[0]["params"]["system"]
    source = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    body = source.split("---\n", 2)[-1]
    # Headings are demoted and cross-references rewritten, so compare on the prose
    # lines that neither transform touches.
    plain = [
        line
        for line in body.splitlines()
        if line.strip() and not line.startswith("#") and "[[" not in line and "$ARGUMENTS" not in line
    ]
    assert len(plain) > 20, f"{name}: nothing left to compare — the skill's shape changed"
    missing = [line for line in plain if line not in prompt]
    assert not missing, f"{name}: {len(missing)} line(s) absent from the export, first: {missing[0]!r}"


def test_prompt_forbids_answering_without_a_tool_call(export):
    # The knowledge base reads like a reference document, which is exactly what
    # invites a model to answer from it. Rule zero is what stops that.
    prompt = export[0]["params"]["system"]
    assert "Rule zero" in prompt
    assert "Never answer an E&O question without calling at least one" in prompt
    for expected in ("eo_max_frozen_date", "eo_dimensions", "Never estimate"):
        assert expected in prompt, f"rule zero lost its {expected} clause"


def test_prompt_tells_the_model_it_has_no_shell(export):
    # The skills hand out `python -m scripts...` commands. Without this the model
    # claims to have run them and invents the output.
    assert "cannot run shell commands" in export[0]["params"]["system"]
