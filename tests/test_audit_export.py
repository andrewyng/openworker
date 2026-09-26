"""Audit export (remote/audit_export.py): off by default, policy-driven, content-blind."""

from __future__ import annotations

import asyncio
import json

import pytest

from coworker.audit import AuditStore
from coworker.remote.audit_export import (
    AuditExporter,
    ExportPolicy,
    load_policy,
    save_policy,
    to_ocsf,
)


def _row_event(i=0, **over):
    return {
        "session_id": "s1",
        "agent": "cowork",
        "workspace": "/Users/x/proj",
        "tool": "github_open_pr",
        "stage": "tool_call",
        "status": "ok",
        "approval": "allow",
        "arguments": {"directory": "/Users/x/proj", "title": f"PR {i}", "token": "sk-secret"},
        "result_preview": "THE PR BODY — content, must never leave",
        "reason": "the model's reasoning — content, must never leave",
        "resource": "acme/api#12",
        "call_id": f"c{i}",
        "tokens_in": 10,
        "tokens_out": 5,
        **over,
    }


# -- policy ---------------------------------------------------------------------


def test_policy_is_off_by_default_and_never_on_by_accident():
    assert ExportPolicy.from_policy(None).enabled is False
    assert ExportPolicy.from_policy({}).enabled is False
    assert ExportPolicy.from_policy({"audit_export": "yes"}).enabled is False
    assert ExportPolicy.from_policy({"audit_export": {"enabled": False}}).enabled is False
    # http sink needs a real URL, otherwise OFF.
    assert ExportPolicy.from_policy({"audit_export": {"enabled": True, "sink": "http"}}).enabled is False
    assert ExportPolicy.from_policy({"audit_export": {"enabled": True, "sink": "ftp"}}).enabled is False


def test_policy_parses_cloud_and_http_sinks():
    cloud = ExportPolicy.from_policy({"audit_export": {"enabled": True}})
    assert cloud.enabled and cloud.sink == "cloud" and cloud.batch == 200
    http = ExportPolicy.from_policy(
        {
            "audit_export": {
                "enabled": True,
                "sink": "http",
                "url": "https://siem.example/hec",
                "headers": {"Authorization": "Splunk x"},
                "batch": 5000,
                "flush_seconds": 0.1,
            }
        }
    )
    assert http.sink == "http" and http.url == "https://siem.example/hec"
    assert http.headers == {"Authorization": "Splunk x"}
    assert http.batch == 1000 and http.flush_seconds == 1.0  # clamped


def test_policy_record_persists_for_visible_governance(tmp_path):
    save_policy(tmp_path, {"audit_export": {"enabled": True}, "org_id": "org-1"})
    stored = load_policy(tmp_path)
    assert stored["audit_export"] == {"enabled": True} and stored["org_id"] == "org-1"
    assert "received_at" in stored
    assert load_policy(tmp_path / "missing") == {}


# -- OCSF mapping ------------------------------------------------------------------


def test_to_ocsf_is_content_blind(tmp_path):
    store = AuditStore(tmp_path / "a.db")
    store.append(_row_event())
    row = store.list_since(0)[0]
    ev = to_ocsf(row, machine_id="m1", machine_name="cloud-vm", org_id="org-1")
    flat = json.dumps(ev)
    assert "THE PR BODY" not in flat and "reasoning" not in flat
    assert "sk-secret" not in flat  # the local store already stripped it
    assert ev["class_uid"] == 6003 and ev["category_uid"] == 6 and ev["type_uid"] == 600399
    assert ev["api"] == {"operation": "github_open_pr", "service": {"name": "github"}}
    # No controller-verified login on this row → automated; the persona rides unmapped.
    assert ev["actor"]["user"] == {"name": "", "org": {"uid": "org-1"}}
    assert ev["unmapped"]["agent"] == "cowork" and ev["unmapped"]["automated"] is True
    assert ev["actor"]["session"] == {"uid": "s1"}
    store.append(_row_event(1, actor="erin@l.com"))
    who = to_ocsf(store.list_since(1)[0], org_id="org-1")
    assert who["actor"]["user"] == {"name": "erin@l.com", "email_addr": "erin@l.com", "org": {"uid": "org-1"}}
    assert who["unmapped"]["automated"] is False
    assert ev["device"] == {"uid": "m1", "name": "cloud-vm"}
    assert ev["unmapped"]["workspace"] == "proj"  # basename only
    assert ev["unmapped"]["tokens_in"] == 10 and ev["unmapped"]["tokens_out"] == 5
    assert ev["unmapped"]["approval"] == "allow"
    assert isinstance(ev["unmapped"]["resource"], str)  # store-derived, never content
    assert ev["status_id"] == 1 and ev["metadata"]["uid"] == f"m1:{row['id']}"
    assert isinstance(ev["time"], int) and ev["time"] > 1_600_000_000_000
    store.close()


