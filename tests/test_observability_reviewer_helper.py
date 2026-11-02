"""Observability E12 and E6: auto-approver checks and the explore helper, recorded.

- E12: each auto-approver check is its own step, linked to the tool call by `call_id`,
  with the reviewer's model call nested inside (purpose `auto_approver`); live checks carry
  the breaker, shadow checks whether they matched your answer.
- E6: the explore helper's model and tool calls are recorded on the parent's turn, nested
  under the `explore` tool step, with purpose `helper` and decision `{helper: true}`.
"""

from __future__ import annotations

import asyncio
import json

import aisuite as ai

from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.observability import Recorder
from coworker.observability.provider_view import RecordingProvider
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.reviewer import Reviewer
from coworker.tools import ToolRegistry
from coworker.tools.subagent import explorer_tools


class _Meta:
    category: str = ""
    risk_level: str = "high"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.lock = __import__("threading").Lock()

    def complete(self, *, model, messages, tools=None, **settings):
        with self.lock:
            turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return AssistantTurn(text=turn, usage=TokenUsage(input=50, output=5)) if isinstance(turn, str) else turn

    def capabilities(self, model):
        return ModelCapabilities()


def run_shell(command: str) -> str:
    """Run a command.

    Args:
        command: the command line
    """
    return f"ran: {command}"


def _call(cid, name, args):
    return AssistantTurn(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_calls")


def _engine(tmp_path, main_turns, review_turns, *, shadow=False, answer=ApprovalOutcome.ONCE):
    registry = ToolRegistry()
    registry.register(run_shell, metadata=_Meta())
    base = RecordingProvider(_Scripted(main_turns))

    async def approver(request):
        return answer

    engine = TurnEngine(
        provider=base,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path,
                                     mode=Mode.INTERACTIVE if shadow else Mode.AUTO_APPROVE),
        model="openai:model-a",
        approver=approver,
    )
    engine.audit_context = {"session_id": "sess_r"}
    engine.is_attended = lambda: True
    rec = Recorder()
    engine.recorder = rec
    # The reviewer's own provider: the engine's view tagged auto_approver, as the server does.
    reviewer_base = RecordingProvider(_Scripted(review_turns))
    engine.reviewer = Reviewer(
        provider=reviewer_base.tagged(purpose="auto_approver", sink=engine._obs), model="openai:model-r"
    )
    engine.reviewer_choice = "session"
    engine.reviewer_enabled = not shadow
    engine.reviewer_shadow = shadow
    return engine, rec


def _run(engine, text="clean the build"):
    async def go():
        events = [ev async for ev in engine.run(text, trigger="user")]
        await engine.drain_shadow_reviews()
        return events

    return asyncio.run(go())


def test_a_live_check_is_a_step_with_its_model_call_inside(tmp_path):
    engine, rec = _engine(
        tmp_path,
        [_call("c1", "run_shell", {"command": "make clean"}), AssistantTurn(text="done")],
        [json.dumps({"verdict": "allow", "reason": "matches the request"})],
    )
    _run(engine)
    steps = rec.as_dict()["steps"]
    [check] = [s for s in steps if s["kind"] == "auto_approver"]
    assert (check["name"], check["call_id"], check["shadow"], check["status"]) == ("run_shell", "c1", False, "ok")
    assert check["attrs"] == {
        "mode": "live", "result": "allow", "usage_reported": True,
        "reviewer": {"kind": "session_model", "external": False, "model": "openai:model-r"},
        "breaker": {"denials_in_row": 0, "tripped": False},
    }
    assert check["content"] == {"reason": "matches the request"}
    [inner] = [s for s in steps if s["kind"] == "model" and s["parent_step_id"] == check["step_id"]]
    assert inner["attrs"]["purpose"] == "auto_approver" and inner["call_id"] == "c1"
    [tool] = [s for s in steps if s["kind"] == "tool"]
    assert tool["attrs"]["decision"]["origin"] == "reviewer"


def test_a_denied_check_counts_toward_the_breaker(tmp_path):
    engine, rec = _engine(
        tmp_path,
        [_call("c1", "run_shell", {"command": "rm -rf /"}), AssistantTurn(text="ok")],
        [json.dumps({"verdict": "deny", "reason": "destroys the machine"})],
    )
    _run(engine)
    [check] = [s for s in rec.as_dict()["steps"] if s["kind"] == "auto_approver"]
    assert check["attrs"]["result"] == "deny"
    assert check["attrs"]["breaker"] == {"denials_in_row": 1, "tripped": False}
    [tool] = [s for s in rec.as_dict()["steps"] if s["kind"] == "tool"]
    assert tool["status"] == "denied" and tool["attrs"]["decision"]["origin"] == "reviewer_denied"


