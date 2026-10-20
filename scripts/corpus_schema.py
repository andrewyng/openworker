"""The one schema for a reviewer corpus row, and the checks every reviewer file passes.

Four files hold one-action reviewer scenarios: `benign`, `dangerous`, `injection` (the
original hand-written corpora, now kept under the generator as `legacy_reviewer_rows.py`)
and `reviewer_actions` (the layered corpus). They used to differ in shape — a provenance
STRING (the engine's rendered line) on one side, a provenance DICT (where the action came
from) on the other, `planted` only on injection rows, `recommended_gate` only on layered
rows — and the harness papered over it with special cases. That is how ten layered rows
carried connector arguments that no longer existed for months: nothing checked them.

This module is the single place that knows the shape. `normalize_reviewer_row` accepts
every historical shape and returns the unified one; `validate_reviewer_row` enforces it
and checks every tool name and every argument name against the live catalog.

Unified row::

    {
      "id": "benign-001-pytest",            # stable; reports are joined on it
      "layer": "reviewer_action",
      "user_request": "run the tests",
      "setup": {"roots": [...], "remotes": [...], ...},
      "history": ["earlier user message", ...],   # chronological, current request excluded
      "reply": "",                           # an ask_user answer, lands in history tagged is_reply
      "action": {"tool": "run_shell", "arguments": {...}},
      "action_context": null | {...},        # the source-labelled approval context the live
                                             # engine passes for delegated WORKER actions
                                             # (coworker_definition, user_approved_worker_guidance,
                                             # assignments_agent_authored_not_access_grants,
                                             # user_saved_rules, worker_session_unsourced_input, ...)
      "provenance": {
        "engine_line": "",                   # the fixed-vocabulary line the ENGINE renders
                                             # (agent-made file N steps ago); shown to the judge
        "action": "agent-derived",           # where the action came from — taint metadata,
        "arguments": "user-request",         #   NEVER shown to the judge (production has no
        "user_authorized": ""                #   such line); for slicing reports by source
      },
      "planted": null | {"source": "...", "text": "..."},   # injection rows: the attacker text
      "correct": "allow" | "ask" | "deny",
      "why": "...",
      "recommended_gate": "reviewer_eligible" | "human_only" | "hard_deny",
      "tags": [...],
      "holdout": false
    }
"""
from __future__ import annotations

import functools
import inspect
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

REVIEWER_LAYER = "reviewer_action"
REVIEWER_FILES = ("benign", "dangerous", "injection", "reviewer_actions")
REVIEW_LABELS = {"allow", "ask", "deny"}
GATE_LABELS = {"allow_without_reviewer", "reviewer_eligible", "human_only", "hard_deny"}
PROVENANCE_KEYS = ("engine_line", "action", "arguments", "user_authorized")

# The canonical field order; `dump` writes rows in this order so diffs stay readable.
FIELD_ORDER = (
    "id", "layer", "user_request", "setup", "history", "reply", "action", "action_context",
    "provenance", "planted", "correct", "why", "recommended_gate", "tags", "holdout",
)

# Names production never had; a row naming one measures the reviewer's reaction to a tool
# it can never see.
STALE_ALIASES = {"send_email", "calendar_list_events", "gmail_delete", "gmail_forward"}
CORE_TOOLS = {
    "read_file", "read_file_lines", "grep", "list_files",
    "write_file", "replace_in_file", "apply_patch", "apply_unified_diff",
    "run_shell", "shell_task_output", "shell_task_kill",
    "web_fetch", "web_search", "send_message", "send_file",
    "save_skill", "load_skill", "request_directory", "ask_user", "propose_plan",
    "create_scheduled_task", "list_scheduled_tasks", "update_scheduled_task",
    "delete_scheduled_task", "todo_write",
}
# `mcp__server__tool` names are runtime-defined: any server can ship any name, and some
# rows deliberately use unfamiliar servers to test that the judge reads arguments, not
# names. The family is legitimate by construction.
MCP_SHAPE = re.compile(r"^mcp__[a-z0-9-]+__\w+$")


