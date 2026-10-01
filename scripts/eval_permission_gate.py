"""Replay the deterministic permission-gate corpus through the real policy engine.

Unlike ``eval_reviewer``, this harness is hermetic: it executes no tools and uses no model
or network service.  A run fails only when production no longer matches
``expected_current``.  Differences from ``expected_secure`` remain visible as remediation
work without turning acknowledged policy gaps into regression failures.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

# Allow ``python scripts/eval_permission_gate.py`` as well as ``-m`` execution.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coworker.permissions import Decision, Mode, PermissionEngine  # noqa: E402
from coworker.risk import RiskClass  # noqa: E402
from scripts.validate_layered_corpora import (  # noqa: E402
    ValidationError,
    load_jsonl,
    production_tools,
    validate_rows,
)

CORPUS_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "corpora" / "permission_gate.jsonl"
)

_LIST_FIELDS = {
    "allowed_commands",
    "allowed_domains",
    "auto_allow_tools",
    "session_allow_commands",
    "session_allow_domains",
    "session_allow_tools",
}
_ENGINE_FIELDS = _LIST_FIELDS | {
    "risk_overrides",
    "session_readonly",
    "task_rules",
}
_SETUP_FIELDS = {"roots", "permission_engine"}


@dataclass(frozen=True)
class GateRowResult:
    id: str
    actual: str
    expected_current: str
    expected_secure: str
    reason: str
    known_gap: bool
    failure_point: str


@dataclass(frozen=True)
class PermissionGateReport:
    results: tuple[GateRowResult, ...]

    @property
    def row_count(self) -> int:
        return len(self.results)

    @property
    def current_mismatches(self) -> tuple[GateRowResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.actual != result.expected_current
        )

    @property
    def secure_differences(self) -> tuple[GateRowResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.actual != result.expected_secure
        )

    @property
    def passed(self) -> bool:
        return not self.current_mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.row_count,
            "actual_labels": dict(Counter(result.actual for result in self.results)),
            "current_mismatches": [asdict(result) for result in self.current_mismatches],
            "secure_differences": [asdict(result) for result in self.secure_differences],
            "passed": self.passed,
        }


def decision_label(decision: Decision) -> str:
    """Translate a production decision into the corpus's four-outcome vocabulary."""
    if decision.allowed:
        return "allow_without_reviewer"
    if decision.needs_user:
        return "human_only" if decision.human_only else "reviewer_eligible"
    return "hard_deny"


def _string_list(value: Any, *, where: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{where} must be a list of strings")
    return value


def _engine_kwargs(row: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    row_id = str(row.get("id", "<unknown>"))
    setup = row.get("setup")
    if not isinstance(setup, dict):
        raise ValidationError(f"{row_id}: setup must be an object")
    unknown_setup = sorted(set(setup) - _SETUP_FIELDS)
    if unknown_setup:
        raise ValidationError(
            f"{row_id}: unknown setup fields: {', '.join(unknown_setup)}"
        )
    roots = setup.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ValidationError(f"{row_id}: setup.roots must be a non-empty list of objects")
    for index, root in enumerate(roots):
        where = f"{row_id}: setup.roots[{index}]"
        if not isinstance(root, dict):
            raise ValidationError(f"{where} must be an object")
        if set(root) - {"path", "writable", "label"}:
            raise ValidationError(f"{where} has unknown fields")
        if not isinstance(root.get("path"), str) or not root["path"]:
            raise ValidationError(f"{where}.path must be a non-empty string")
        if "writable" in root and not isinstance(root["writable"], bool):
            raise ValidationError(f"{where}.writable must be a boolean")
        if "label" in root and not isinstance(root["label"], str):
            raise ValidationError(f"{where}.label must be a string")
    workspace = roots[0]["path"]

    raw = setup.get("permission_engine", {})
    if not isinstance(raw, dict):
        raise ValidationError(f"{row_id}: setup.permission_engine must be an object")
    unknown = sorted(set(raw) - _ENGINE_FIELDS)
    if unknown:
        raise ValidationError(
            f"{row_id}: unknown permission_engine fields: {', '.join(unknown)}"
        )

    kwargs: dict[str, Any] = {"roots": roots}
    for field in _LIST_FIELDS:
        if field in raw:
            values = _string_list(raw[field], where=f"{row_id}: {field}")
            kwargs[field] = (
                values
                if field in {"allowed_commands", "allowed_domains"}
                else set(values)
            )

    if "session_readonly" in raw:
        if not isinstance(raw["session_readonly"], bool):
            raise ValidationError(f"{row_id}: session_readonly must be a boolean")
        kwargs["session_readonly"] = raw["session_readonly"]

    if "task_rules" in raw:
        rules = raw["task_rules"]
        if not isinstance(rules, dict) or any(not isinstance(tool, str) for tool in rules):
            raise ValidationError(f"{row_id}: task_rules must map tool names to target lists")
        kwargs["task_rules"] = {
            tool: set(_string_list(targets, where=f"{row_id}: task_rules.{tool}"))
            for tool, targets in rules.items()
        }

    if "risk_overrides" in raw:
        overrides = raw["risk_overrides"]
        if not isinstance(overrides, dict) or any(
            not isinstance(tool, str) or not isinstance(risk, str)
            for tool, risk in overrides.items()
        ):
            raise ValidationError(f"{row_id}: risk_overrides must map tool names to risks")
        try:
            parsed = {tool: RiskClass(risk) for tool, risk in overrides.items()}
        except ValueError as exc:
            raise ValidationError(f"{row_id}: invalid risk override: {exc}") from exc
        kwargs["risk_overrides"] = parsed.get

    return Path(workspace), kwargs


def _evaluate_row(row: dict[str, Any]) -> GateRowResult:
    workspace, kwargs = _engine_kwargs(row)
    engine = PermissionEngine(
        workspace_root=workspace,
        mode=Mode(row["mode"]),
        **kwargs,
    )
    action = row["action"]
    arguments = action.get("arguments", {})
    metadata = row.get("metadata", {})
    if not isinstance(arguments, dict):
        raise ValidationError(f"{row['id']}: action.arguments must be an object")
    if not isinstance(metadata, dict):
        raise ValidationError(f"{row['id']}: metadata must be an object")
    decision = engine.evaluate(
        action["tool"], arguments, SimpleNamespace(**metadata)
    )
    return GateRowResult(
        id=row["id"],
        actual=decision_label(decision),
        expected_current=row["expected_current"],
        expected_secure=row["expected_secure"],
        reason=decision.reason,
        known_gap=bool(row.get("known_gap", False)),
        failure_point=str(row.get("failure_point", "")),
    )


def run_permission_gate(path: Path = CORPUS_PATH) -> PermissionGateReport:
    rows = load_jsonl(path)
    validate_rows("permission_gate.jsonl", rows, production_tools())
    return PermissionGateReport(tuple(_evaluate_row(row) for row in rows))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    args = parser.parse_args(argv)
    try:
        report = run_permission_gate(args.corpus)
    except (OSError, ValidationError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "passed": False}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
