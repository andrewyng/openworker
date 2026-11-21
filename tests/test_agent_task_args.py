"""`spawn_agent`'s optional arguments: a model can send null for the label and a
number for the approval timeout, and neither may crash the tool. The arguments are
not schema-validated before they reach it.
"""

from __future__ import annotations

import pytest

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.providers.base import TokenUsage
from coworker.tools import ToolRegistry
from coworker.tools.agent_tasks import agent_task_tools


class QuietProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(
            text="child report: all clear",
            usage=TokenUsage(input=1, output=1, cache_read=0, cache_write=0),
        )

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _tools(tmp_path):
    tools, registry = agent_task_tools(
        workspace=tmp_path, provider=QuietProvider(), model="m"
    )
    reg = ToolRegistry()
    reg.register_all(tools)
    return reg, registry


def test_a_null_label_falls_back_to_the_task(tmp_path):
    reg, registry = _tools(tmp_path)
    out = reg.execute(
        "spawn_agent", {"task": "map the repo", "label": None, "mode": "read_only"}
    )
    assert "error" not in out, out
    task = registry.get(out["task_id"])
    assert task.label == "map the repo"  # the task's first 40 characters


def test_a_zero_approval_timeout_still_gets_the_minimum(tmp_path):
    # 0 is falsy, so the timeout used to be dropped entirely — the approval card
    # then waited forever instead of expiring after the documented minimum.
    reg, registry = _tools(tmp_path)
    out = reg.execute(
        "spawn_agent",
        {"task": "t", "label": "x", "mode": "read_only", "approval_timeout": 0},
    )
    assert "error" not in out, out
    task = registry.get(out["task_id"])
    assert task.approval_timeout == 60


def test_a_non_numeric_approval_timeout_is_an_error_not_a_crash(tmp_path):
    reg, _registry = _tools(tmp_path)
    out = reg.execute(
        "spawn_agent",
        {"task": "t", "label": "x", "mode": "read_only", "approval_timeout": "soon"},
    )
    assert "error" in out, out


def test_a_null_task_is_an_error_not_a_broken_child(tmp_path):
    reg, _registry = _tools(tmp_path)
    out = reg.execute(
        "spawn_agent", {"task": None, "label": "x", "mode": "read_only"}
    )
    assert "error" in out, out
