"""OB-13: the sandbox's start as a `sandbox` setup step with its stages, and restarts as a
`sandbox_restarted` notice (phase-2 design §12, recording format 1.2).

A fake provider and a fake runner client stand in for a real sandbox: the stages, their
order and the recording are what is tested, not a sandbox."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import jsonschema

from coworker.observability import calculator, signals
from coworker.observability.export_spans import Masker, build_turn_spans
from coworker.providers import AssistantTurn
from coworker.sandbox import stages
from coworker.sandbox.workspace import RunnerWorkspace

base = importlib.import_module("test_observability_stamps_context")

SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))


def _check(recording):
    validator = jsonschema.validators.validator_for(SCHEMA)(SCHEMA)
    errors = sorted(validator.iter_errors(recording), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message[:300]}" for e in errors[:8])


class ImageMissing(RuntimeError):
    pass


class _Provider:
    """A provider that reports its own inner stages, like OpenShell."""

    name = "openshell"
    reports_stages = True
    profile = "allowlist"
    extra_hosts: list = []

    def __init__(self, *, fail_at=None, grants=True, image_known=False, regrant=False):
        self.fail_at, self.grants, self.image_known = fail_at, grants, image_known
        self.roots = [{"path": "/a", "writable": True}]
        self.destroyed = 0
        if regrant:
            self.regrant = self._regrant

    def create(self):
        if self.grants:
            stages.stage(self, "copy_logins")
        stages.stage(self, "create")
        if not self.image_known:
            stages.mark(self, "download_image", uncertain=True)
        if self.fail_at == "create":
            raise ImageMissing("The sandbox base image is not downloaded yet")
        stages.stage(self, "ready")

    def verify(self, client):
        if self.fail_at == "verify":
            raise RuntimeError("the home folder was listable from inside")

    def destroy(self):
        self.destroyed += 1

    def describe(self):
        return {"provider": self.name, "enforcement": "full", "sandbox": "sbx-1"}

    def _regrant(self, roots, *, before_create=None):
        self.roots = list(roots)
        self.create()  # a restart makes a new sandbox: not part of the first start's stages


class _Client:
    def __init__(self):
        self.restarts = 0
        self.starter = None
        self.hello = {}

    def connect(self):
        return {"instance_id": "i1"}

    def detach(self):
        pass


class _Root:
    def __init__(self, path, writable=True):
        self.path, self.writable = path, writable


def _workspace(provider, roots=None):
    ws = RunnerWorkspace(provider, cwd=".", start=False, live_roots=roots)
    ws.client = _Client()
    return ws


# -- the stage log ---------------------------------------------------------------------------


def test_stages_close_in_order_and_a_failure_names_its_stage():
    log = stages.StageLog()
    log.begin("preparing")
    log.begin("create")
    log.mark("download_image", uncertain=True)
    log.end(ImageMissing("no image"))
    names = [(s["name"], s["status"]) for s in log.stages]
    assert names == [("preparing", "ok"), ("create", "error"), ("download_image", "ok")]
    assert log.stages[2]["duration_ms"] is None and log.stages[2]["uncertain"] is True
    assert all(isinstance(s["duration_ms"], int) for s in log.stages[:2])
    assert log.failed_stage == "create"
    assert log.error == {"type": "ImageMissing", "text": "no image"}
    log.begin("nonsense")  # not a stage name: ignored
    assert len(log.stages) == 3


def test_the_workspace_records_each_stage_of_its_start():
    told = []
    ws = _workspace(_Provider())
    ws.observer = lambda event, data: told.append((event, data.get("trigger")))
    ws.ensure_started()
    assert [s["name"] for s in ws.last_start.stages] == ["preparing", "copy_logins", "create", "download_image", "ready", "connect", "verify"]
    assert ws.last_start.failed_stage is None
    assert told == [("setup_started", "first_tool"), ("setup_ended", "first_tool")]
    assert getattr(ws.provider, "stage_log", None) is None  # a later restart is not this start


def test_a_provider_without_stages_gets_one_create_stage():
    provider = _Provider()
    provider.reports_stages = False
    ws = _workspace(provider)
    ws.ensure_started("turn_start")
    assert [s["name"] for s in ws.last_start.stages][:2] == ["preparing", "create"]


# -- in a turn -------------------------------------------------------------------------------

_WS: dict = {}


def run_cmd(command: str) -> dict:
    """Run a command in the sandbox.

    Args:
        command: the command
    """
    # What the executor does before every command: restart when the folders changed.
    return {"exit_code": 0, "output": "", "sandbox_notice": _WS["ws"].sync_roots()}


def needs_runner(path: str) -> dict:
    """Read something in the sandbox.

    Args:
        path: what to read
    """
    _WS["ws"].ensure_started()  # what the runner client's starter does on first use
    return {"path": path}


def _engine_with(tmp_path, provider, turns, roots=None):
    engine, rec = base._engine(tmp_path, turns)
    for fn in (run_cmd, needs_runner):  # before any turn: the engine offers what is registered
        engine.registry.register(fn, metadata=base._ReadMeta())
    ws = _workspace(provider, roots)
    ws.observer = engine.on_sandbox
    engine.sandbox_workspace = ws
    _WS["ws"] = ws
    return engine, rec, ws


def _turns(engine, *steps):
    """Several turns on one event loop, as in the app; a callable between them changes
    something mid-session. (A loop per turn leaves the engine's Stop flag on the old loop.)"""

    async def go():
        for step in steps:
            if callable(step):
                step()
            else:
                async for _ in engine.run(step, trigger="user"):
                    pass

    asyncio.run(go())


async def _no_start():
    if False:  # pragma: no cover - an empty async generator
        yield None


def test_the_first_turn_records_the_setup_step(tmp_path):
    engine, rec, ws = _engine_with(tmp_path, _Provider(), [AssistantTurn(text="ok"), AssistantTurn(text="again")])
    _turns(engine, "go")
    d = rec.as_dict()
    [setup] = [s for s in d["steps"] if s["kind"] == "sandbox"]
    assert setup["name"] == "setup" and setup["status"] == "ok" and setup["parent_step_id"] is None
    a = setup["attrs"]
    assert a["provider"] == "openshell" and a["trigger"] == "turn_start" and a["failed_stage"] is None
    assert [s["name"] for s in a["stages"]] == ["preparing", "copy_logins", "create", "download_image", "ready", "connect", "verify"]
    assert "error" not in a
    _check(d)
    turn = d["turns"][0]
    assert "sandbox" in calculator.buckets(d, turn)  # its own total (mock ob13)
    assert signals.check_turn(turn, d["steps"])["warn"] == 0
    _turns(engine, "again")  # the sandbox is running: no setup step
    assert len([s for s in rec.as_dict()["steps"] if s["kind"] == "sandbox"]) == 1


def test_a_failed_start_names_its_stage_and_is_an_issue(tmp_path):
    engine, rec, ws = _engine_with(tmp_path, _Provider(fail_at="create"), [AssistantTurn(text="never")])
    events = base._run(engine)
    assert any(e.type.name == "ERROR" for e in events)
    d = rec.as_dict()
    [setup] = [s for s in d["steps"] if s["kind"] == "sandbox"]
    assert setup["status"] == "error"
    assert setup["attrs"]["failed_stage"] == "create"
    assert setup["attrs"]["error"] == {"type": "ImageMissing", "text": "The sandbox base image is not downloaded yet"}
    assert d["turns"][0]["status"] == "error"
    _check(d)
    found = signals.check_turn(d["turns"][0], d["steps"])
    assert [i["check"] for i in found["issues"]] == ["sandbox_failed"]
    assert "create" in found["issues"][0]["text"] and "ImageMissing" in found["issues"][0]["text"]
    assert ws.provider.destroyed == 1


def test_a_lazy_start_inside_a_tool_is_recorded_as_first_tool(tmp_path):
    """On resume nothing starts the sandbox at the turn's start: the first runner call does."""
    engine, rec, ws = _engine_with(tmp_path, _Provider(grants=False, image_known=True),
                                   [base._call("c1", "needs_runner", {"path": "x"}), AssistantTurn(text="ok")])
    engine._start_sandbox = _no_start
    base._run(engine)
    d = rec.as_dict()
    [setup] = [s for s in d["steps"] if s["kind"] == "sandbox"]
    assert setup["attrs"]["trigger"] == "first_tool"
    assert [s["name"] for s in setup["attrs"]["stages"]] == ["preparing", "create", "ready", "connect", "verify"]
    assert setup["parent_step_id"] is None and setup["turn_id"] == d["turns"][0]["turn_id"]
    [tool] = [s for s in d["steps"] if s["kind"] == "tool"]
    assert tool["started_at"] <= setup["started_at"]  # it started inside the call that needed it
    _check(d)


def test_a_restart_for_changed_folders_is_a_notice_and_the_tool_says_so(tmp_path):
    roots = [_Root("/a")]
    engine, rec, ws = _engine_with(tmp_path, _Provider(regrant=True),
                                   [AssistantTurn(text="ok"), base._call("c1", "run_cmd", {"command": "ls"}),
                                    AssistantTurn(text="done")], roots=roots)
    _turns(engine, "go", lambda: roots.append(_Root("/b")), "again")  # a folder granted mid-session
    d = rec.as_dict()
    [notice] = [s for s in d["steps"] if s["kind"] == "notice" and s["name"] == "sandbox_restarted"]
    assert notice["attrs"] == {"provider": "openshell", "reason": "folders_changed", "how": "restart"}
    [tool] = [s for s in d["steps"] if s["kind"] == "tool"]
    assert tool["attrs"]["sandbox"]["notice"] == "restart"
    assert notice["started_at"] >= tool["started_at"]
    # The first start's stages did not grow with the restart's create.
    [setup] = [s for s in d["steps"] if s["kind"] == "sandbox"]
    assert [s["name"] for s in setup["attrs"]["stages"]].count("create") == 1
    _check(d)


def test_a_runner_that_came_back_on_its_own_is_a_notice(tmp_path):
    engine, rec, ws = _engine_with(tmp_path, _Provider(),
                                   [AssistantTurn(text="ok"), base._call("c1", "run_cmd", {"command": "ls"}),
                                    AssistantTurn(text="done")])
    def came_back():
        ws.client.restarts += 1  # the client saw a new runner instance after a reconnect

    _turns(engine, "go", came_back, "again")
    d = rec.as_dict()
    [notice] = [s for s in d["steps"] if s["name"] == "sandbox_restarted"]
    assert notice["attrs"]["reason"] == "runner_restarted"
    [tool] = [s for s in d["steps"] if s["kind"] == "tool"]
    assert tool["attrs"]["sandbox"]["notice"] is None  # the agent was told nothing new
    _check(d)


def test_the_setup_span_has_one_event_per_stage(tmp_path):
    engine, rec, ws = _engine_with(tmp_path, _Provider(fail_at="verify"), [AssistantTurn(text="never")])
    base._run(engine)
    d = rec.as_dict()
    turn = d["turns"][0]
    steps = [s for s in d["steps"] if s["turn_id"] == turn["turn_id"]]
    spans = build_turn_spans(turn, steps, sw={}, dialect="otel", get_piece=lambda k: None, masker=Masker())
    [span] = [s for s in spans if s["name"] == "openworker.sandbox.setup"]
    assert span["attrs"]["openworker.sandbox.failed_stage"] == "verify"
    assert span["error"] == "RuntimeError"
    names = [e["name"] for e in span["events"]]
    assert names[0] == "openworker.sandbox.stage.preparing" and names[-1] == "openworker.sandbox.stage.verify"
    assert "listable" not in json.dumps(span)  # the error's text stays on this machine
