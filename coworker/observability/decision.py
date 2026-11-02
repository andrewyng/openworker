"""The decision path of a tool call: how it was allowed or refused, stage by stage
(observability E4, technical design §8.3).

`_authorize` in the engine records each stage it passes through a `DecisionPath`; the
tool step gets `attrs.decision = {path, origin}`. Every value is an id from a fixed list;
rule or reason text never goes here.

Stages, in order: `permission`, `grant`, `auto_approver`, `card`, `resolved`. `origin` names
the single stage that decided.
"""

from __future__ import annotations

from typing import Any, Optional

# Permission engine allow reasons -> grant ids (§8.3). Matched as prefixes.
_GRANTS = (
    ("command on allowlist", "config_allowlist"),
    ("domain on allowlist", "config_allowlist"),
    ("auto-allowed by config", "config_allowlist"),
    ("site on the allowed sites", "config_allowlist"),
    ("already on the allowed sites", "config_allowlist"),
    ("command allowed for session", "session_grant"),
    ("read-only command (session grant)", "session_grant"),
    ("tool allowed for session", "session_grant"),
    ("tool allowed for this request", "run_grant"),
    ("trusted MCP tool (user trust rule)", "trusted_rule"),
    ("trusted MCP tool", "trusted_server"),
    ("allowed by standing rule", "standing_rule"),
)

# Outcomes of an approval card -> the `resolved` stage (§8.3).
_RESOLVED = {
    "once": ("approved_once", None),
    "always_tool": ("approved_session", "always_tool"),
    "always_command": ("approved_session", "always_command"),
    "always_domain": ("approved_session", "always_domain"),
    "always_site": ("approved_session", "always_site"),
    "readonly_session": ("approved_session", "readonly_session"),
    "this_run": ("approved_run", None),
    "always_trust": ("approved_always", None),
    "deny": ("denied", None),
    "superseded": ("superseded", None),
}

# A grant stage that decided a call that ran is its origin.
_GRANT_ORIGINS = {"config_allowlist", "session_grant", "standing_rule", "trusted_rule",
                  "trusted_server", "run_grant", "allow_anyway", "lead_denial",
                  "readonly_tier", "gate_tier"}


class DecisionPath:
    def __init__(self) -> None:
        self.path: list[dict[str, Any]] = []

    def add(self, stage: str, outcome: str, detail: Optional[str] = None) -> None:
        entry: dict[str, Any] = {"stage": stage, "outcome": outcome}
        if detail:
            entry["detail"] = detail
        self.path.append(entry)

    def permission(self, *, allowed: bool, needs_user: bool, human_only: bool, reason: str,
                   tier: Optional[str] = None) -> None:
        """The permission engine's answer, and the grant behind an allow."""
        if allowed:
            if tier:
                # An Auto-Approve gate tier (OPE-233/234) cleared it without the reviewer.
                self.add("permission", "allowed")
                self.add("grant", "readonly_tier" if tier == "readonly_command" else "gate_tier")
                return
            if reason == "low risk":
                self.add("permission", "allowed", "low_risk")
            elif reason == "full access":
                self.add("permission", "allowed", "bypass")
            elif reason.startswith("cleared by mode"):
                self.add("permission", "allowed", "cleared_by_mode")
            else:
                grant = next((g for prefix, g in _GRANTS if reason.startswith(prefix)), None)
                self.add("permission", "allowed")
                if grant:
                    self.add("grant", grant)
            return
        if needs_user:
            self.add("permission", "human_only" if human_only else "needs_user")
        else:
            self.add("permission", "denied")

    def resolved(self, outcome_value: str, *, interrupted: bool = False) -> None:
        if interrupted:
            self.add("resolved", "interrupted")
            return
        outcome, detail = _RESOLVED.get(outcome_value, ("approved_once", None))
        self.add("resolved", outcome, detail)

    def stage(self, name: str) -> Optional[dict[str, Any]]:
        return next((s for s in reversed(self.path) if s["stage"] == name), None)

    def finish(self, *, ran: bool) -> dict[str, Any]:
        """`{path, origin}` for the tool step."""
        return {"path": list(self.path), "origin": self._origin(ran)}

    def _origin(self, ran: bool) -> Optional[str]:
        resolved, reviewer = self.stage("resolved"), self.stage("auto_approver")
        card, grant, perm = self.stage("card"), self.stage("grant"), self.stage("permission")
        if ran:
            if resolved and resolved["outcome"].startswith("approved"):
                return "user"
            if reviewer and reviewer["outcome"] == "allow":
                return "reviewer"
            if grant and grant["outcome"] in _GRANT_ORIGINS:
                return grant["outcome"]
            if perm and perm.get("detail") in ("low_risk", "bypass"):
                return perm["detail"]
            if perm and perm.get("detail") == "cleared_by_mode":
                return "bypass"
            return None
        if reviewer and reviewer["outcome"] == "deny":
            return "reviewer_denied"
        if card and card["outcome"] == "auto_refused":
            return "unattended_auto"
        if resolved:
            return "user"
        if perm and perm["outcome"] == "denied":
            return "denied_by_rule"
        return None
