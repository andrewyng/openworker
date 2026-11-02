"""OB-6: sending recordings to Langfuse or an OpenTelemetry endpoint (phase-2 design §10).

A recording is built in a real store with content planted everywhere a real session puts it:
the user's text, OpenWorker's per-turn context block, the system prompt and tool list, the
AI's answer and its thinking, a tool call's arguments and result (inside the next call's
history too), the auto-approver's input, answer and reason, a summary, the session title
call, a failed call's error text and cost, who answered a card, the machine's name, the
session title, and two secrets. Every planted text holds `PLANT`.

- Metadata only: no planted text in anything sent; a value planted in an exported field is
  sent (the leak test of phase-2/lab/leak_test.py, as a repo test).
- Each switch alone lets out only its own content, and rules R1 to R5 hold.
- Sending: each step once per destination (marks survive a restart), retry and back-off,
  a refused key, a refused span, against a fake OTLP endpoint on 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from coworker.observability import export as ex
from coworker.observability import export_spans as sp
from coworker.observability.capture import store_exchange
from coworker.observability.store import RecordingStore

VECTORS = Path(__file__).parent / "corpora" / "observability" / "golden-phase2" / "vectors" / "masking.json"

STORED_SECRET = "hunter2-correct-horse-PLANT"
KEY_SHAPED = "sk-PLANTabcdefghijklmnopqrstu"
T0 = 1_790_000_000_000


# -- a recording with content planted everywhere ------------------------------------------------
def _exchange(store: RecordingStore, request: dict, response: dict) -> dict:
    return store_exchange({
        "method": "POST", "url": "https://api.anthropic.com/v1/messages",
        "request_headers": {"content-type": "application/json"},
        "request_body": json.dumps(request).encode("utf-8"),
        "status": 200, "response_headers": {}, "body": json.dumps(response),
    }, store.put_piece)


def _reply(*blocks: dict, stop: str = "end_turn") -> dict:
    return {"type": "message", "role": "assistant", "content": list(blocks), "stop_reason": stop}


def _usage(i: int = 100, o: int = 10) -> dict:
    return {"reported": True, "input_fresh": i, "cache_read": 0, "cache_write": None, "output": o, "reasoning": None}


def _model(sid, tid, step_id, seq, at, purpose, exchange, *, parent=None, call_id=None, after_turn=False, status="ok",
           model="anthropic:claude-EXPORTED-zq81", **attrs):
    a = {"model": model, "provider": "anthropic", "purpose": purpose, "stream": False,
         "finish_reason": "end_turn", "tools_offered": 1, "usage": _usage(),
         "provider_facts": {"cost_reported": 98765.4321, "served_model": "claude-x"}}
    a.update(attrs)
    return {"step_id": step_id, "turn_id": tid, "session_id": sid, "parent_step_id": parent, "seq": seq,
            "kind": "model", "name": "model", "started_at": at, "duration_ms": 400, "status": status,
            "call_id": call_id, "after_turn": after_turn, "attrs": a,
            "content": {"exchange": [exchange]} if exchange else {}}


def plant_turn(store: RecordingStore, sid: str = "sess-1", tid: str = "turn-1", at: int = T0,
               *, long_text: str = "") -> None:
    user = (f"USER-PLANT-u1 my key {KEY_SHAPED} and {STORED_SECRET} {long_text}"
            "<system-context>CTX-PLANT-folders C:\\Users\\PLANT-home</system-context>")
    system = "SYSTEM-PLANT-instructions"
    tools = [{"name": "run_shell", "description": "TOOLDEF-PLANT", "input_schema": {"type": "object"}}]
    m1 = _exchange(store, {"model": "claude-x", "system": system, "tools": tools,
                           "messages": [{"role": "user", "content": user}]},
                   _reply({"type": "thinking", "thinking": "THINK-PLANT-reasoning"},
                          {"type": "text", "text": "ASSIST-PLANT-a1"},
                          {"type": "tool_use", "id": "c1", "name": "run_shell", "input": {"command": "ARGS-PLANT-cmd"}},
                          stop="tool_use"))
    rv = _exchange(store, {"model": "claude-x", "system": "You review actions.",
                           "messages": [{"role": "user", "content": "REVIEWER-IN-PLANT run_shell ARGS-PLANT-cmd"}]},
                   _reply({"type": "text", "text": "REVIEWER-OUT-PLANT allow"}))
    m2 = _exchange(store, {"model": "claude-x", "system": system, "tools": tools, "messages": [
        {"role": "user", "content": user},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "run_shell",
                                           "input": {"command": "ARGS-PLANT-cmd"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "RESULT-PLANT-out"}]}]},
        _reply({"type": "text", "text": "ASSIST-PLANT-a2 final"}))
    sm = _exchange(store, {"model": "claude-x", "messages": [
        {"role": "user", "content": "SUMMARY-IN-PLANT summarise RESULT-PLANT-out"}]},
        _reply({"type": "text", "text": "SUMMARY-OUT-PLANT"}))
    ti = _exchange(store, {"model": "claude-x", "messages": [{"role": "user", "content": "TITLE-IN-PLANT"}]},
                   _reply({"type": "text", "text": "TITLE-OUT-PLANT"}))
    failed = _exchange(store, {"model": "claude-x", "messages": [{"role": "user", "content": user}]}, {})
    store.write_turn({
        "turn_id": tid, "session_id": sid, "seq": 1, "kind": "run", "trigger": "user", "started_at": at,
        "duration_ms": 9000, "status": "completed", "approval_mode": "ask", "attended": True,
        "stamp": {"machine": {"name": "PLANT-machine-name", "app_version": "9.9.9"}}, "schema_version": 1,
    })
    steps = [
        {"step_id": f"{tid}-ctx", "kind": "context", "name": "context", "seq": 1, "started_at": at, "duration_ms": 1,
         "status": "ok", "attrs": {"size_chars": 120, "block_hash": "abc"},
         "content": {"agents_md": "AGENTS-PLANT"}},
        _model(sid, tid, f"{tid}-m1", 2, at + 10, "main", m1),
        {"step_id": f"{tid}-aa", "kind": "auto_approver", "name": "run_shell", "seq": 3, "started_at": at + 500,
         "duration_ms": 300, "status": "ok", "call_id": "c1",
         "attrs": {"mode": "live", "result": "allow", "reviewer": {"kind": "session_model", "model": "anthropic:claude-x"}},
         "content": {"reason": "REVIEWER-REASON-PLANT", "questions": [{"name": "QUESTIONS-PLANT"}]}},
        _model(sid, tid, f"{tid}-rm", 4, at + 510, "auto_approver", rv, parent=f"{tid}-aa", call_id="c1"),
        {"step_id": f"{tid}-w", "kind": "wait", "name": "approval", "seq": 5, "started_at": at + 900,
         "duration_ms": 1000, "status": "ok",
         "attrs": {"kind": "approval", "where": "session", "outcome": "approved", "attended": True, "for_call_id": "c1"},
         "content": {"resolved_by": "PLANT-person-bob"}},
        {"step_id": f"{tid}-t", "kind": "tool", "name": "run_shell", "seq": 6, "started_at": at + 2000,
         "duration_ms": 500, "status": "ok", "call_id": "c1",
         "attrs": {"risk_class": "exec", "exit_code": 0, "result_bytes": 16,
                   "decision": {"origin": "user", "path": [{"stage": "permission", "outcome": "asked"}]}},
         "content": {"decision_details": "DECISION-PLANT", "blocked": "BLOCKED-PLANT"}},
        _model(sid, tid, f"{tid}-m2", 7, at + 2600, "main", m2),
        {"step_id": f"{tid}-sum", "kind": "summary", "name": "summary", "seq": 8, "started_at": at + 3200,
         "duration_ms": 900, "status": "ok", "attrs": {"reason": "overflow", "tokens_after": 700}},
        _model(sid, tid, f"{tid}-sm", 9, at + 3210, "summary", sm, parent=f"{tid}-sum"),
        _model(sid, tid, f"{tid}-err", 10, at + 4200, "main", failed, status="error",
               error={"type": "rate_limit", "text": "ERRTEXT-PLANT https://PLANT.example/v1"}, usage={"reported": False}),
        {"step_id": f"{tid}-n", "kind": "notice", "name": "interrupted", "seq": 11, "started_at": at + 4700,
         "duration_ms": 0, "status": "ok", "attrs": {}},
        _model(sid, tid, f"{tid}-title", 12, at + 8000, "title", ti, after_turn=True),
    ]
    for s in steps:
        s.setdefault("turn_id", tid)
        s.setdefault("session_id", sid)
        store.write_step(s)
    store.flush()


class Captured:
    """A `post` for the exporter that keeps what it was given and answers 200."""

    def __init__(self, answers=None):
        self.bodies: list[dict] = []
        self.headers: list[dict] = []
        self.answers = list(answers or [])

    def __call__(self, url, body, headers, timeout):
        self.bodies.append(json.loads(body))
        self.headers.append(headers)
        return self.answers.pop(0) if self.answers else (200, "")

    def text(self) -> str:
        return json.dumps(self.bodies, ensure_ascii=False)

    def spans(self) -> list[dict]:
        return [s for b in self.bodies for r in b["resourceSpans"] for ss in r["scopeSpans"] for s in ss["spans"]]


def _attr(span: dict, key: str):
    for a in span["attributes"]:
        if a["key"] == key:
            v = a["value"]
            return next(iter(v.values()))
    return None


def export_once(tmp_path, include: dict, *, dest=None, long_text: str = "") -> Captured:
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    plant_turn(store, long_text=long_text)
    cap = Captured()
    cfg = ex.ExportConfig(destination=dest or ex.Destination(kind="langfuse", host="http://127.0.0.1:9"),
                          headers={"Authorization": "Basic x"}, include=include, since_ms=0)
    exporter = ex.Exporter(store, cfg, post=cap, secret_values=lambda: [STORED_SECRET],
                           session_title=lambda _sid: "TITLE-PLANT-session")
    exporter.flush_once()
    store.close()
    return cap


OFF = {"messages": False, "tool_io": False, "context": False, "names": False}


def on(*keys: str) -> dict:
    return {**OFF, **{k: True for k in keys}}


# -- 2. metadata only carries no content --------------------------------------------------------------
@pytest.mark.parametrize("dest", [ex.Destination(kind="langfuse", host="http://127.0.0.1:9"),
                                  ex.Destination(kind="otlp", url="http://127.0.0.1:9/v1/traces")])
def test_metadata_only_export_carries_no_content(tmp_path, dest):
    cap = export_once(tmp_path, OFF, dest=dest)
    sent = cap.text()
    assert len(cap.spans()) == 12  # the turn and 11 steps; the notice is an event on the turn
    # The positive control: a value planted in an exported field is found by the same search.
    assert "claude-EXPORTED-zq81" in sent
    # Nothing planted in content is.
    assert "PLANT" not in sent
    assert "98765" not in sent  # provider-reported cost (R5)
    assert "C:\\\\Users" not in sent and "Users" not in sent
    # Metadata is there: names, kinds, counts, the verdict, the notice.
    spans = cap.spans()
    names = {s["name"] for s in spans}
    assert {"invoke_agent coworker", "openworker.context.build", "chat claude-EXPORTED-zq81",
            "openworker.policy.review run_shell", "openworker.approval.wait", "execute_tool run_shell",
            "openworker.compaction"} <= names
    review = next(s for s in spans if s["name"] == "openworker.policy.review run_shell")
    assert _attr(review, "openworker.reviewer.result") == "allow"
    root = next(s for s in spans if s["name"] == "invoke_agent coworker")
    assert [e["name"] for e in root["events"]] == ["openworker.notice.interrupted"]
    failed = next(s for s in spans if _attr(s, "openworker.step.id") == "turn-1-err")
    assert _attr(failed, "error.type") == "rate_limit" and failed["status"]["code"] == 2


# -- 3. each switch lets out only its own content --------------------------------------------------------
ALL_PLANTS = ["USER-PLANT", "ASSIST-PLANT", "CTX-PLANT", "SYSTEM-PLANT", "TOOLDEF-PLANT", "ARGS-PLANT",
              "RESULT-PLANT", "REVIEWER-IN-PLANT", "REVIEWER-OUT-PLANT", "REVIEWER-REASON-PLANT",
              "SUMMARY-IN-PLANT", "SUMMARY-OUT-PLANT", "TITLE-IN-PLANT", "TITLE-OUT-PLANT",
              "TITLE-PLANT-session", "PLANT-person-bob", "PLANT-machine-name"]
NEVER = ["THINK-PLANT", "ERRTEXT-PLANT", "98765", "QUESTIONS-PLANT", "DECISION-PLANT", "AGENTS-PLANT",
         "BLOCKED-PLANT", STORED_SECRET, KEY_SHAPED]

CASES = {
    # R1, R2, R3: the user's and the AI's own text, the title call; no tool parts, no context
    # block, no reviewer or summary text.
    "messages": (on("messages"), {"USER-PLANT", "ASSIST-PLANT", "TITLE-IN-PLANT", "TITLE-OUT-PLANT"}),
    # Tool spans only: the reviewer's input and reason need the messages switch too (R3).
    "tool_io": (on("tool_io"), {"ARGS-PLANT", "RESULT-PLANT"}),
    "context": (on("context"), {"SYSTEM-PLANT", "TOOLDEF-PLANT"}),
    "names": (on("names"), {"TITLE-PLANT-session", "PLANT-person-bob", "PLANT-machine-name"}),
    "messages+tool_io": (on("messages", "tool_io"),
                         {"USER-PLANT", "ASSIST-PLANT", "ARGS-PLANT", "RESULT-PLANT", "REVIEWER-IN-PLANT",
                          "REVIEWER-OUT-PLANT", "REVIEWER-REASON-PLANT", "SUMMARY-IN-PLANT", "SUMMARY-OUT-PLANT",
                          "TITLE-IN-PLANT", "TITLE-OUT-PLANT"}),
    "messages+context": (on("messages", "context"),
                         {"USER-PLANT", "ASSIST-PLANT", "CTX-PLANT", "SYSTEM-PLANT", "TOOLDEF-PLANT",
                          "TITLE-IN-PLANT", "TITLE-OUT-PLANT"}),
    "everything": (on("messages", "tool_io", "context", "names"), set(ALL_PLANTS)),
}


@pytest.mark.parametrize("case", list(CASES))
def test_each_switch_lets_out_only_its_content(tmp_path, case):
    include, allowed = CASES[case]
    sent = export_once(tmp_path, include).text()
    for plant in ALL_PLANTS:
        assert (plant in sent) == (plant in allowed), f"{case}: {plant}"
    for plant in NEVER:  # R5 and R4, whatever the switches say
        assert plant not in sent, f"{case}: {plant}"


def test_tool_parts_inside_messages_are_masked_unless_the_tool_switch_is_on(tmp_path):
    cap = export_once(tmp_path, on("messages"))
    main = [s for s in cap.spans() if _attr(s, "openworker.step.id") == "turn-1-m2"][0]
    history = json.loads(_attr(main, "gen_ai.input.messages"))
    parts = [p for m in history for p in m["parts"]]
    call = next(p for p in parts if p["type"] == "tool_call")
    result = next(p for p in parts if p["type"] == "tool_call_response")
    # R1: the part, its id and its tool name stay; its content does not.
    assert (call["id"], call["name"], call["arguments"]) == ("c1", "run_shell", sp.TOOL_ARGS_OUT)
    assert (result["id"], result["response"]) == ("c1", sp.TOOL_RESULT_OUT)
    # R2: the context block is cut out of the user's text, the rest kept.
    user = next(p["content"] for m in history if m["role"] == "user" for p in m["parts"] if p["type"] == "text")
    assert user.startswith("USER-PLANT-u1") and user.endswith(sp.CONTEXT_OUT)
    # R4: the stored secret and the key-shaped one are masked.
    assert user.count(sp.SECRET) == 2
    # Reasoning keeps its marker, never its text (R5).
    reply = json.loads(_attr([s for s in cap.spans() if _attr(s, "openworker.step.id") == "turn-1-m1"][0],
                             "gen_ai.output.messages"))
    assert {"type": "reasoning", "content": sp.REASONING} in reply[0]["parts"]
    # The turn's own input and output (Langfuse).
    root = cap.spans()[0]
    assert _attr(root, "langfuse.trace.input").endswith(sp.CONTEXT_OUT)
    assert _attr(root, "langfuse.trace.output") == "ASSIST-PLANT-a2 final"


def test_tool_switch_with_messages_lets_tool_parts_into_messages(tmp_path):
    cap = export_once(tmp_path, on("messages", "tool_io"))
    main = [s for s in cap.spans() if _attr(s, "openworker.step.id") == "turn-1-m2"][0]
    history = _attr(main, "gen_ai.input.messages")
    assert "ARGS-PLANT-cmd" in history and "RESULT-PLANT-out" in history
    tool = [s for s in cap.spans() if s["name"] == "execute_tool run_shell"][0]
    assert json.loads(_attr(tool, "gen_ai.tool.call.arguments")) == {"command": "ARGS-PLANT-cmd"}
    assert _attr(tool, "gen_ai.tool.call.result") == "RESULT-PLANT-out"
    # gen_ai.tool.call.id only on the tool's own span (V-22).
    assert [s["name"] for s in cap.spans() if _attr(s, "gen_ai.tool.call.id")] == ["execute_tool run_shell"]


def test_exported_text_is_cut_at_8_kb(tmp_path):
    cap = export_once(tmp_path, on("messages"), long_text="x" * 20_000)
    main = [s for s in cap.spans() if _attr(s, "openworker.step.id") == "turn-1-m1"][0]
    user = json.loads(_attr(main, "gen_ai.input.messages"))[0]["parts"][0]["content"]
    assert user.endswith(" more bytes]") and "[cut: " in user
    kept = user[: user.index("[cut: ")]
    assert len(kept.encode("utf-8")) == sp.CUT_BYTES


def test_masking_and_cut_match_the_shared_vectors():
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    for v in vectors["mask"]:
        assert sp.Masker(v["secrets"]).mask(v["text"]) == v["expected"], v["text"]
    for v in vectors["cut"]:
        text = "a" * 8191 + "é" + "b" * 10
        assert len(text.encode("utf-8")) == v["text_bytes"]
        out = sp.cut(text)
        kept = out[: out.index("[cut:")]
        assert len(kept.encode("utf-8")) == v["expected_kept_bytes"]
        assert out.endswith(v["expected_tail"])
    # The patterns are the ones in attributes.yaml export.content_switches.masking.
    assert sp.CUT_BYTES == 8192 and len(sp.MASK_PATTERNS) == 7


def test_credential_looking_environment_variables_are_masked():
    env = {"MY_API_KEY": "PLANT-envkey-123", "SHORT_TOKEN": "abc", "HOME": "PLANT-not-a-secret"}
    m = sp.Masker(sp.env_secret_values(env))
    assert m.mask("a PLANT-envkey-123 b abc PLANT-not-a-secret") == "a [secret masked] b abc PLANT-not-a-secret"


def test_token_numbers_follow_the_verified_rules(tmp_path):
    cap = export_once(tmp_path, OFF)
    spans = cap.spans()
    m1 = [s for s in spans if _attr(s, "openworker.step.id") == "turn-1-m1"][0]
    assert _attr(m1, "gen_ai.usage.input_tokens") == "100"
    assert _attr(m1, "gen_ai.usage.cache_read.input_tokens") == "0"
    assert _attr(m1, "gen_ai.usage.cache_creation.input_tokens") is None  # not reported: left out
    assert json.loads(_attr(m1, "langfuse.observation.usage_details")) == {"input": 100, "input_cache_read": 0, "output": 10}
    root = spans[0]
    assert _attr(root, "gen_ai.usage.input_tokens") is None  # never on a parent span
    assert _attr(root, "openworker.usage.failed_calls") == "1"
    assert _attr(root, "langfuse.trace.tags") == {"values": [{"stringValue": "usage-incomplete"}]}
    err = [s for s in spans if _attr(s, "openworker.step.id") == "turn-1-err"][0]
    assert _attr(err, "langfuse.observation.level") == "ERROR"
    assert _attr(err, "langfuse.observation.status_message") == "rate_limit; usage unknown"
    # One trace per turn, children under their parents.
    assert len({s["traceId"] for s in spans}) == 1
    rm = [s for s in spans if _attr(s, "openworker.step.id") == "turn-1-rm"][0]
    aa = [s for s in spans if _attr(s, "openworker.step.id") == "turn-1-aa"][0]
    assert rm["parentSpanId"] == aa["spanId"] and aa["parentSpanId"] == root["spanId"]
    resource = cap.bodies[0]["resourceSpans"][0]["resource"]["attributes"]
    assert {"key": "openworker.export.schema", "value": {"stringValue": "openworker/1.2"}} in resource
    assert not any(a["key"] == "host.name" for a in resource)  # the machine name: names switch only


# -- 4. each step once; retry; refused key; refused span ------------------------------------------------
class FakeOtlp:
    """A local OTLP/HTTP endpoint. `answers` is a list of status codes for the next posts;
    `refuse` is a set of span ids refused (400) whenever they are in a post."""

    def __init__(self):
        self.answers: list[int] = []
        self.refuse: set[str] = set()
        self.received: list[dict] = []
        self.auth: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.auth.append(self.headers.get("Authorization") or "")
                ids = [s["spanId"] for r in body["resourceSpans"] for ss in r["scopeSpans"] for s in ss["spans"]]
                code = outer.answers.pop(0) if outer.answers else 200
                if code == 200 and outer.refuse & set(ids):
                    code = 400
                if code == 200:
                    outer.received.append(body)
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'{"message":"refused here"}' if code != 200 else b"{}")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/traces"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def span_ids(self) -> list[str]:
        return [s["spanId"] for b in self.received for r in b["resourceSpans"] for ss in r["scopeSpans"] for s in ss["spans"]]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def otlp():
    f = FakeOtlp()
    yield f
    f.close()


def _cfg(url: str, since: int = 0) -> ex.ExportConfig:
    return ex.ExportConfig(destination=ex.Destination(kind="otlp", url=url), headers={"Authorization": "Bearer t"},
                           include=OFF, since_ms=since)


def test_each_step_is_sent_once_and_the_marks_survive_a_restart(tmp_path, otlp):
    db = tmp_path / "recordings.db"
    store = RecordingStore(db, background=False)
    plant_turn(store, tid="turn-1")
    plant_turn(store, tid="turn-2", at=T0 + 20_000)
    exporter = ex.Exporter(store, _cfg(otlp.url))
    assert exporter.flush_once() == 24
    assert exporter.flush_once() == 0  # nothing new
    store.close()
    # A restart: a new store on the same file, a new exporter.
    store = RecordingStore(db, background=False)
    assert ex.Exporter(store, _cfg(otlp.url)).flush_once() == 0
    ids = otlp.span_ids()
    assert len(ids) == len(set(ids)) == 24
    # A step that ends after its turn was sent (a late title call) is sent alone, once.
    store.write_step({"step_id": "turn-1-late", "turn_id": "turn-1", "session_id": "sess-1", "seq": 13,
                      "kind": "model", "name": "model", "started_at": T0 + 9500, "duration_ms": 100, "status": "ok",
                      "after_turn": True, "attrs": {"model": "anthropic:claude-x", "purpose": "title", "usage": _usage()}})
    store.flush()
    assert ex.Exporter(store, _cfg(otlp.url)).flush_once() == 1
    assert len(otlp.span_ids()) == 25
    assert store.export_counts("otlp:" + otlp.url)["sent"] == 2 * 12 + 2 * 1 + 1  # notices ride with their turn
    store.close()


def test_a_new_destination_starts_from_turns_that_end_after_the_change(tmp_path, otlp):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    plant_turn(store, tid="old", at=T0)
    plant_turn(store, tid="new", at=T0 + 60_000)
    ex.Exporter(store, _cfg(otlp.url, since=T0 + 30_000)).flush_once()
    assert {s["traceId"] for b in otlp.received for r in b["resourceSpans"] for ss in r["scopeSpans"]
            for s in ss["spans"]} == {sp.trace_id("new")}
    store.close()


def _wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_retry_with_back_off_then_sent(tmp_path, otlp):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    plant_turn(store)
    otlp.answers = [503, 503, 200]
    exporter = ex.Exporter(store, _cfg(otlp.url), backoff_start=0.05, flush_seconds=0.05)
    seen: list[tuple[str, str]] = []
    exporter.start()
    try:
        assert _wait_for(lambda: seen.append((exporter.state, exporter.last_error)) or len(otlp.span_ids()) == 12)
    finally:
        exporter.stop()
    assert ("retrying", "HTTP 503: {\"message\":\"refused here\"}") in seen
    assert exporter.state == "sending" and exporter.last_error == ""
    store.close()


def test_a_network_error_is_retried(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    plant_turn(store)
    exporter = ex.Exporter(store, _cfg("http://127.0.0.1:1/v1/traces"), backoff_start=0.05)
    with pytest.raises(ex._Retry):
        exporter.flush_once()
    assert store.export_marked("otlp:http://127.0.0.1:1/v1/traces", "turn-1") == set()
    store.close()


def test_a_refused_key_stops_sending_until_the_settings_change(tmp_path, otlp):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    plant_turn(store)
    otlp.answers = [401]
    exporter = ex.Exporter(store, _cfg(otlp.url), backoff_start=0.05, flush_seconds=0.05)
    exporter.start()
    try:
        assert _wait_for(lambda: exporter.state == "failed")
        time.sleep(0.2)
        assert otlp.span_ids() == []  # it stopped: no second try with the same key
        assert exporter.last_error == "The destination refused the key"
        assert store.export_counts("otlp:" + otlp.url)["failed"] == 1
    finally:
        exporter.stop()
    store.close()


def test_a_span_refused_on_its_own_is_dropped_and_the_rest_sent(tmp_path, otlp):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    plant_turn(store)
    otlp.refuse = {sp.step_span_id("turn-1-t")}
    ex.Exporter(store, _cfg(otlp.url)).flush_once()
    ids = otlp.span_ids()
    assert len(ids) == 11 and sp.step_span_id("turn-1-t") not in ids
    assert store.export_counts("otlp:" + otlp.url)["dropped"] == 1
    store.close()


def test_a_turn_deleted_before_it_was_sent_counts_as_dropped(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False, retention_days=7)
    plant_turn(store, at=T0)
    store.export_destination = ("otlp:http://x/v1/traces", 0)
    store.sweep(now_ms=T0 + 8 * 24 * 3600 * 1000)
    assert store.export_counts("otlp:http://x/v1/traces")["dropped"] == 1
    store.close()


def test_batches_hold_at_most_the_batch_size(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    for i in range(3):
        plant_turn(store, tid=f"t{i}", at=T0 + i * 20_000)
    cap = Captured()
    ex.Exporter(store, _cfg("http://127.0.0.1:9/v1/traces"), post=cap, batch_spans=5).flush_once()
    sizes = [len([s for r in b["resourceSpans"] for ss in r["scopeSpans"] for s in ss["spans"]]) for b in cap.bodies]
    assert max(sizes) <= 5 and sum(sizes) == 36
    store.close()


# -- settings, keys and the test recording ------------------------------------------------------------
class FakeSecrets:
    def __init__(self):
        self.data: dict = {}

    def get(self, profile):
        return self.data.get(profile)

    def put(self, profile, value):
        self.data[profile] = json.loads(json.dumps(value))

    def delete(self, profile):
        return self.data.pop(profile, None) is not None

    def _read(self):
        return self.data


def test_settings_keep_keys_out_of_the_settings_file_and_answers(tmp_path):
    secrets = FakeSecrets()
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    obs = ex.ObservabilityExport(tmp_path, secrets, store, post=Captured())
    try:
        assert obs.exporter is None and obs.status()["state"] == "off"
        code, answer = obs.put_settings({
            "destination": {"kind": "langfuse", "host": "https://langfuse.example.com"},
            "secrets": {"public_key": "pk-lf-PLANT-public-3a91", "secret_key": "sk-lf-PLANT-secret-key"},
            "include": {"messages": True}})
        assert code == 200 and answer["ok"]
        assert answer["secrets"] == {"public_key": "pk-lf-••••••••3a91", "secret_key": "sk-lf-••••••••"}
        assert "PLANT" not in json.dumps(answer) and "PLANT" not in json.dumps(obs.get_settings())
        assert "PLANT" not in (tmp_path / ex.SETTINGS_FILE).read_text(encoding="utf-8")
        assert secrets.data[ex.PROFILE]["user"]["secret_key"] == "sk-lf-PLANT-secret-key"
        assert obs.exporter is not None and obs.exporter.config.destination.endpoint == \
            "https://langfuse.example.com/api/public/otel/v1/traces"
        # Off again: the exporter goes away, its thread with it.
        obs.put_settings({"destination": {"kind": "off"}})
        assert obs.exporter is None
        assert not any(t.name == "observability-export" for t in threading.enumerate())
        # Bad input is refused, not saved.
        assert obs.put_settings({"destination": {"kind": "otlp", "url": "ftp://x"}})[0] == 400
        assert obs.put_settings({"retention_days": 12})[0] == 400
    finally:
        obs.close()
        store.close()


def test_the_test_recording_reports_a_refused_key(tmp_path, otlp):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    obs = ex.ObservabilityExport(tmp_path, FakeSecrets(), store)
    try:
        obs.put_settings({"destination": {"kind": "otlp", "url": otlp.url, "header_names": ["Authorization"]},
                          "secrets": {"headers": {"Authorization": "Bearer PLANT-token"}}})
        otlp.answers = [200]
        ok = obs.send_test()
        assert ok["ok"] and ok["text"] == "Sent." and ok["status"] == 200
        assert otlp.auth[-1] == "Bearer PLANT-token"
        [span_names] = [[s["name"] for r in b["resourceSpans"] for ss in r["scopeSpans"] for s in ss["spans"]]
                        for b in otlp.received[-1:]]
        assert span_names == ["invoke_agent coworker", "chat test-model", "execute_tool test_tool"]
        otlp.answers = [401]
        refused = obs.send_test()
        assert refused == {"ok": False, "status": 401, "text": "The destination refused the key", "ms": refused["ms"]}
        otlp.answers = [500]
        assert obs.send_test()["text"].startswith("HTTP 500")
    finally:
        obs.close()
        store.close()


POLICY = {"org_id": "org_1", "org_name": "Acme Corp", "version": 3, "observability_export": {
    "enabled": True, "name": "Security team default", "set_at": "2026-10-12",
    "destination": {"kind": "langfuse", "host": "https://langfuse.acme.internal",
                    "public_key": "pk-lf-POLICY-PLANT-0001", "secret_key": "sk-lf-POLICY-PLANT"},
    "include": {"messages": True, "names": True}}}


def test_a_company_policy_manages_the_page(tmp_path):
    secrets = FakeSecrets()
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    obs = ex.ObservabilityExport(tmp_path, secrets, store, post=Captured())
    try:
        stripped = ex.take_policy_secrets(POLICY, secrets)
        assert "PLANT" not in json.dumps(stripped)  # what policy.json will hold
        assert secrets.data[ex.PROFILE]["policy"]["secret_key"] == "sk-lf-POLICY-PLANT"
        obs.apply_policy(stripped)
        got = obs.get_settings()
        assert got["managed"] == {"by": "Acme Corp", "name": "Security team default", "at": "2026-10-12"}
        assert got["destination"] == {"kind": "langfuse", "host": "https://langfuse.acme.internal"}
        assert got["include"] == {"messages": True, "tool_io": False, "context": False, "names": True}
        assert got["notice"] == "Acme Corp receives this session's messages and names (Langfuse, langfuse.acme.internal)."
        assert "PLANT" not in json.dumps(got)
        assert obs.put_settings({"include": {"tool_io": True}})[0] == 409
        assert obs.put_settings({"destination": {"kind": "off"}})[0] == 409
        assert obs.put_settings({"retention_days": 7})[0] == 200  # on this machine stays the person's
        assert obs.exporter is not None and obs.exporter.config.managed
        assert obs.status()["state"] in ("managed", "sending", "retrying")
        # Anything malformed reads as not managed.
        for bad in ({"observability_export": {"enabled": "yes"}},
                    {"observability_export": {"enabled": True, "destination": {"kind": "smtp"}}},
                    {"observability_export": []}):
            assert ex.managed_export(bad) is None
        # The policy taken away: the person's own settings are back, the policy's keys gone.
        obs.apply_policy(ex.take_policy_secrets({"org_id": "org_1"}, secrets))
        assert obs.get_settings()["managed"] is None and "policy" not in (secrets.data.get(ex.PROFILE) or {})
    finally:
        obs.close()
        store.close()