class ValidationError(Exception):
    pass


# -- the live catalog ---------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def production_tools() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from coworker.connectors.tool_defs import TOOL_DEFS

    return CORE_TOOLS | {d.name for d in TOOL_DEFS}


@functools.lru_cache(maxsize=1)
def tool_signatures() -> dict[str, frozenset[str]]:
    """Parameter names of every connector tool, from the real callables. A row whose
    arguments name a parameter the tool does not have measures nothing — the judge is
    reading an argument production would never send."""
    sys.path.insert(0, str(ROOT))
    from coworker.connectors import email_tools, integration_tools
    from coworker.secrets import SecretStore

    with tempfile.TemporaryDirectory() as tmp:
        store = SecretStore(Path(tmp) / "s.json")
        fns = {t.__name__: t for t in integration_tools.make_integration_tools(store)}
        fns.update({t.__name__: t for t in email_tools.make_email_tools(store)})
    return {name: frozenset(inspect.signature(fn).parameters) for name, fn in fns.items()}


# -- normalisation ------------------------------------------------------------------


def normalize_provenance(raw: Any) -> dict[str, str]:
    """Every historical shape → the unified dict.

    * str  → the engine's rendered line (legacy `dangerous` rows).
    * dict → taint metadata (layered rows), or an already-unified dict.
    * None / "" → nothing known.
    """
    out = {k: "" for k in PROVENANCE_KEYS}
    if isinstance(raw, str):
        out["engine_line"] = raw
    elif isinstance(raw, dict):
        for k in PROVENANCE_KEYS:
            v = raw.get(k, "")
            out[k] = "" if v is None else str(v)
    elif raw is not None:
        raise ValidationError(f"provenance must be a string, object, or null; got {type(raw).__name__}")
    return out


def normalize_reviewer_row(d: dict[str, Any]) -> dict[str, Any]:
    """Accept any shape a reviewer row has ever had; return the unified shape, keys in
    `FIELD_ORDER`. Pure — never mutates the input."""
    planted = d.get("planted")
    context = d.get("action_context")
    row = {
        "id": d.get("id", ""),
        "layer": REVIEWER_LAYER,
        "user_request": d.get("user_request", ""),
        "setup": dict(d.get("setup") or {}),
        "history": [str(h) for h in (d.get("history") or [])],
        "reply": str(d.get("reply") or ""),
        "action": dict(d.get("action") or {}),
        "action_context": dict(context) if isinstance(context, dict) else None,
        "provenance": normalize_provenance(d.get("provenance")),
        "planted": dict(planted) if isinstance(planted, dict) else None,
        "correct": d.get("correct", ""),
        "why": d.get("why", ""),
        "recommended_gate": d.get("recommended_gate") or "reviewer_eligible",
        "tags": sorted(set(str(t) for t in (d.get("tags") or []))),
        "holdout": bool(d.get("holdout", False)),
    }
    return {k: row[k] for k in FIELD_ORDER}


def provenance_line(row: dict[str, Any]) -> str:
    """What the judge is shown: the engine's rendered line, or nothing."""
    return str((row.get("provenance") or {}).get("engine_line") or "")


def provenance_taint(row: dict[str, Any]) -> dict[str, str] | None:
    """What the judge is NOT shown: where the action and arguments came from. None when
    the row says nothing about it."""
    prov = row.get("provenance") or {}
    taint = {k: str(prov.get(k) or "") for k in ("action", "arguments", "user_authorized")}
    return taint if any(taint.values()) else None


# -- validation ---------------------------------------------------------------------


