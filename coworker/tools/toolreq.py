"""The `request_tool` tool — the agent asks the user for a CLI it needs but can't find.

Sibling of `request_directory`: the TurnEngine intercepts it, emits TOOL_REQUESTED, and the
user decides out-of-band (install the pinned build, or skip and let the run continue
degraded). The callable here is only a schema carrier + the fallback for surfaces with no
requester wired.

This exists because of a specific failure mode (OPE-85): with gitleaks absent, a security
review silently dropped its git-history secret scan — the check didn't fail, it vanished
from the report. A missing tool must become a visible decision, never an invisible gap.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata, tool


def request_tool_tool() -> object:
    def request_tool(name: str, reason: str) -> dict:
        """Ask the user to install one of the PINNED catalog tools you need but can't find
        on this machine. The catalog is a small closed set — currently `gitleaks`,
        `trivy`, `osv-scanner` — installed at a pinned, checksum-verified version.

        For ANY other missing CLI (semgrep, jq, kubectl, …) do NOT use this tool: install
        it yourself with the shell (brew/pip/…), which goes through the normal command
        approval, or proceed without it.

        Keep `reason` to ONE sentence: which check needs the tool. The prompt the user
        sees already explains what the install is (pinned version, publisher, checksum)
        and what happens if they decline — don't restate any of that in `reason`.

        Use this INSTEAD of quietly skipping a check. If the user declines, carry on with a
        fallback (e.g. reading git history yourself instead of running gitleaks) and state
        plainly in your report which checks were degraded and why.
        """
        return {
            "installed": False,
            "error": "tool requests aren't available in this surface",
        }

    return tool(
        request_tool,
        metadata=ToolMetadata(
            category="system",
            risk_level="low",
            capabilities=["request_tool"],
            description=(
                "Ask the user to install a missing command-line tool, rather than silently "
                "skipping the check that needs it."
            ),
        ),
    )
