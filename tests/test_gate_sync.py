"""The same stable gate identity reaches every viewing socket on external resolution."""
import asyncio

import pytest

from test_engine import ScriptedProvider, _text_turn, _tool_turn
from test_team_live_gaps import manager
from proposal_fixtures import work_proposal, team_proposal


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("name,args,event_name", [
    ("propose_team", team_proposal(), "team_proposed"),
    ("propose_work_items", work_proposal(), "items_proposed"),
])
def test_external_gate_answer_broadcasts_matching_completion_to_all_viewers(manager, approved, name, args, event_name):
    manager.provider = ScriptedProvider([_tool_turn(name, args, "proposal-id"), _text_turn("Finished")])
    engine = manager.get_engine("lead")
    views = [[], []]

    async def run():
        for view in views:
            async def receive(event, view=view):
                view.append(event)
            manager.register_session_client("lead", receive)

        async def ask(args, tool_call_id):
            item = manager.inbox.add_plan("lead", "Approve?", tool_call_id=tool_call_id)
            return {"approved": (await manager.inbox.wait(item.id)) == "allow"}

        if name == "propose_team":
            engine.team_approver = ask
        else:
            engine.items_approver = ask
        turn = asyncio.create_task(manager.deliver_to_session("lead", "Propose the work"))
        for _ in range(100):
            await asyncio.sleep(.01)
            pending = manager.inbox.pending("lead")
            if pending:
                break
        assert pending
        # The shared path used by REST/Inbox, not a foreground socket response.
        assert await manager.resolve_inbox(pending[0].id, "allow" if approved else "deny", by="second-viewer")
        await asyncio.wait_for(turn, 3)

    asyncio.run(run())
    for view in views:
        proposed = next(e for e in view if e["type"] == event_name)
        finished = next(e for e in view if e["type"] == "tool_finished" and e["data"]["name"] == name)
        assert proposed["data"]["tool_call_id"] == finished["data"]["tool_call_id"] == "proposal-id"
        assert finished["data"]["status"] == ("ok" if approved else "denied")
