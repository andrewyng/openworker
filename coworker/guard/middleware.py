"""GuardMiddleware — combine GuardRuleSet, GuardLogger, and PermissionEngine.

The middleware wraps the existing ``PermissionEngine`` (from
``coworker.permissions``) and applies guard-specific rules on top — deny
patterns, allow patterns, and fan-out concurrency limits for subagent tools.
All decisions are logged to the guard log for audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..permissions import PermissionEngine, Decision
from .ruleset import GuardRuleSet, GuardDecision
from .logger import GuardLogger
from .config_loader import GuardConfigLoader


class GuardMiddleware:
    """Layered authorization: base permissions → guard rules → audit log.

    Usage::

        middleware = GuardMiddleware(
            permissions,
            config_path="config/guard.yaml",
            log_path="guard.log",
            agent_id="code",
        )
        decision = middleware.evaluate("run_shell", {"command": "rm -rf /"}, metadata)
    """

    def __init__(
        self,
        permissions: PermissionEngine,
        *,
        config_path: str | Path = "",
        log_path: str | Path = "guard.log",
        ruleset: Optional[GuardRuleSet] = None,
        logger: Optional[GuardLogger] = None,
        agent_id: str = "",
    ):
        self.permissions = permissions
        self.agent_id = agent_id

        self._ruleset = ruleset or GuardRuleSet()
        self._logger = logger or GuardLogger(log_path=log_path)
        self._config_loader: Optional[GuardConfigLoader] = (
            GuardConfigLoader(config_path) if config_path else None
        )

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        metadata: Any = None,
    ) -> Decision:
        """Evaluate a tool call against base permissions, then guard rules.

        Returns a ``Decision`` compatible with the engine's ``_authorize()``
        flow.  On exception, fails *safe* (deny + needs_user) so no dangerous
        call slips through due to a bug in the guard layer.
        """
        try:
            # 1. Hot-reload rules if config changed (optional).
            self._maybe_reload()

            # 2. Base permission decision (read-only mode, path scoping, etc.).
            base_decision = self.permissions.evaluate(
                tool_name, arguments, metadata
            )

            if not base_decision.allowed:
                # Blocked by permissions — log and return unchanged.
                self._logger.log_decision(
                    agent_id=self.agent_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    allowed=False,
                    reason=base_decision.reason,
                    rule="",
                )
                return base_decision

            # 3. Guard rule check (deny patterns, fan-out limits).
            guard_decision = self._ruleset.evaluate(tool_name, arguments)

            if not guard_decision.allowed:
                # Blocked by guard rule.
                self._logger.log_decision(
                    agent_id=self.agent_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    allowed=False,
                    reason=guard_decision.reason,
                    rule=guard_decision.rule,
                )
                return Decision(
                    allowed=False,
                    reason=f"guard: {guard_decision.reason}",
                    needs_user=guard_decision.needs_user,
                    rule=guard_decision.rule,
                )

            # 4. Allowed by both layers.
            self._logger.log_decision(
                agent_id=self.agent_id,
                tool_name=tool_name,
                arguments=arguments,
                allowed=True,
                reason=base_decision.reason,
                rule=guard_decision.rule,
            )
            return base_decision

        except Exception as exc:
            # Fail safe: block the call and flag it for user attention.
            try:
                self._logger.log_decision(
                    agent_id=self.agent_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    allowed=False,
                    reason=f"guard middleware error: {exc}",
                    rule="",
                    needs_user=True,
                )
            except Exception:
                pass  # best-effort: logging must not mask the safety decision
            return Decision(
                allowed=False,
                reason=f"guard middleware error: {exc}",
                needs_user=True,
            )

    # ------------------------------------------------------------------
    # Fan-out tracking
    # ------------------------------------------------------------------

    def track_start(self, tool_name: str) -> bool:
        """Try to start tracking *tool_name*.

        Returns ``True`` if the tool was allowed to start (counter incremented),
        or ``False`` if the fan-out limit would be exceeded.

        When returning ``False`` the decision is logged for audit so the
        blocked call is traceable.
        """
        allowed = self._ruleset.track_start(tool_name)
        if not allowed:
            self._logger.log_decision(
                agent_id=self.agent_id,
                tool_name=tool_name,
                arguments={},
                allowed=False,
                reason="fanout limit exceeded during track_start",
                rule="",
            )
        return allowed

    def track_end(self, tool_name: str) -> None:
        """Notify the rule set that *tool_name* has finished executing."""
        self._ruleset.track_end(tool_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_reload(self) -> None:
        """If a config file is configured and has changed, reload rules."""
        if self._config_loader is None:
            return
        if not self._config_loader.has_changed():
            return
        # Reload the ruleset from the config path.
        self._ruleset = GuardRuleSet.load_rules(self._config_loader.config_path)
