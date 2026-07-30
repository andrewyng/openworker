"""GuardRuleSet — load and evaluate guard rules from YAML configuration.

Supports allow/deny rules with command match patterns (contains, prefix, regex),
argument key-value matches, and fan-out concurrency limits for subagent tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class GuardRule:
    """A single guard rule evaluated against tool calls."""

    name: str
    tool: str
    action: str  # "allow", "deny", or "limit"
    reason: str = ""

    # Command match patterns (any supplied must all match for the rule to fire)
    match_command_contains: Optional[str] = None
    match_command_prefix: Optional[str] = None
    match_command_regex: Optional[str] = None

    # Key-value arg matcher: tool argument name → expected value (str compare)
    match_args: dict[str, str] = field(default_factory=dict)

    # Fan-out concurrency limit (relevant for subagent/spawn tools)
    max_concurrent: Optional[int] = None


@dataclass
class GuardDecision:
    """Outcome of evaluating a tool call against the rule set."""

    allowed: bool
    reason: str = ""
    rule: str = ""  # name of the matching rule, if any
    needs_user: bool = False  # True → surface should prompt the user for approval
    is_fanout_block: bool = False  # True when the denial was caused by max_concurrent


class GuardRuleSet:
    """Loads guard rules from YAML and evaluates tool calls against them.

    Thread-safe for fan-out counting if used from a single event-loop thread;
    the engine serializes tool-authorization calls, so races on counters are
    not expected in practice.
    """

    def __init__(self, rules: Optional[list[GuardRule]] = None):
        self._rules: list[GuardRule] = list(rules or [])
        # Tool name → number of currently-active (started, not yet finished) calls.
        # Used to enforce fan-out concurrency limits.
        self._fanout_counters: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load_rules(cls, path: str | Path) -> GuardRuleSet:
        """Load rules from a YAML file.

        Expected schema::

            rules:
              - name: block-recursive-delete
                tool: run_shell
                match:
                  command:
                    contains: "rm -rf /"
                action: deny
                reason: "Block recursive delete outside workspace"

        Returns an empty set when the file is missing or empty.
        """
        p = Path(path)
        if not p.is_file():
            return cls()

        with open(p, "r") as f:
            raw = yaml.safe_load(f)

        rules: list[GuardRule] = []
        for entry in (raw or {}).get("rules", []):
            match = entry.get("match") or {}
            command = match.get("command") or {} if isinstance(match, dict) else {}
            fanout = entry.get("fanout") or {} if isinstance(entry, dict) else {}
            args_match = match.get("args") or {} if isinstance(match, dict) else {}

            rules.append(
                GuardRule(
                    name=str(entry.get("name", "unnamed")),
                    tool=str(entry.get("tool", "")),
                    action=str(entry.get("action", "deny")),
                    reason=str(entry.get("reason", "")),
                    match_command_contains=command.get("contains"),
                    match_command_prefix=command.get("prefix"),
                    match_command_regex=command.get("regex"),
                    match_args={
                        str(k): str(v) for k, v in args_match.items()
                    },
                    max_concurrent=fanout.get("max_concurrent"),
                )
            )

        return cls(rules)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> GuardDecision:
        """Check *tool_name* with *arguments* against every registered rule.

        Returns the first rule's decision that matches the call.  When no rule
        matches, returns ``GuardDecision(allowed=True)`` (no opinion).
        """
        command = str(arguments.get("command", ""))

        for rule in self._rules:
            # Tool name filter (empty = match any)
            if rule.tool and rule.tool != tool_name:
                continue

            # --- command match patterns ---
            if rule.match_command_contains and rule.match_command_contains not in command:
                continue
            if rule.match_command_prefix and not command.startswith(rule.match_command_prefix):
                continue
            if rule.match_command_regex and not re.search(rule.match_command_regex, command):
                continue

            # --- argument key-value match ---
            if rule.match_args:
                if not all(
                    str(arguments.get(k)) == str(v)
                    for k, v in rule.match_args.items()
                ):
                    continue

            # --- fan-out concurrency limit ---
            if rule.max_concurrent is not None:
                current = self._fanout_counters.get(tool_name, 0)
                if current >= rule.max_concurrent:
                    # Over the limit — apply the rule's action (deny/limit).
                    return GuardDecision(
                        allowed=False,
                        reason=f"{rule.reason} (max {rule.max_concurrent} concurrent)",
                        rule=rule.name,
                        is_fanout_block=True,
                    )
                # Within limits — this rule doesn't apply; continue to next.
                # A rule with max_concurrent but no other match patterns
                # is purely a fan-out throttle, not a blanket allow/deny.
                continue

            # --- action (only reached when no fan-out limit is set) ---
            if rule.action == "allow":
                return GuardDecision(
                    allowed=True, reason=rule.reason, rule=rule.name
                )
            # "deny" or "limit" → block
            return GuardDecision(
                allowed=False,
                reason=rule.reason or f"blocked by rule: {rule.name}",
                rule=rule.name,
            )

        return GuardDecision(allowed=True)

    # ------------------------------------------------------------------
    # Fan-out tracking
    # ------------------------------------------------------------------

    def track_start(self, tool_name: str) -> bool:
        """Try to start tracking *tool_name*.

        Returns ``True`` if the tool was allowed to start (counter incremented),
        or ``False`` if the fan-out limit would be exceeded (no change).

        Delegates to :meth:`evaluate` so the fan-out check logic stays in one
        place — only fan-out denials ("concurrent" in reason) cause a rejection;
        other deny rules are already enforced at authorization time.
        """
        decision = self.evaluate(tool_name, {})
        if not decision.allowed and decision.is_fanout_block:
            return False
        self._fanout_counters[tool_name] = self._fanout_counters.get(tool_name, 0) + 1
        return True

    def track_end(self, tool_name: str) -> None:
        """Decrement the active-count for *tool_name* (tool finished)."""
        current = self._fanout_counters.get(tool_name, 0)
        if current > 0:
            self._fanout_counters[tool_name] = current - 1