def test_store_cursor_read_and_subscribe(tmp_path):
    store = AuditStore(tmp_path / "a.db")
    seen = []
    store.subscribe(seen.append)
    for i in range(3):
        store.append(_row_event(i))
    assert seen == [1, 2, 3]
    assert [r["id"] for r in store.list_since(0)] == [1, 2, 3]
    assert [r["id"] for r in store.list_since(2)] == [3]
    assert store.list_since(1, limit=1)[0]["args"]["title"] == "PR 1"
    store.close()


# -- the emitter -------------------------------------------------------------------


def test_flush_advances_cursor_only_on_ack(tmp_path):
    store = AuditStore(tmp_path / "a.db")
    for i in range(5):
        store.append(_row_event(i))
    policy = ExportPolicy.from_policy({"audit_export": {"enabled": True, "batch": 2}})
    exp = AuditExporter(tmp_path, store, machine_id="m1", policy=policy)
    batches = []

    async def ok(events):
        batches.append([e["unmapped"]["row_id"] for e in events])
        return True

    assert asyncio.run(exp.flush_once(ok)) == 5
    assert batches == [[1, 2], [3, 4], [5]]
    assert exp.status()["last_id"] == 5 and exp.status()["exported"] == 5
    assert json.loads((tmp_path / "audit-export.json").read_text())["last_id"] == 5

    # New rows, sink refuses: cursor stays, error recorded, nothing skipped.
    store.append(_row_event(6))

    async def refuse(events):
        return False

    with pytest.raises(ConnectionError):
        asyncio.run(exp.flush_once(refuse))
    assert exp.status()["last_id"] == 5 and "refused" in exp.status()["last_error"]
    assert exp.status()["pending"] == 1
    # A fresh exporter over the same state resumes from the persisted cursor.
    again = AuditExporter(tmp_path, store, machine_id="m1", policy=policy)
    assert asyncio.run(again.flush_once(ok)) == 1 and batches[-1] == [6]
    store.close()


def test_run_idles_while_off_and_ships_when_turned_on(tmp_path):
    store = AuditStore(tmp_path / "a.db")
    exp = AuditExporter(tmp_path, store, machine_id="m1")  # policy off
    sent = []

    async def sink(events):
        sent.extend(events)
        return True

    async def scenario():
        task = asyncio.create_task(exp.run(sink))
        store.append(_row_event(0))
        await asyncio.sleep(0.2)
        assert sent == []  # off: nothing leaves
        exp.set_policy(ExportPolicy.from_policy({"audit_export": {"enabled": True, "flush_seconds": 1}}))
        for _ in range(50):
            if sent:
                break
            await asyncio.sleep(0.05)
        assert len(sent) == 1
        store.append(_row_event(1))  # the append wakes the loop
        for _ in range(50):
            if len(sent) == 2:
                break
            await asyncio.sleep(0.05)
        assert len(sent) == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert exp.status()["enabled"] is True and exp.status()["sink"] == "cloud"
    store.close()


def test_channel_sender_resolves_on_ack(tmp_path):
    store = AuditStore(tmp_path / "a.db")
    exp = AuditExporter(tmp_path, store)
    frames = []

    async def send_frame(frame):
        frames.append(frame)
        # The controller answers out of band; simulate it.
        asyncio.get_running_loop().call_soon(
            exp.ack, {"type": "audit_ack", "id": frame["id"], "accepted": True}
        )

    send = exp.channel_sender(send_frame)
    assert asyncio.run(send([{"x": 1}])) is True
    assert frames[0]["type"] == "audit" and frames[0]["events"] == [{"x": 1}]

    async def send_frame_refused(frame):
        asyncio.get_running_loop().call_soon(
            exp.ack, {"type": "audit_ack", "id": frame["id"], "accepted": False}
        )

    assert asyncio.run(exp.channel_sender(send_frame_refused)([{"x": 2}])) is False
    store.close()


# -- the channel round trip: welcome policy → box exports → controller sink → push off


class _Server:
    """The real controller app under real uvicorn on an ephemeral port."""

    def __init__(self, app):
        import uvicorn

        self._config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        self._server = uvicorn.Server(self._config)
        self._task = None

    async def __aenter__(self):
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.01)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        self._server.should_exit = True
        await self._task