def test_a_check_that_could_not_run_is_an_error_step(tmp_path):
    engine, rec = _engine(
        tmp_path,
        [_call("c1", "run_shell", {"command": "deploy"}), AssistantTurn(text="ok")],
        [RuntimeError("no key")],
    )
    _run(engine)
    [check] = [s for s in rec.as_dict()["steps"] if s["kind"] == "auto_approver"]
    assert (check["status"], check["attrs"]["result"]) == ("error", "could_not_run")


def test_a_shadow_check_records_whether_it_matched_you(tmp_path):
    engine, rec = _engine(
        tmp_path,
        [_call("c1", "run_shell", {"command": "make"}), AssistantTurn(text="done")],
        [json.dumps({"verdict": "allow", "reason": "fine"})],
        shadow=True, answer=ApprovalOutcome.DENY,
    )
    _run(engine)
    [check] = [s for s in rec.as_dict()["steps"] if s["kind"] == "auto_approver"]
    assert check["shadow"] is True and check["attrs"]["mode"] == "shadow"
    assert check["attrs"]["matched_user"] is False  # it would have allowed what you denied
    assert "breaker" not in check["attrs"]


def test_explore_helper_steps_are_nested_under_the_explore_step(tmp_path):
    (tmp_path / "notes.txt").write_text("Friday release", encoding="utf-8")
    scripted = _Scripted([
        _call("c1", "explore", {"task": "what is in notes.txt?"}),  # parent
        _call("h1", "read_file", {"path": "notes.txt"}),           # helper
        AssistantTurn(text="It says Friday release"),              # helper report
        AssistantTurn(text="The notes say Friday."),               # parent
    ])
    base = RecordingProvider(scripted)
    registry = ToolRegistry()
    registry.register_all(explorer_tools(workspace=tmp_path, provider=base, model="openai:model-a"))
    engine = TurnEngine(provider=base, registry=registry,
                        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
                        model="openai:model-a")
    engine.audit_context = {"session_id": "sess_h"}
    rec = Recorder()
    engine.recorder = rec
    _run(engine, "use explore")
    d = rec.as_dict()
    [turn] = d["turns"]
    [explore] = [s for s in d["steps"] if s["kind"] == "tool" and s["name"] == "explore"]
    nested = [s for s in d["steps"] if s["parent_step_id"] == explore["step_id"]]
    assert sorted((s["kind"], s["attrs"].get("purpose")) for s in nested) == [
        ("model", "helper"), ("model", "helper"), ("tool", None)]
    [read] = [s for s in nested if s["kind"] == "tool"]
    assert read["name"] == "read_file" and read["attrs"]["decision"] == {"helper": True}
    assert all(s["turn_id"] == turn["turn_id"] for s in nested)
    # The helper's own run is not a second turn.
    assert len(d["turns"]) == 1
    mains = [s for s in d["steps"] if s["kind"] == "model" and s["attrs"]["purpose"] == "main"]
    assert len(mains) == 2


# -- E23: a decision-model reviewer's request is a nested model step ----------------------------

import threading as _threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _JevHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        answers = {name: {"noul": 0.97, "probabilities": {"yes": 0.97, "no": 0.03}}
                   for name in (body.get("questions") or {})}
        data = json.dumps({"model": "jev-test-1", "answers": answers,
                           "usage": {"input_tokens": 6203, "output_tokens": 529}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("x-request-id", "req_jev")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_a_decision_reviewer_check_has_a_model_step_with_usage(tmp_path):
    from coworker.decision_reviewer import build_decision_reviewer

    server = HTTPServer(("127.0.0.1", 0), _JevHandler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        engine, rec = _engine(tmp_path, [_call("c1", "run_shell", {"command": "make clean"}),
                                         AssistantTurn(text="done")], [])
        engine.reviewer = build_decision_reviewer(
            "jev", api_key="test-key-not-real", base_url=f"http://127.0.0.1:{server.server_address[1]}")
        engine.reviewer_choice = "jev"
        _run(engine)
    finally:
        server.shutdown()
    steps = rec.as_dict()["steps"]
    [check] = [s for s in steps if s["kind"] == "auto_approver"]
    assert check["attrs"]["reviewer"] == {"kind": "typesafe_jev", "external": True, "model": None}
    [inner] = [s for s in steps if s["kind"] == "model" and s["parent_step_id"] == check["step_id"]]
    assert inner["attrs"]["purpose"] == "auto_approver" and inner["attrs"]["provider"] == "typesafe_jev"
    assert inner["attrs"]["usage"] == {"reported": True, "input_fresh": 6203, "cache_read": None,
                                       "cache_write": None, "output": 529, "reasoning": None}
    assert inner["attrs"]["provider_meta"][0]["wire"] == "typesafe_jev"
    assert inner["attrs"]["provider_facts"]["provider_request_id"] == "req_jev"
    assert check["attrs"]["usage_reported"] is True
    assert "test-key-not-real" not in json.dumps(steps)
