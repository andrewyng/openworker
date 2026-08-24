"""Golden decision table — freezes the PermissionEngine's verdict for a fixed set of
(mode, tool, arguments, grants) situations.

The point of this test is regression detection: any change to `permissions.py` /
`risk.py` shows up as a diff in exactly the rows it was meant to change. Rows whose
`note` starts with BASELINE-WRONG or BASELINE-ANNOYING record *today's* behaviour on
purpose — they document known gaps (see `ocw-context/docs/reviewed-auto-mode.md` Part 3),
and a PR that fixes one flips its row here as the visible proof.

The matrix lives in `tests/corpora/decision_matrix.csv`. Each row builds a fresh
`PermissionEngine`; relative paths resolve against a per-row temp workspace.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coworker.permissions import Mode, PermissionEngine

_MATRIX = Path(__file__).parent / "corpora" / "decision_matrix.csv"


def _meta(kind: str):
    """The `meta` column → a stand-in aisuite ToolMetadata (only the fields classify()
    and evaluate() read). Empty → None (built-ins classify by name)."""
    kind = (kind or "").strip()
    if not kind:
        return None
    if kind == "external":
        return SimpleNamespace(requires_approval=True, category="connector")
    if kind == "read":
        return SimpleNamespace(requires_approval=False, category="connector")
    raise ValueError(f"unknown meta kind: {kind!r}")


def _pipes(value: str) -> list[str]:
    return [v for v in (value or "").split("|") if v]


def _verdict(decision) -> str:
    if decision.allowed and not decision.needs_user:
        return "allow"
    if decision.needs_user:
        return "ask"
    return "deny"


def _rows() -> list[dict]:
    with open(_MATRIX, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _build_engine(row: dict, workspace: Path) -> PermissionEngine:
    kwargs: dict = {
        "workspace_root": workspace,
        "mode": Mode(row["mode"]),
        "allowed_commands": _pipes(row.get("allowed_commands", "")),
        "auto_allow_tools": set(_pipes(row.get("auto_allow", ""))),
    }
    eng = PermissionEngine(**kwargs)
    for tool in _pipes(row.get("session_tools", "")):
        eng.allow_tool_for_session(tool)
    for cmd in _pipes(row.get("session_commands", "")):
        eng.allow_command_for_session(cmd)
    standing = (row.get("standing") or "").strip()
    if standing:
        tool, _, target = standing.partition(" ")
        eng.task_rules.setdefault(tool, set()).add(target.strip())
    # allowed_domains: applied only if the engine supports it (arrives with PR1).
    domains = _pipes(row.get("allowed_domains", ""))
    if domains and hasattr(eng, "allowed_domains"):
        eng.allowed_domains = list(domains)
    return eng


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["id"])
def test_decision_matrix(row, tmp_path):
    eng = _build_engine(row, tmp_path)
    args = json.loads(row["args"]) if row.get("args") else {}
    decision = eng.evaluate(row["tool"], args, _meta(row.get("meta", "")))
    got = _verdict(decision)
    assert got == row["expected"], (
        f"{row['id']}: expected {row['expected']}, got {got} "
        f"(reason: {decision.reason!r}) — note: {row.get('note', '')}"
    )
