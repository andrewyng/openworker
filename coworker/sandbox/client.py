"""The workspace client: JSON-RPC to a tool runner over a pipe.

It does not know what is on the other end of the pipe (a local process, an OpenShell
sandbox, later another machine). It gets a function that opens a fresh pipe, and it uses
that function again whenever the pipe breaks.

After a break (design doc, section 4c):
- same runner instance: send `session.resume`; the runner replays the results we missed,
  and requests still waiting are sent again under the SAME id, which the runner runs once;
- a new runner instance (the sandbox restarted): every waiting request fails with
  RUNNER_RESTARTED. A command is never sent again by the client on its own, because
  commands are not safe to repeat.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Optional

from .runner import protocol as P
from .transport import Transport

log = logging.getLogger(__name__)

NotifyHandler = Callable[[str, dict[str, Any]], None]
_ACK_EVERY = 20
_RECONNECT_TRIES = 5


class _Pending:
    def __init__(self, frame: bytes, on_notify: Optional[NotifyHandler]) -> None:
        self.frame = frame
        self.on_notify = on_notify
        self.done = threading.Event()
        self.result: Optional[dict[str, Any]] = None
        self.error: Optional[P.RunnerError] = None


class RunnerClient:
    def __init__(self, open_transport: Callable[[], Transport], *, ping_seconds: float = P.PING_SECONDS) -> None:
        self._open = open_transport
        self._ping_seconds = ping_seconds
        self._transport: Optional[Transport] = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._connect_lock = threading.Lock()  # one (re)connect at a time
        self._pending: dict[str, _Pending] = {}
        self._last_seq = 0
        self._acked = 0
        self._generation = 0
        self._closed = False
        self.instance_id: Optional[str] = None
        self.hello: dict[str, Any] = {}
        self.restarts = 0  # how many times the runner came back as a new instance

    # -- connection -------------------------------------------------------------------
    def connect(self) -> dict[str, Any]:
        with self._connect_lock:
            return self._connect_locked()

    def _connect_locked(self) -> dict[str, Any]:
        transport = self._open()
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            old, self._transport = self._transport, transport
        if old is not None:
            old.close()
        threading.Thread(target=self._read_loop, args=(transport, generation), daemon=True).start()
        hello = self._call_on(transport, "runner.hello", {"protocol_version": P.PROTOCOL_VERSION}, timeout=20)
        previous, self.instance_id, self.hello = self.instance_id, hello["instance_id"], hello
        if previous is None:
            threading.Thread(target=self._ping_loop, daemon=True).start()
        elif previous != self.instance_id:
            self.restarts += 1
            self._last_seq = self._acked = 0
            self._fail_all(P.RunnerError(P.RUNNER_RESTARTED, "the sandbox restarted; the outcome of this command is unknown"))
        else:
            self._call_on(transport, "session.resume", {"last_seq": self._last_seq}, timeout=20)
            with self._state_lock:
                waiting = [p.frame for p in self._pending.values() if not p.done.is_set()]
            for frame in waiting:  # same ids: the runner answers, it does not run them twice
                self._send(transport, frame)
        return hello

    def detach(self) -> None:
        """The provider is about to replace the runner on purpose (a sandbox restart). Drop
        this connection without the automatic reconnect, which would chase a runner that is
        going away. `connect()` follows."""
        with self._connect_lock:
            with self._state_lock:
                self._generation += 1  # the old read loop sees a newer generation and stops
                transport, self._transport = self._transport, None
            if transport is not None:
                transport.close()

    def _reconnect(self, generation: int) -> None:
        with self._connect_lock:
            if self._closed or generation != self._generation:
                return  # someone already reconnected, or we are shutting down
            for attempt in range(_RECONNECT_TRIES):
                try:
                    self._connect_locked()
                    return
                except Exception as exc:
                    log.warning("runner reconnect %d/%d failed: %s", attempt + 1, _RECONNECT_TRIES, exc)
                    time.sleep(min(0.25 * 2**attempt, 3.0))
            self._fail_all(P.RunnerError(P.INTERNAL_ERROR, "lost the connection to the tool runner"))

    def close(self) -> None:
        self._closed = True
        with self._state_lock:
            transport, self._transport = self._transport, None
        if transport is not None:
            transport.close()
        self._fail_all(P.RunnerError(P.INTERNAL_ERROR, "the workspace client was closed"))

    # -- calls ------------------------------------------------------------------------
    def call(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        on_notify: Optional[NotifyHandler] = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            transport = self._transport
        if transport is None:
            raise P.RunnerError(P.INTERNAL_ERROR, "not connected to a tool runner")
        return self._call_on(transport, method, params or {}, timeout=timeout, on_notify=on_notify)

    def _call_on(
        self,
        transport: Transport,
        method: str,
        params: dict[str, Any],
        *,
        timeout: Optional[float],
        on_notify: Optional[NotifyHandler] = None,
    ) -> dict[str, Any]:
        req_id = f"r-{uuid.uuid4().hex[:12]}"
        pending = _Pending(P.encode(P.request(req_id, method, params)), on_notify)
        with self._state_lock:
            self._pending[req_id] = pending
        try:
            self._send(transport, pending.frame)
            if not pending.done.wait(timeout):
                raise P.RunnerError(P.INTERNAL_ERROR, f"no answer from the tool runner to {method}")
        finally:
            with self._state_lock:
                self._pending.pop(req_id, None)
        if pending.error is not None:
            raise pending.error
        assert pending.result is not None
        return pending.result

    def _send(self, transport: Transport, frame: bytes) -> None:
        try:
            with self._send_lock:
                transport.send(frame)
        except (OSError, ValueError):
            pass  # the read loop sees the break and reconnects; the request is sent again then

    def _fail_all(self, error: P.RunnerError) -> None:
        with self._state_lock:
            waiting = list(self._pending.values())
        for pending in waiting:
            if not pending.done.is_set():
                pending.error = error
                pending.done.set()

    # -- incoming ---------------------------------------------------------------------
    def _read_loop(self, transport: Transport, generation: int) -> None:
        while True:
            try:
                line = transport.recv_line()
            except (OSError, ValueError):
                line = None
            if line is None:
                break
            frame = P.decode(line)
            if frame is None:
                log.debug("dropped a line that is not a frame: %r", line[:120])
                continue
            self._handle(frame)
        if not self._closed and generation == self._generation:
            threading.Thread(target=self._reconnect, args=(generation,), daemon=True).start()

    def _handle(self, frame: dict[str, Any]) -> None:
        if "method" in frame:  # a notification: the live view
            params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
            with self._state_lock:
                pending = self._pending.get(str(params.get("id")))
            if pending is not None and pending.on_notify is not None:
                try:
                    pending.on_notify(frame["method"], params)
                except Exception:
                    log.exception("live-output handler failed")
            return
        body = frame.get("result") if "result" in frame else (frame.get("error") or {}).get("data") or {}
        seq = body.get("seq") if isinstance(body, dict) else None
        if isinstance(seq, int):
            self._last_seq = max(self._last_seq, seq)
            if self._last_seq - self._acked >= _ACK_EVERY:
                self._acked = self._last_seq
                with self._state_lock:
                    transport = self._transport
                if transport is not None:
                    self._send(transport, P.encode(P.notification("session.ack", {"seq": self._acked})))
        with self._state_lock:
            pending = self._pending.get(str(frame.get("id")))
        if pending is None or pending.done.is_set():
            return  # a replayed result nobody waits for any more
        if "error" in frame:
            err = frame["error"] or {}
            pending.error = P.RunnerError(int(err.get("code", P.INTERNAL_ERROR)), str(err.get("message", "")), err.get("data"))
        else:
            pending.result = frame.get("result") or {}
        pending.done.set()

    def _ping_loop(self) -> None:
        while not self._closed:
            time.sleep(self._ping_seconds)
            with self._state_lock:
                transport = self._transport
            if transport is not None and not self._closed:
                self._send(transport, P.encode(P.notification("$/ping")))
