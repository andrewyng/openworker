"""User-local risk overrides — relax (or tighten) a tool's risk class — and, since
OPE-136, per-tool TRUST rules.

``rules`` relax or tighten a third-party (plugin) tool's risk class by glob; the most
specific rule wins. MCP tools cannot be reclassified (the floor in ``risk.classify``);
their sanctioned lever is a ``trust`` rule instead: *waive the approval card for this
tool* — nothing else. A trusted tool stays EXTERNAL: read-only modes still deny it, the
Auto-approve reviewer still judges it, and the audit trail still records it. One store,
two rule types, one loader — deliberately NOT a second file (the architecture review
rejected a parallel trust store as yet another labeling system).

**Inviolable rule: this store is user-local and is NEVER written by a persona/package.** A
persona can declare what tools it wants, but only the user decides how much to trust them — so
the persona-loading path never touches this file (see ``PERMISSIONS-AND-INBOX.md``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable, Optional

from .risk import RiskClass


@dataclass
class _Rule:
    pattern: str
    risk: RiskClass


def _specificity(pattern: str) -> int:
    """More literal (non-wildcard) characters = more specific; an exact pattern beats any glob."""
    literal = sum(1 for c in pattern if c not in "*?[]")
    exact = 0 if any(c in pattern for c in "*?[") else 1000
    return literal + exact


class RiskOverrideStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        # Rules refused at load with the reason why — surfaced to the user instead of
        # silently shaping permissions differently than their file says.
        self.rejected: list[tuple[str, str]] = []  # (pattern, reason)
        # OPE-136 trust rules: exact tool names (the card writes exact names — a button
        # grants precisely what its card showed; globs stay a hand-editing power path).
        self._trust: list[str] = []
        self._rules: list[_Rule] = self._load()

    def _load(self) -> list[_Rule]:
        if not (self.path and self.path.is_file()):
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # Trust entries: {"pattern": "..."} dicts (the written form) or bare strings.
        seen: set[str] = set()
        for entry in data.get("trust", []) or []:
            pattern = (
                str(entry.get("pattern", "")) if isinstance(entry, dict) else str(entry)
            )
            if pattern and pattern not in seen:
                seen.add(pattern)
                self._trust.append(pattern)
        rules = []
        for r in data.get("rules", []):
            try:
                rule = _Rule(str(r["pattern"]), RiskClass(str(r["risk"])))
            except (KeyError, ValueError):
                continue  # skip malformed rules rather than failing the whole store
            # OPE-136: an explicitly MCP-targeting rule may not sink a tool below
            # EXTERNAL — the floor in risk.classify would silently ignore it anyway,
            # and a rule that reads one way in the file but acts another is worse than
            # a refused rule. (Generic globs that merely HAPPEN to match mcp__ names
            # load normally; the classify floor neutralizes the loosening for those.)
            if rule.pattern.startswith("mcp__") and rule.risk in (
                RiskClass.READ,
                RiskClass.EGRESS,
            ):
                self.rejected.append(
                    (
                        rule.pattern,
                        "MCP tools cannot be reclassified below external "
                        "(OPE-136) — use a trust rule to stop the asking",
                    )
                )
                continue
            rules.append(rule)
        return rules

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "rules": [
                        {"pattern": r.pattern, "risk": r.risk.value}
                        for r in self._rules
                    ],
                    "trust": [{"pattern": p} for p in self._trust],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def set_rule(self, pattern: str, risk: RiskClass | str) -> None:
        """Add/replace a user override (the everyday path writes this from the approval UI).

        Refuses what `_load` refuses (OPE-136): an explicitly MCP-targeting rule below
        EXTERNAL would be written now and silently dropped on the next load — a rule
        that works for one session and then vanishes is a trap, so it never lands."""
        risk = RiskClass(risk) if not isinstance(risk, RiskClass) else risk
        if pattern.startswith("mcp__") and risk in (RiskClass.READ, RiskClass.EGRESS):
            raise ValueError(
                "MCP tools cannot be reclassified below external (OPE-136) — "
                "use a trust rule to stop the asking"
            )
        self._rules = [r for r in self._rules if r.pattern != pattern]
        self._rules.append(_Rule(pattern, risk))
        self.save()

    def resolve(self, tool_name: str) -> Optional[RiskClass]:
        best: Optional[RiskClass] = None
        best_score = -1
        for r in self._rules:
            if fnmatchcase(tool_name, r.pattern):
                score = _specificity(r.pattern)
                if score > best_score:
                    best, best_score = r.risk, score
        return best

    def resolver(self) -> Callable[[str], Optional[RiskClass]]:
        """A callable for ``PermissionEngine.risk_overrides`` / ``risk.classify``."""
        return self.resolve

    # -- OPE-136 trust rules (waive the card; never reclassify) ---------------------
    def trusted(self, tool_name: str) -> bool:
        """Whether a standing trust rule covers this tool (glob-matched, like risk rules)."""
        return any(fnmatchcase(tool_name, p) for p in self._trust)

    def set_trust(self, pattern: str) -> None:
        """Mint a trust rule (the approval card's "Always allow this tool" writes an
        EXACT name — a button grants precisely what its card showed, nothing wider)."""
        if not pattern:
            return
        if pattern not in self._trust:
            self._trust.append(pattern)
            self.save()

    def revoke_trust(self, pattern: str) -> None:
        before = len(self._trust)
        self._trust = [p for p in self._trust if p != pattern]
        if len(self._trust) != before:
            self.save()

    def trust_patterns(self) -> list[str]:
        return list(self._trust)
