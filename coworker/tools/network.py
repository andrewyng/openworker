"""The `request_network_access` tool: the agent asks the person for a site its commands
cannot reach (OPE-219).

A sandboxed session on "Only the sites you allow" lets its commands out to the listed sites
only. When a task needs another one, the agent calls this tool with the host and a reason.
The permission engine turns the call into a card only a person can answer (allow for this
session, always allow, or deny); on approval the engine opens the site on the running
sandbox, and only then does this body run, to tell the agent what it now has. In full
access the call is refused: no mode changes a wall.

Registered only for a session whose sandbox uses the allowed-sites list.
"""

from __future__ import annotations

from typing import Any

from aisuite.agents import ToolMetadata, tool

from ..permissions import NETWORK_ACCESS_TOOL, PermissionEngine, network_request_hosts


def _name(entry: str) -> str:
    return entry[:-4] if entry.endswith(":443") else entry


def request_network_access_tool(permissions: PermissionEngine) -> object:
    def request_network_access(hosts: list[str], reason: str) -> dict:
        """Ask the user to let this session's commands reach a site that the sandbox blocks.
        Give `hosts` as exact host names (for example ["registry.npmjs.org"]; add a port
        only when it is not 443, for example "db.example.com:5432"), at most five, no
        wildcards and no IP addresses, and a one-line `reason` the user can judge. Use it
        when a command failed because a site was blocked, or before a command you know
        needs a site that is not on the allowed sites. The user decides; when the result
        says a site is allowed, run the command again. Ask only for sites the task needs,
        and do not look for another route to a blocked site.
        """
        wanted, _ = network_request_hosts({"hosts": hosts})
        allowed = [entry for entry in wanted if permissions._entry_allowed(entry)]
        failed: dict[str, Any] = {
            _name(entry): permissions.site_open_errors[key]
            for entry in allowed
            for key in (entry, entry.rsplit(":", 1)[0])
            if key in permissions.site_open_errors
        }
        ready = [_name(entry) for entry in allowed if _name(entry) not in failed]
        result: dict[str, Any] = {"allowed": ready}
        if ready:
            result["message"] = f"{', '.join(ready)} can be reached by this session's commands now. Run the command again."
        if failed:
            result["not_opened"] = failed
            result["note"] = "The user allowed these, but the running sandbox could not take them. Tell the user; a new session will have them."
        return result

    request_network_access.__name__ = NETWORK_ACCESS_TOOL
    return tool(
        request_network_access,
        metadata=ToolMetadata(
            category="sandbox",
            risk_level="medium",
            capabilities=["request_network_access"],
            description="Ask the user to let this session's commands reach a site that the sandbox's allowed sites block.",
        ),
    )
