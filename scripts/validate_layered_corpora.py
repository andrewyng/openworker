"""Validate every Auto-Approve corpus under tests/corpora.

Checks syntax, schema, IDs, labels, holdout splits, coverage tags, tool-name parity with the
production connector catalog and, for the one-action reviewer files, argument-name parity
with the real connector callables (scripts/corpus_schema.py). Exits non-zero on any defect.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "corpora"
sys.path.insert(0, str(ROOT))

from scripts.corpus_schema import (  # noqa: E402
    GATE_LABELS,
    REVIEW_LABELS,
    STALE_ALIASES,
    ValidationError,
    production_tools,
    validate_reviewer_rows,
)

# name → (layer, minimum rows). The reviewer files share one schema; the gate and sequence
# files have their own, checked below.
FILES = {
    "benign.jsonl": ("reviewer_action", 40),
    "dangerous.jsonl": ("reviewer_action", 30),
    "injection.jsonl": ("reviewer_action", 20),
    "reviewer_actions.jsonl": ("reviewer_action", 120),
    "permission_gate.jsonl": ("permission_gate", 120),
    "action_sequences.jsonl": ("action_sequence", 60),
}
MODES = {"discuss", "plan", "interactive", "custom", "auto-approve", "bypass-approvals"}
# Coverage tags each file must span. The legacy reviewer files predate the tag vocabulary
# and are not held to it; the layered file is.
REQUIRED_TAGS = {
    "permission_gate.jsonl": {"exec", "outside-root", "credentials", "environment", "self-protection", "egress", "persistence", "privilege", "browser", "mcp", "connector", "persistent-authority"},
    "reviewer_actions.jsonl": {"exec", "egress", "connector", "browser", "transformed-injection", "explicit-danger", "wrong-destination", "production-tool", "persistent-authority", "judge-injection", "judge-injection-control", "multi-turn", "reply-context", "worker-context", "long-context"},
    "action_sequences.jsonl": {"injection", "cross-connector", "benign-control", "persistence", "browser", "exfiltration", "automation"},
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path.name}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValidationError(f"{path.name}:{line_no}: row must be an object")
        rows.append(row)
    return rows


def require(row: dict[str, Any], fields: set[str], where: str) -> None:
    missing = sorted(k for k in fields if k not in row)
    if missing:
        raise ValidationError(f"{where}: missing fields: {', '.join(missing)}")


def action_tools(row: dict[str, Any]) -> list[str]:
    if row.get("layer") == "action_sequence":
        actions = row.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValidationError(f"{row.get('id')}: actions must be a non-empty list")
        return [str(a.get("tool", "")) for a in actions if isinstance(a, dict)]
    action = row.get("action")
    if not isinstance(action, dict):
        raise ValidationError(f"{row.get('id')}: action must be an object")
    return [str(action.get("tool", ""))]


def validate_rows(name: str, rows: list[dict[str, Any]], tools: set[str]) -> None:
    expected_layer, minimum = FILES[name]
    if len(rows) < minimum:
        raise ValidationError(f"{name}: expected at least {minimum} rows, found {len(rows)}")
    holdouts = sum(bool(r.get("holdout")) for r in rows)
    if holdouts == 0 or holdouts == len(rows):
        raise ValidationError(f"{name}: requires both holdout and non-holdout rows")

    labels: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    for index, row in enumerate(rows, 1):
        where = f"{name}:{index}"
        require(row, {"id", "layer", "user_request", "setup", "tags", "holdout"}, where)
        if row["layer"] != expected_layer:
            raise ValidationError(f"{where}: expected layer {expected_layer!r}")
        if not isinstance(row["id"], str) or not row["id"]:
            raise ValidationError(f"{where}: id must be a non-empty string")
        if not isinstance(row["tags"], list) or not row["tags"]:
            raise ValidationError(f"{where}: tags must be a non-empty list")
        tags.update(str(t) for t in row["tags"])

        if expected_layer == "permission_gate":
            require(row, {"mode", "action", "expected_current", "expected_secure", "why"}, where)
            if row["mode"] not in MODES:
                raise ValidationError(f"{where}: invalid mode {row['mode']!r}")
            for field in ("expected_current", "expected_secure"):
                if row[field] not in GATE_LABELS:
                    raise ValidationError(f"{where}: invalid {field} {row[field]!r}")
            if row["expected_current"] != row["expected_secure"]:
                if row.get("known_gap") is not True or not row.get("failure_point"):
                    raise ValidationError(f"{where}: differing expectations require known_gap and failure_point")
            labels.update([row["expected_current"], row["expected_secure"]])
        elif expected_layer == "reviewer_action":
            # Shape, labels, tool and argument parity: all in validate_reviewer_rows below.
            labels.update([row.get("correct", "")])
        else:
            require(row, {"observations", "actions", "correct", "why"}, where)
            if row["correct"] not in REVIEW_LABELS:
                raise ValidationError(f"{where}: invalid correct label {row['correct']!r}")
            if not isinstance(row["observations"], list):
                raise ValidationError(f"{where}: observations must be a list")
            labels.update([row["correct"]])

        if expected_layer != "reviewer_action":
            for tool in action_tools(row):
                if not tool:
                    raise ValidationError(f"{where}: action has no tool name")
                if tool in STALE_ALIASES:
                    raise ValidationError(f"{where}: stale/non-production alias {tool!r}")
                if tool not in tools and "unknown-tool" not in row["tags"]:
                    raise ValidationError(f"{where}: unknown production tool {tool!r}")

    if expected_layer == "reviewer_action":
        validate_reviewer_rows(name, rows)

    if expected_layer == "permission_gate":
        missing_labels = GATE_LABELS - set(labels)
    elif name == "reviewer_actions.jsonl" or expected_layer == "action_sequence":
        missing_labels = REVIEW_LABELS - set(labels)
    else:
        missing_labels = set()  # a legacy file is one key by design (benign = all allow)
    if missing_labels:
        raise ValidationError(f"{name}: missing labels {sorted(missing_labels)}")
    missing_tags = REQUIRED_TAGS.get(name, set()) - set(tags)
    if missing_tags:
        raise ValidationError(f"{name}: missing required coverage tags {sorted(missing_tags)}")


def validate_all() -> dict[str, Any]:
    tools = production_tools()
    seen: set[str] = set()
    summary: dict[str, Any] = {}
    for name in FILES:
        rows = load_jsonl(CORPUS_DIR / name)
        validate_rows(name, rows, tools)
        for row in rows:
            rid = row["id"]
            if rid in seen:
                raise ValidationError(f"duplicate id across corpora: {rid}")
            seen.add(rid)
        label_field = "expected_secure" if row["layer"] == "permission_gate" else "correct"
        summary[name] = {
            "rows": len(rows),
            "holdout": sum(bool(r.get("holdout")) for r in rows),
            "labels": dict(Counter(str(r[label_field]) for r in rows)),
            "tools": len(set(t for r in rows for t in action_tools(r))),
            "tags": len(set(str(tag) for r in rows for tag in r["tags"])),
        }
    summary["total"] = sum(v["rows"] for v in summary.values())
    return summary


def main() -> int:
    try:
        summary = validate_all()
    except (OSError, ValidationError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
