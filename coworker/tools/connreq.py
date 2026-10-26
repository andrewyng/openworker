"""`request_connector` and `grant_connector` — connector access is a human decision.

Spec (connectors-across-machines §11.6, owner-ruled 2026-09-05):

- `request_connector(connector, reason)`: any connector-capable coworker may ask the
  human to connect a service it could use ("connecting GitHub lets me keep the board
  and the repo in step"). The card offers Connect / Not now; declining is a normal
  outcome and the coworker says what it could not do.
- `grant_connector(worker, connector, reason)`: a lead asks the human to give one of
  its workers a connector, later than the staffing card. Approval flips the worker's
  per-session connection within the worker persona's declared ceiling.

Both are engine-intercepted like `request_tool`: the user's out-of-band decision IS the
consent, so they never go through the permission path. These bodies only run when no
requester is wired (a surface that cannot prompt).
"""

from __future__ import annotations

from aisuite import ToolMetadata, tool


def request_connector_tool() -> object:
    def request_connector(connector: str, reason: str) -> dict:
        """Ask the user to connect a service you could use but that is not connected
        (e.g. `github`, `slack`, `linear`). Use it when a connection would let you do
        the job properly — keeping a tracker in step, posting where the team reads —
        not for a convenience. `reason` is ONE sentence: what the connection lets you
        do. The user may decline; then carry on without it and say plainly what you
        could not do. Never ask twice for the same connector in one session."""
        return {"approved": False, "error": "connector requests aren't available in this surface"}

    return tool(
        request_connector,
        metadata=ToolMetadata(
            category="system",
            risk_level="low",
            capabilities=["request_connector"],
            description="Ask the user to connect a service (GitHub, Slack, Linear, …) you could use.",
        ),
    )


def grant_connector_tool() -> object:
    def grant_connector(worker: str, connector: str, reason: str) -> dict:
        """Ask the user to give one of your workers access to a connector it does not
        have yet — `worker` is the callname from the staffing roster (e.g. "nia"),
        `connector` the service id (e.g. "github"). Workers start with no connectors;
        ask only when the item genuinely needs it (pushing a branch, filing an issue)
        and say so in `reason` (one sentence). The user decides; if declined, route
        the external step through yourself or the user."""
        return {"approved": False, "error": "connector grants aren't available in this surface"}

    return tool(
        grant_connector,
        metadata=ToolMetadata(
            category="team",
            risk_level="medium",
            capabilities=["team"],
            description="Ask the user to grant one of your workers a connector.",
        ),
    )