async def test_policy_rides_welcome_and_audit_batches_reach_the_sink(tmp_path):
    import httpx

    from coworker.remote.joiner import parse_join_url, run_joined
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    ctrl = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    received: list[tuple[str, list]] = []
    accept = {"ok": True}

    def policy_for(machine_id, known):
        return {"org_id": "org-1", "version": 1, "audit_export": {"enabled": True, "flush_seconds": 1}}

    async def audit_sink(machine_id, known, events):
        received.append((machine_id, events))
        return accept["ok"]

    ctrl.policy_for = policy_for
    ctrl.audit_sink = audit_sink
    ctrl_app = create_app(ctrl)
    box = SessionManager(data_dir=tmp_path / "box-data")
    box_app = create_app(box)
    async with _Server(ctrl_app) as server:
        async with httpx.AsyncClient() as client:
            join_url = (await client.post(f"{server.base}/v1/remote/arm")).json()["join_url"]
        _, token = parse_join_url(join_url)
        state = tmp_path / "box-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="cloud-vm",
                token=token,
                app=box_app,
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            for _ in range(100):
                if getattr(box, "org_policy", None):
                    break
                await asyncio.sleep(0.05)
            # The welcome carried the org policy: persisted + applied + visible.
            assert box.org_policy["org_id"] == "org-1"
            assert load_policy(state)["audit_export"]["enabled"] is True
            assert box.get_settings()["audit_export"]["enabled"] is True
            assert box.get_settings()["audit_export"]["sink"] == "cloud"

            # A tool call on the box → one OCSF event at the controller's sink.
            box.audit_store.append(_row_event(1, session_id="sess-9"))
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(0.05)
            machine_id, events = received[0]
            assert len(events) == 1 and events[0]["class_uid"] == 6003
            assert events[0]["device"]["uid"] == machine_id and events[0]["device"]["name"] == "cloud-vm"
            assert events[0]["actor"]["user"]["org"] == {"uid": "org-1"}
            assert events[0]["actor"]["session"] == {"uid": "sess-9"}
            assert "THE PR BODY" not in json.dumps(events)
            assert box.audit_exporter.status()["exported"] == 1

            # A refusing sink: the cursor holds and the row is retried, not lost.
            accept["ok"] = False
            box.audit_store.append(_row_event(2))
            for _ in range(100):
                if len(received) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert box.audit_exporter.status()["last_id"] == 1
            accept["ok"] = True
            for _ in range(200):
                if box.audit_exporter.status()["last_id"] == 2:
                    break
                await asyncio.sleep(0.05)
            assert box.audit_exporter.status()["last_id"] == 2

            # Governance flips it off: the send lands, the record updates, exports stop.
            status, body = await ctrl_app.state.remote_acceptor.push_policy(
                machine_id, {"org_id": "org-1", "version": 2, "audit_export": {"enabled": False}}
            )
            assert status == 200 and body["ok"] is True and body["applied"] is True and body["version"] == 2
            assert box.get_settings()["audit_export"]["enabled"] is False
            assert load_policy(state)["audit_export"] == {"enabled": False}
            before = len(received)
            box.audit_store.append(_row_event(3))
            await asyncio.sleep(0.4)
            assert len(received) == before
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_desktop_controller_sends_no_policy_and_refuses_audit(tmp_path):
    """A desktop (OSS) controller has no policy and no sink: the box keeps its
    stored record (off), and an audit frame is answered accepted=false."""
    import httpx

    from coworker.remote.joiner import parse_join_url, run_joined
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    ctrl = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    ctrl_app = create_app(ctrl)
    box = SessionManager(data_dir=tmp_path / "box-data")
    box_app = create_app(box)
    async with _Server(ctrl_app) as server:
        async with httpx.AsyncClient() as client:
            join_url = (await client.post(f"{server.base}/v1/remote/arm")).json()["join_url"]
        _, token = parse_join_url(join_url)
        state = tmp_path / "box-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(state=state, controller=server.base, name="lap", token=token,
                       app=box_app, once=True, log=lambda *_: None)
        )
        try:
            for _ in range(100):
                if getattr(box, "audit_exporter", None) is not None:
                    break
                await asyncio.sleep(0.05)
            assert box.get_settings()["audit_export"]["enabled"] is False
            assert load_policy(state) == {}
            box.audit_store.append(_row_event(1))
            await asyncio.sleep(0.3)
            assert box.audit_exporter.status()["exported"] == 0
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


# -- versioned documents (spec §Versioned org policy) ----------------------------