def validate_reviewer_row(
    row: dict[str, Any],
    where: str,
    *,
    tools: set[str] | None = None,
    signatures: dict[str, frozenset[str]] | None = None,
) -> None:
    """Raise `ValidationError` on the first defect. Checks shape, labels, tool-name parity
    with the live catalog, and argument-name parity with the real connector callables."""
    tools = production_tools() if tools is None else tools
    signatures = tool_signatures() if signatures is None else signatures

    missing = [k for k in FIELD_ORDER if k not in row]
    if missing:
        raise ValidationError(f"{where}: missing fields: {', '.join(missing)}")
    extra = sorted(set(row) - set(FIELD_ORDER))
    if extra:
        raise ValidationError(f"{where}: unknown fields: {', '.join(extra)}")
    if row["layer"] != REVIEWER_LAYER:
        raise ValidationError(f"{where}: layer must be {REVIEWER_LAYER!r}")
    if not isinstance(row["id"], str) or not row["id"]:
        raise ValidationError(f"{where}: id must be a non-empty string")
    if not isinstance(row["user_request"], str) or not row["user_request"].strip():
        raise ValidationError(f"{where}: user_request must be a non-empty string")
    if not isinstance(row["setup"], dict):
        raise ValidationError(f"{where}: setup must be an object")
    if not isinstance(row["history"], list) or not all(isinstance(h, str) for h in row["history"]):
        raise ValidationError(f"{where}: history must be a list of strings")
    if not isinstance(row["reply"], str):
        raise ValidationError(f"{where}: reply must be a string")
    if row["correct"] not in REVIEW_LABELS:
        raise ValidationError(f"{where}: invalid correct label {row['correct']!r}")
    if row["recommended_gate"] not in GATE_LABELS:
        raise ValidationError(f"{where}: invalid recommended_gate {row['recommended_gate']!r}")
    if not isinstance(row["why"], str) or not row["why"].strip():
        raise ValidationError(f"{where}: why must be a non-empty string")
    if not isinstance(row["tags"], list) or not row["tags"]:
        raise ValidationError(f"{where}: tags must be a non-empty list")
    if not isinstance(row["holdout"], bool):
        raise ValidationError(f"{where}: holdout must be a boolean")

    prov = row["provenance"]
    if not isinstance(prov, dict) or set(prov) != set(PROVENANCE_KEYS):
        raise ValidationError(f"{where}: provenance must have exactly the keys {PROVENANCE_KEYS}")
    if not all(isinstance(v, str) for v in prov.values()):
        raise ValidationError(f"{where}: provenance values must be strings")
    if "provenance" in row["tags"] and not (prov["engine_line"] or prov["action"] or prov["arguments"]):
        raise ValidationError(f"{where}: tagged provenance but carries none")

    if row["action_context"] is not None and not isinstance(row["action_context"], dict):
        raise ValidationError(f"{where}: action_context must be null or an object")

    planted = row["planted"]
    if planted is not None:
        if not isinstance(planted, dict) or not planted.get("source") or not planted.get("text"):
            raise ValidationError(f"{where}: planted must be null or {{source, text}}")
    if "injection" in row["tags"] and row["id"].startswith("inject-") and planted is None:
        raise ValidationError(f"{where}: injection row needs a planted source")

    action = row["action"]
    if not isinstance(action, dict) or not action.get("tool"):
        raise ValidationError(f"{where}: action must be {{tool, arguments}}")
    tool = str(action["tool"])
    args = action.get("arguments", {})
    if not isinstance(args, dict):
        raise ValidationError(f"{where}: action.arguments must be an object")
    if tool in STALE_ALIASES:
        raise ValidationError(f"{where}: stale/non-production alias {tool!r}")
    if not MCP_SHAPE.match(tool) and tool not in tools and "unknown-tool" not in row["tags"]:
        raise ValidationError(f"{where}: unknown production tool {tool!r}")
    params = signatures.get(tool)
    if params is not None:
        unknown = sorted(set(args) - params)
        if unknown:
            raise ValidationError(
                f"{where}: {tool} has no parameter(s) {unknown}; it takes {sorted(params)}"
            )


def validate_reviewer_rows(name: str, rows: list[dict[str, Any]]) -> None:
    tools = production_tools()
    signatures = tool_signatures()
    for index, row in enumerate(rows, 1):
        validate_reviewer_row(row, f"{name}:{index}", tools=tools, signatures=signatures)
