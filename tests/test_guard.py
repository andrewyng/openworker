"""Tests for the guard middleware — GuardRuleSet, GuardLogger, GuardConfigLoader,
and GuardMiddleware integration.

Follows the same patterns as test_permissions_risk.py: parametrize, tmp_path for
file operations, SimpleNamespace for metadata.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from coworker.guard.config_loader import GuardConfigLoader
from coworker.guard.logger import GuardLogger
from coworker.guard.middleware import GuardMiddleware
from coworker.guard.ruleset import GuardDecision, GuardRule, GuardRuleSet
from coworker.permissions import Mode, PermissionEngine


# =============================================================================
# GuardRuleSet
# =============================================================================


class TestGuardRuleSet:
    def test_empty_ruleset_allows_all(self):
        rset = GuardRuleSet()
        d = rset.evaluate("any_tool", {})
        assert d.allowed
        assert not d.reason

    def test_deny_rule_blocks_by_tool_name(self):
        rset = GuardRuleSet(
            [GuardRule(name="block-test", tool="dangerous_tool", action="deny")]
        )
        d = rset.evaluate("dangerous_tool", {})
        assert not d.allowed
        assert d.rule == "block-test"

    def test_deny_rule_does_not_affect_other_tools(self):
        rset = GuardRuleSet(
            [GuardRule(name="block-test", tool="dangerous_tool", action="deny")]
        )
        d = rset.evaluate("safe_tool", {})
        assert d.allowed

    def test_allow_rule_allows_tool(self):
        rset = GuardRuleSet(
            [GuardRule(name="allow-safe", tool="safe_tool", action="allow")]
        )
        d = rset.evaluate("safe_tool", {})
        assert d.allowed
        assert d.rule == "allow-safe"

    def test_command_contains_match(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="block-rmrf",
                    tool="run_shell",
                    action="deny",
                    match_command_contains="rm -rf ",
                )
            ]
        )
        assert not rset.evaluate("run_shell", {"command": "rm -rf /"}).allowed
        assert not rset.evaluate("run_shell", {"command": "rm -rf ~"}).allowed
        # Not blocked: different command
        assert rset.evaluate("run_shell", {"command": "ls -la"}).allowed

    def test_command_prefix_match(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="allow-git-status",
                    tool="run_shell",
                    action="allow",
                    match_command_prefix="git status",
                )
            ]
        )
        assert rset.evaluate("run_shell", {"command": "git status"}).allowed
        assert rset.evaluate("run_shell", {"command": "git status -s"}).allowed
        # Not matched: different prefix
        assert rset.evaluate("run_shell", {"command": "git push"}).allowed
        # Not matched: not the right tool
        assert rset.evaluate("read_file", {"path": "x"}).allowed

    def test_command_regex_match(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="allow-git-read-only",
                    tool="run_shell",
                    action="allow",
                    match_command_regex=r"^git (status|diff|log|branch|show)",
                )
            ]
        )
        assert rset.evaluate("run_shell", {"command": "git status"}).allowed
        assert rset.evaluate("run_shell", {"command": "git diff HEAD"}).allowed
        assert rset.evaluate("run_shell", {"command": "git log --oneline"}).allowed
        # Not matched: write git command
        assert rset.evaluate("run_shell", {"command": "git push"}).allowed
        # Not matched: doesn't start with git

    def test_arg_key_value_match(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="block-specific-tool-arg",
                    tool="custom_tool",
                    action="deny",
                    match_args={"mode": "dangerous"},
                )
            ]
        )
        assert not rset.evaluate("custom_tool", {"mode": "dangerous"}).allowed
        assert rset.evaluate("custom_tool", {"mode": "safe"}).allowed
        assert rset.evaluate("custom_tool", {}).allowed

    def test_all_match_conditions_must_align(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="pick-only",
                    tool="custom_tool",
                    action="deny",
                    match_command_contains="danger",
                    match_args={"mode": "unsafe"},
                )
            ]
        )
        # Both match → deny
        assert not rset.evaluate("custom_tool", {"command": "danger", "mode": "unsafe"}).allowed
        # Only one matches → no match
        assert rset.evaluate("custom_tool", {"command": "danger", "mode": "safe"}).allowed

    def test_first_matching_rule_wins(self):
        rset = GuardRuleSet(
            [
                GuardRule(name="first", tool="test_tool", action="deny"),
                GuardRule(name="second", tool="test_tool", action="allow"),
            ]
        )
        d = rset.evaluate("test_tool", {})
        assert not d.allowed
        assert d.rule == "first"

    def test_fanout_limit_blocks_after_max(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="limit-explore",
                    tool="explore",
                    action="deny",
                    max_concurrent=2,
                )
            ]
        )
        # First two start
        rset.track_start("explore")
        rset.track_start("explore")
        # Third is blocked
        d = rset.evaluate("explore", {})
        assert not d.allowed
        assert "concurrent" in d.reason.lower()

    def test_fanout_limit_allows_after_end(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="limit-explore",
                    tool="explore",
                    action="deny",
                    max_concurrent=2,
                )
            ]
        )
        rset.track_start("explore")
        rset.track_start("explore")
        rset.track_end("explore")
        # One slot freed
        d = rset.evaluate("explore", {})
        assert d.allowed

    def test_fanout_track_end_does_not_go_negative(self):
        rset = GuardRuleSet(
            [
                GuardRule(
                    name="limit-explore",
                    tool="explore",
                    action="deny",
                    max_concurrent=2,
                )
            ]
        )
        rset.track_end("explore")  # no-op, not negative
        d = rset.evaluate("explore", {})
        assert d.allowed

    def test_load_rules_from_yaml(self, tmp_path):
        yaml_path = tmp_path / "guard.yaml"
        yaml_path.write_text(
            yaml.dump(
                {
                    "rules": [
                        {
                            "name": "block-dd",
                            "tool": "run_shell",
                            "match": {"command": {"contains": "dd if="}},
                            "action": "deny",
                            "reason": "Block dd",
                        },
                        {
                            "name": "allow-git-log",
                            "tool": "run_shell",
                            "match": {"command": {"prefix": "git log"}},
                            "action": "allow",
                            "reason": "Allow git log",
                        },
                    ]
                }
            )
        )
        rset = GuardRuleSet.load_rules(yaml_path)
        assert len(rset._rules) == 2
        assert not rset.evaluate("run_shell", {"command": "dd if=/dev/sda"}).allowed
        assert rset.evaluate("run_shell", {"command": "git log --oneline"}).allowed

    def test_load_rules_missing_file(self, tmp_path):
        rset = GuardRuleSet.load_rules(tmp_path / "nonexistent.yaml")
        assert len(rset._rules) == 0
        assert rset.evaluate("anything", {}).allowed


# =============================================================================
# GuardLogger
# =============================================================================


class TestGuardLogger:
    def test_log_decision_writes_to_file(self, tmp_path):
        log_path = tmp_path / "guard.log"
        logger = GuardLogger(log_path=str(log_path))
        logger.log_decision(
            agent_id="test-agent",
            tool_name="run_shell",
            arguments={"command": "rm -rf /"},
            allowed=False,
            reason="blocked by guard rule",
            rule="block-dangerous",
        )
        # Force flush
        for handler in logger._logger.handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "DENY" in content
        assert "test-agent" in content
        assert "run_shell" in content
        assert "block-dangerous" in content

    def test_log_decision_allow(self, tmp_path):
        log_path = tmp_path / "guard.log"
        logger = GuardLogger(log_path=str(log_path))
        logger.log_decision(
            agent_id="agent-1",
            tool_name="read_file",
            arguments={"path": "/tmp/x"},
            allowed=True,
        )
        for handler in logger._logger.handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "ALLOW" in content
        assert "agent-1" in content
        assert "read_file" in content

    def test_logger_custom_level(self, tmp_path):
        log_path = tmp_path / "guard.log"
        logger = GuardLogger(log_path=str(log_path), level=logging.WARNING)
        assert logger._logger.level == logging.WARNING
        # Logging at INFO level should not appear
        logger.log_decision(
            agent_id="test", tool_name="x", arguments={}, allowed=True
        )
        for handler in logger._logger.handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert content == ""  # WARNING level, so INFO is ignored


# =============================================================================
# GuardConfigLoader
# =============================================================================


class TestGuardConfigLoader:
    def test_load_config_returns_dict(self, tmp_path):
        yaml_path = tmp_path / "guard.yaml"
        yaml_path.write_text(
            yaml.dump({"rules": [{"name": "test", "tool": "x", "action": "deny"}]})
        )
        loader = GuardConfigLoader(yaml_path)
        cfg = loader.load_config()
        assert "rules" in cfg
        assert len(cfg["rules"]) == 1

    def test_load_config_missing_file_returns_empty(self, tmp_path):
        loader = GuardConfigLoader(tmp_path / "nonexistent.yaml")
        cfg = loader.load_config()
        assert cfg == {}

    def test_has_changed_false_on_first_load(self, tmp_path):
        yaml_path = tmp_path / "guard.yaml"
        yaml_path.write_text(yaml.dump({"rules": []}))
        loader = GuardConfigLoader(yaml_path)
        loader.load_config()
        # No change
        assert not loader.has_changed()

    def test_has_changed_true_after_file_modification(self, tmp_path):
        yaml_path = tmp_path / "guard.yaml"
        yaml_path.write_text(yaml.dump({"rules": []}))
        loader = GuardConfigLoader(yaml_path)
        loader.load_config()
        # Modify the file
        yaml_path.write_text(yaml.dump({"rules": [{"name": "new", "tool": "x", "action": "deny"}]}))
        assert loader.has_changed()

    def test_cached_config_returned_without_change(self, tmp_path):
        yaml_path = tmp_path / "guard.yaml"
        yaml_path.write_text(yaml.dump({"rules": [{"name": "original"}]}))
        loader = GuardConfigLoader(yaml_path)
        first = loader.load_config()
        # Modify file behind our back
        yaml_path.write_text(yaml.dump({"rules": [{"name": "modified"}]}))
        # Without calling load_config again, has_changed is True
        assert loader.has_changed()
        # But a second load will pick it up
        second = loader.load_config()
        assert second["rules"][0]["name"] == "modified"


# =============================================================================
# GuardMiddleware
# =============================================================================


class TestGuardMiddleware:
    def test_middleware_allows_low_risk_tool(self, tmp_path):
        permissions = PermissionEngine(workspace_root=tmp_path)
        middleware = GuardMiddleware(permissions)
        d = middleware.evaluate("read_file", {"path": "x"})
        assert d.allowed
        assert not d.needs_user

    def test_middleware_blocks_with_guard_rule(self, tmp_path):
        permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
        ruleset = GuardRuleSet(
            [GuardRule(name="block-all", tool="run_shell", action="deny")]
        )
        middleware = GuardMiddleware(permissions, ruleset=ruleset)
        d = middleware.evaluate("run_shell", {"command": "anything"})
        assert not d.allowed
        assert "guard:" in d.reason

    def test_middleware_uses_permission_engine_decision(self, tmp_path):
        permissions = PermissionEngine(
            workspace_root=tmp_path, mode=Mode.PLAN  # read-only mode
        )
        middleware = GuardMiddleware(permissions)
        d = middleware.evaluate("run_shell", {"command": "rm -rf /"})
        assert not d.allowed
        # Should be blocked by plan mode (not by guard rule)
        assert "read-only" in d.reason

    def test_middleware_guard_rule_wins_over_permissions(self, tmp_path):
        permissions = PermissionEngine(
            workspace_root=tmp_path, mode=Mode.AUTO
        )
        ruleset = GuardRuleSet(
            [
                GuardRule(
                    name="block-dangerous-command",
                    tool="run_shell",
                    action="deny",
                    match_command_contains="rm -rf",
                )
            ]
        )
        middleware = GuardMiddleware(permissions, ruleset=ruleset)
        d = middleware.evaluate("run_shell", {"command": "rm -rf /tmp"})
        assert not d.allowed
        assert "guard:" in d.reason

    def test_middleware_logs_decision(self, tmp_path):
        permissions = PermissionEngine(workspace_root=tmp_path)
        log_path = tmp_path / "guard.log"
        middleware = GuardMiddleware(
            permissions, log_path=str(log_path), agent_id="test-agent"
        )
        middleware.evaluate("run_shell", {"command": "echo hi"})
        for handler in middleware._logger._logger.handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        # Should have a log entry
        assert "test-agent" in content

    def test_middleware_exception_fails_safe(self, tmp_path):
        """On exception, middleware returns deny + needs_user (fail safe)."""
        permissions = PermissionEngine(workspace_root=tmp_path)

        class _BrokenRuleset:
            def evaluate(self, tool_name, arguments):
                raise RuntimeError("something went wrong")

            def track_start(self, tool_name):
                pass

            def track_end(self, tool_name):
                pass

            def track_start(self, tool_name):
                pass

            def track_end(self, tool_name):
                pass

        middleware = GuardMiddleware(permissions, ruleset=_BrokenRuleset())  # type: ignore
        d = middleware.evaluate("read_file", {"path": "x"})
        assert not d.allowed
        assert d.needs_user
        assert "error" in d.reason

    def test_middleware_fanout_tracking(self, tmp_path):
        permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
        ruleset = GuardRuleSet(
            [
                GuardRule(
                    name="limit-explore",
                    tool="explore",
                    action="deny",
                    max_concurrent=1,
                )
            ]
        )
        middleware = GuardMiddleware(permissions, ruleset=ruleset)
        # One active
        middleware.track_start("explore")
        # Second should be blocked
        d = middleware.evaluate("explore", {"task": "research"})
        assert not d.allowed
        # After end, allowed again
        middleware.track_end("explore")
        d = middleware.evaluate("explore", {"task": "research"})
        assert d.allowed

    def test_middleware_config_reload(self, tmp_path):
        permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
        yaml_path = tmp_path / "guard.yaml"
        # Initial config: no rules
        yaml_path.write_text(yaml.dump({"rules": []}))
        middleware = GuardMiddleware(
            permissions,
            config_path=str(yaml_path),
            agent_id="test",
        )
        # Initially everything is allowed
        assert middleware.evaluate("run_shell", {"command": "anything"}).allowed
        # Update config with a deny rule
        yaml_path.write_text(
            yaml.dump(
                {
                    "rules": [
                        {
                            "name": "block-all",
                            "tool": "run_shell",
                            "action": "deny",
                            "reason": "blocked",
                        }
                    ]
                }
            )
        )
        # Next evaluation should pick up the change
        d = middleware.evaluate("run_shell", {"command": "anything"})
        assert not d.allowed
        assert "guard:" in d.reason