def test_apply_policy_ignores_older_versions_and_reports(tmp_path):
    from types import SimpleNamespace

    from coworker.remote.joiner import apply_policy, held_policy, policy_version

    manager = SimpleNamespace(org_policy=None)
    app = SimpleNamespace(state=SimpleNamespace(manager=manager))
    assert apply_policy(app, {"version": 3, "audit_export": {"enabled": True}}, state=tmp_path) == (True, 3)
    assert policy_version(held_policy(app)) == 3
    # Older: ignored, held version unchanged, file untouched.
    assert apply_policy(app, {"version": 2}, state=tmp_path) == (False, 3)
    assert load_policy(tmp_path)["version"] == 3
    # Same version re-sent (welcome after reconnect): applied idempotently.
    assert apply_policy(app, {"version": 3, "audit_export": {"enabled": False}}, state=tmp_path) == (True, 3)
    assert held_policy(app)["audit_export"] == {"enabled": False}
    # Unversioned documents (desktop controllers, tests) always apply.
    assert apply_policy(app, {"audit_export": {"enabled": True}}, state=tmp_path) == (True, 0)


async def test_box_checks_every_interval_and_controller_records_status(tmp_path, monkeypatch):
    """The box asks with the version it holds; the controller records what the
    box SAYS (checked / applied), answers current or the newer document; an
    admin minting v2 reaches the box within one check without any push."""
    import httpx

    from coworker.remote import joiner
    from coworker.remote.joiner import parse_join_url, run_joined
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setattr(joiner, "POLICY_CHECK_SECONDS", 0.3)
    current = {"doc": {"org_id": "org-1", "version": 1, "audit_export": {"enabled": False}}}
    status_log: list[tuple[int, str]] = []
    ctrl = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    ctrl.policy_for = lambda mid, known: current["doc"]
    ctrl.policy_status = lambda mid, known, version, outcome: status_log.append((version, outcome))
    ctrl_app = create_app(ctrl)
    box = SessionManager(data_dir=tmp_path / "box-data")
    box_app = create_app(box)
    async with _Server(ctrl_app) as server:
        async with httpx.AsyncClient() as client:
            join_url = (await client.post(f"{server.base}/v1/remote/arm")).json()["join_url"]
        _, token = parse_join_url(join_url)
        state = tmp_path / "box-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(state=state, controller=server.base, name="vm", token=token,
                       app=box_app, once=True, log=lambda *_: None)
        )
        try:
            for _ in range(100):
                if getattr(box, "org_policy", None):
                    break
                await asyncio.sleep(0.05)
            assert box.org_policy["version"] == 1
            # First check: reports v1, nothing newer → "checked" only.
            for _ in range(100):
                if (1, "checked") in status_log:
                    break
                await asyncio.sleep(0.05)
            assert (1, "checked") in status_log and not any(o == "applied" for _, o in status_log)
            # Admin mints v2. No push: the next check carries it down.
            current["doc"] = {"org_id": "org-1", "version": 2, "audit_export": {"enabled": True, "flush_seconds": 1}}
            for _ in range(200):
                if (2, "applied") in status_log:
                    break
                await asyncio.sleep(0.05)
            assert (2, "applied") in status_log
            assert box.org_policy["version"] == 2
            assert load_policy(state)["version"] == 2
            assert box.get_settings()["org_policy"]["version"] == 2
            assert box.get_settings()["audit_export"]["enabled"] is True
            # Subsequent checks report v2.
            for _ in range(200):
                if (2, "checked") in status_log:
                    break
                await asyncio.sleep(0.05)
            assert (2, "checked") in status_log
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


def test_approver_is_stamped_on_the_gated_tool_row(tmp_path):
    """The Inbox item that gated a tool call records who decided; the audit row
    of that call (joined by call id) carries `approved_by`, and so does OCSF."""
    from coworker.inbox import InboxStore
    from coworker.server.manager import SessionManager

    m = SessionManager(data_dir=tmp_path / "d")
    assert isinstance(m.inbox, InboxStore)
    item = m.inbox.add_approval("s1", "Run `github_push`?", tool_call_id="call_77")
    assert m.inbox.resolve(item.id, "allow", by="erin@l.com")
    assert m.inbox.get(item.id).resolved_by == "erin@l.com"
    assert m.inbox.resolver_of("s1", "call_77") == "erin@l.com"
    assert m.inbox.resolver_of("s1", "call_nope") == ""
    m.note_session_actor("s1", "rohit@l.com")
    m._audit_sink_for("s1")({"session_id": "s1", "tool": "github_push", "arguments": {}, "call_id": "call_77", "approval": "allow", "status": "ok"})
    row = m.audit_store.list(session_id="s1")[0]
    assert row["actor"] == "rohit@l.com" and row["approved_by"] == "erin@l.com"
    ev = to_ocsf(row)
    assert ev["unmapped"]["approved_by"] == "erin@l.com" and ev["unmapped"]["approval"] == "allow"
    # A second resolve is a no-op and never rewrites the decider.
    assert m.inbox.resolve(item.id, "deny", by="someone") is False
    assert m.inbox.get(item.id).resolved_by == "erin@l.com"
