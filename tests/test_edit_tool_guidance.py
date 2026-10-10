"""OPE-186 change 2: the file tools tell the model WHEN to use each (rewrite vs edit in
place), and the Cowork prompt says the same. The schema the model sees is built from the
tool's docstring by the registry, so that is what is checked here."""

from __future__ import annotations

from coworker.agents import AgentContext
from coworker.agents.cowork import COWORK_INSTRUCTIONS
from coworker.catalog import EDIT_TOOL_GUIDANCE, expand
from coworker.tools import ToolRegistry


def _descriptions(capability: str, tmp_path) -> dict[str, str]:
    context = AgentContext(workspace=str(tmp_path), executor=None, todo=None, roots=None)
    registry = ToolRegistry()
    registry.register_all(expand([capability], context))
    return {
        name: registry.get(name).schema["function"]["description"]  # type: ignore[union-attr]
        for name in registry.names()
    }


def test_files_capability_describes_when_to_rewrite_and_when_to_edit(tmp_path):
    d = _descriptions("files", tmp_path)
    assert "NEW file" in d["write_file"] and "replace_in_file" in d["write_file"]
    assert "in place" in d["replace_in_file"] and "Prefer this over write_file" in d["replace_in_file"]
    assert "*** Begin Patch" in d["apply_patch"]
    assert "unified diff" in d["apply_unified_diff"]
    # The generic aisuite one-liner is gone from every edit tool.
    for name in EDIT_TOOL_GUIDANCE:
        assert d[name] != "Write a UTF-8 text file under the configured root."


def test_code_files_capability_gets_the_same_guidance(tmp_path):
    d = _descriptions("code_files", tmp_path)
    for name, text in EDIT_TOOL_GUIDANCE.items():
        assert d[name] == text


def test_cowork_prompt_steers_edits_to_in_place_tools():
    assert "edit it in place with replace_in_file or apply_patch" in COWORK_INSTRUCTIONS
    assert "reserve write_file for new files or full rewrites" in COWORK_INSTRUCTIONS
