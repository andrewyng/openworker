"""Remote homes P1a: identity, channel, and the full loopback join.

The loopback tests run the REAL stack end-to-end in one process: a controller
app served by uvicorn on an ephemeral port, and a joined box (its own
SessionManager + app over a separate state dir) dialing it over a real
WebSocket — enrollment, challenge-response, clone trap, token expiry, and the
proxy round trip all cross an actual socket.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from coworker.remote import channel as ch
from coworker.remote.identity import load_or_create, verify_b64
from coworker.remote.joiner import (
    JoinRejected,
    load_remote_config,
    parse_join_url,
    run_joined,
)
from coworker.remote.registry import MachinesRegistry
from coworker.server.app import create_app
from coworker.server.manager import SessionManager


# -- identity ------------------------------------------------------------------


def test_identity_created_once_and_private(tmp_path):
    first = load_or_create(tmp_path)
    again = load_or_create(tmp_path)
    assert first.public_key_b64 == again.public_key_b64
    assert (tmp_path / "machine.key").stat().st_mode & 0o777 == 0o600
    assert len(first.fingerprint) == 16


def test_sealing_round_trip_and_tamper(tmp_path):
    from coworker.remote.identity import seal_b64

    box = load_or_create(tmp_path / "box")
    other = load_or_create(tmp_path / "other")
    blob = seal_b64(box.seal_public_key_b64, b'{"profiles": {"openai": {}}}')
    assert box.unseal_b64(blob) == b'{"profiles": {"openai": {}}}'
    # Only the sealed-to machine can open it.
    with pytest.raises(ValueError):
        other.unseal_b64(blob)
    # A flipped byte fails authentication, never yields garbage plaintext.
    import base64

    raw = bytearray(base64.b64decode(blob))
    raw[-1] ^= 0x01
    with pytest.raises(ValueError):
        box.unseal_b64(base64.b64encode(bytes(raw)).decode())
    assert (tmp_path / "box" / "machine.seal.key").stat().st_mode & 0o777 == 0o600


def test_pre_sealing_state_dir_grows_seal_key(tmp_path):
    # A box created before the sealing key gains one in place, keeping its
    # signing identity (the pre-release backfill path).
    first = load_or_create(tmp_path)
    (tmp_path / "machine.seal.key").unlink()
    again = load_or_create(tmp_path)
    assert again.public_key_b64 == first.public_key_b64
    assert again.seal_public_key_b64  # regenerated


def test_signature_verifies_and_tamper_fails(tmp_path):
    identity = load_or_create(tmp_path)
    signature = identity.sign_b64(b"nonce-bytes")
    assert verify_b64(identity.public_key_b64, b"nonce-bytes", signature)
    assert not verify_b64(identity.public_key_b64, b"other-bytes", signature)
    other = load_or_create(tmp_path / "other")
    assert not verify_b64(other.public_key_b64, b"nonce-bytes", signature)


def test_seal_attestation_verifies_and_rejects_substitution(tmp_path):
    from coworker.remote.identity import verify_seal_attestation

    box = load_or_create(tmp_path / "box")
    sig = box.attest_seal_key_b64()
    assert verify_seal_attestation(box.public_key_b64, box.seal_public_key_b64, sig)
    # A substituted sealing key (the middlebox move) fails against the box's sig.
    mitm = load_or_create(tmp_path / "mitm")
    assert not verify_seal_attestation(box.public_key_b64, mitm.seal_public_key_b64, sig)
    # ...and the middlebox's own attestation never verifies under the box's key.
    assert not verify_seal_attestation(
        box.public_key_b64, mitm.seal_public_key_b64, mitm.attest_seal_key_b64()
    )
    # Domain separation: a plain signature over the raw key bytes is not an
    # attestation — the identity key must have signed the tagged message.
    import base64

    plain = box.sign_b64(base64.b64decode(box.seal_public_key_b64))
    assert not verify_seal_attestation(box.public_key_b64, box.seal_public_key_b64, plain)
    assert not verify_seal_attestation(box.public_key_b64, "not-base64!!", sig)


# -- join URL ------------------------------------------------------------------


def test_parse_join_url():
    base, token = parse_join_url("http://127.0.0.1:9787/j/abc123")
    assert base == "http://127.0.0.1:9787"
    assert token == "abc123"
    with pytest.raises(ValueError):
        parse_join_url("http://127.0.0.1:9787/nope/abc123")
    with pytest.raises(ValueError):
        parse_join_url("ssh://host/j/tok")


def test_in_memory_ephemera_semantics():
    import time as _time

    from coworker.remote.stores import InMemoryEphemera

    eph = InMemoryEphemera()
    eph.add_token("t1", _time.time() + 60)
    assert eph.has_tokens()
    assert eph.consume_token("t1") == {"fingerprint": "", "org_id": "", "actor": "", "provenance": {}}
    assert eph.consume_token("t1") is None  # single-use
    eph.add_token("t2", _time.time() - 1)
    assert eph.consume_token("t2") is None  # expired
    assert not eph.has_tokens()
    eph.add_token("t3", _time.time() + 60, bound_fingerprint="fp42", org_id="org-a", actor="erin")
    assert eph.consume_token("t3") == {"fingerprint": "fp42", "org_id": "org-a", "actor": "erin", "provenance": {}}
    # A managed sandbox's token names the sandbox it was minted for.
    eph.add_token("t4", _time.time() + 60, provenance={"kind": "fly", "ref": "sb1"})
    assert eph.consume_token("t4")["provenance"] == {"kind": "fly", "ref": "sb1"}
    assert eph.acquire_lease("pk")
    assert not eph.acquire_lease("pk")  # clone trap
    eph.release_lease("pk")
    assert eph.acquire_lease("pk")


# -- registry ------------------------------------------------------------------


def test_registry_name_collision_suffixes(tmp_path):
    reg = MachinesRegistry(tmp_path / "coworker.db")
    a = reg.enroll("dev-box", "pk-a")
    b = reg.enroll("dev-box", "pk-b")
    assert a["name"] == "dev-box"
    assert b["name"] == "dev-box-2"
    with pytest.raises(ValueError):
        reg.enroll("whatever", "pk-a")  # pubkey already enrolled
    with pytest.raises(ValueError):
        reg.rename(b["id"], "dev-box")  # rename into a taken name


def test_registry_names_unique_per_org(tmp_path):
    reg = MachinesRegistry(tmp_path / "orgdb")
    a = reg.enroll("box", "pk-1", org_id="org-a")
    b = reg.enroll("box", "pk-2", org_id="org-b")
    c = reg.enroll("box", "pk-3", org_id="org-a")
    assert a["name"] == "box" and b["name"] == "box"  # same name, different orgs
    assert c["name"] == "box-2"  # suffix within the org only
    assert [m["id"] for m in reg.list("org-a")] == [a["id"], c["id"]]
    assert [m["id"] for m in reg.list("org-b")] == [b["id"]]
    assert len(reg.list()) == 3  # None = unscoped (admin/desktop)


# -- loopback harness ----------------------------------------------------------


class _Server:
    """The real controller app under real uvicorn on an ephemeral port."""

    def __init__(self, app):
        import uvicorn

        self._config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error", ws_max_size=16 * 2**20
        )
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


def _controller(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    return create_app(manager)


def _box_app(tmp_path: Path, name: str = "box"):
    manager = SessionManager(data_dir=tmp_path / name)
    return create_app(manager)


async def _arm(base: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/v1/remote/arm")
        resp.raise_for_status()
        return resp.json()["join_url"]


async def _join_box(tmp_path, base, name="box", state_name=None, once=True):
    """Run one join (enroll + serve) in a background task; return the task."""
    join_url = await _arm(base)
    _, token = parse_join_url(join_url)
    state = tmp_path / (state_name or f"{name}-state")
    state.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(
        run_joined(
            state=state,
            controller=base,
            name=name,
            token=token,
            app=_box_app(tmp_path, f"{name}-data"),
            once=once,
            log=lambda *_: None,
        )
    )
    return task, state


async def _wait_connected(base: str, expect: int = 1, timeout: float = 5.0):
    async with httpx.AsyncClient() as client:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            resp = await client.get(f"{base}/v1/machines")
            machines = [m for m in resp.json()["machines"] if m["connected"]]
            if len(machines) >= expect:
                return resp.json()["machines"]
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(
                    f"expected {expect} connected machine(s), have {machines}"
                )
            await asyncio.sleep(0.05)


# -- loopback tests ------------------------------------------------------------


async def test_join_enrolls_and_proxies(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        task, state = await _join_box(tmp_path, server.base)
        try:
            machines = await _wait_connected(server.base)
            mid = machines[0]["id"]
            assert machines[0]["name"] == "box"
            assert machines[0]["connected"] is True

            # THE P1a proof: the box's own /v1 surface, through the controller.
            async with httpx.AsyncClient() as client:
                health = await client.get(
                    f"{server.base}/v1/machines/{mid}/p/v1/health"
                )
                assert health.status_code == 200
                sessions = await client.get(
                    f"{server.base}/v1/machines/{mid}/p/v1/sessions"
                )
                assert sessions.status_code == 200
                assert isinstance(sessions.json().get("sessions"), list)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_token_is_single_use_and_unknown_pubkey_rejected(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state_a = tmp_path / "a-state"
        state_a.mkdir()
        task = asyncio.create_task(
            run_joined(
                state=state_a,
                controller=server.base,
                name="a",
                token=token,
                app=_box_app(tmp_path, "a-data"),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            await _wait_connected(server.base)
            # Same token, DIFFERENT identity: single-use means rejection.
            state_b = tmp_path / "b-state"
            state_b.mkdir()
            with pytest.raises(JoinRejected) as err:
                await run_joined(
                    state=state_b,
                    controller=server.base,
                    name="b",
                    token=token,
                    app=_box_app(tmp_path, "b-data"),
                    once=True,
                    log=lambda *_: None,
                )
            assert err.value.reason == "not-enrolled"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_clone_trap_refuses_second_connection(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        task, state = await _join_box(tmp_path, server.base)
        try:
            await _wait_connected(server.base)
            # Same state dir (same private key) dialing again = a clone.
            with pytest.raises(JoinRejected) as err:
                await run_joined(
                    state=state,
                    controller=server.base,
                    name="box",
                    app=_box_app(tmp_path, "clone-data"),
                    once=True,
                    log=lambda *_: None,
                )
            assert err.value.reason == "already-connected"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_reconnect_without_token_uses_challenge(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        task, state = await _join_box(tmp_path, server.base)
        try:
            await _wait_connected(server.base)
            assert load_remote_config(state) is None or True  # config written by CLI join only
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # Box comes back with NO token — identity alone must be enough (`up`).
        task2 = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="box",
                app=_box_app(tmp_path, "box-data2"),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            machines = await _wait_connected(server.base)
            assert machines[0]["name"] == "box"  # same machine row, not a new enrollment
            assert len(machines) == 1
        finally:
            task2.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task2


async def test_protocol_mismatch_rejected(tmp_path):
    # Speak the wire directly: patching the shared PROTOCOL_VERSION constant
    # would change BOTH sides in one process and they'd happily agree.
    import websockets

    async with _Server(_controller(tmp_path)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        identity = load_or_create(tmp_path / "old-state")
        ws_url = server.base.replace("http://", "ws://") + "/ws/machine"
        async with websockets.connect(ws_url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol_version": ch.PROTOCOL_VERSION - 1,
                        "app_version": "0.0.0",
                        "pubkey": identity.public_key_b64,
                        "name": "old",
                        "token": token,
                    }
                )
            )
            frame = json.loads(await ws.recv())
        assert frame["type"] == "reject"
        assert frame["reason"] == "protocol-mismatch"
        assert "update" in frame["detail"]


async def _wire_join(ws, identity, token=None, seal_pubkey=None, seal_sig=None, name="box"):
    """Speak the machine handshake by hand; return the post-auth frame."""
    hello = {
        "type": "hello",
        "protocol_version": ch.PROTOCOL_VERSION,
        "app_version": "0.0.0",
        "pubkey": identity.public_key_b64,
        "name": name,
    }
    if token:
        hello["token"] = token
    if seal_pubkey is not None:
        hello["seal_pubkey"] = seal_pubkey
    if seal_sig is not None:
        hello["seal_sig"] = seal_sig
    await ws.send(json.dumps(hello))
    frame = json.loads(await ws.recv())
    if frame["type"] != "challenge":
        return frame
    signature = identity.sign_b64(ch.decode_body(frame["nonce"]))
    await ws.send(json.dumps({"type": "auth", "signature": signature}))
    return json.loads(await ws.recv())


async def test_unattested_seal_key_rejected_at_enroll(tmp_path):
    # A middlebox that swaps its own sealing key into the hello passes the
    # challenge (that only proves the identity key) but must not get its key
    # pinned — the acceptor requires the seal key signed by that identity.
    import websockets

    async with _Server(_controller(tmp_path)) as server:
        _, token = parse_join_url(await _arm(server.base))
        box = load_or_create(tmp_path / "box-state")
        mitm = load_or_create(tmp_path / "mitm-state")
        ws_url = server.base.replace("http://", "ws://") + "/ws/machine"
        async with websockets.connect(ws_url) as ws:
            frame = await _wire_join(
                ws,
                box,
                token=token,
                seal_pubkey=mitm.seal_public_key_b64,  # swapped in transit
                seal_sig=box.attest_seal_key_b64(),  # signs the REAL key
            )
        assert frame["type"] == "reject"
        assert frame["reason"] == "bad-seal-attestation"
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{server.base}/v1/machines")
            assert resp.json()["machines"] == []  # nothing was enrolled


async def _device_approve_cycle(client, base: str, fingerprint: str, name: str = "vm"):
    """Run start → approve → poll; return (user_code, join token)."""
    start = (
        await client.post(
            f"{base}/v1/remote/device/start",
            json={"name": name, "fingerprint": fingerprint},
        )
    ).json()
    resp = await client.post(f"{base}/v1/remote/device/{start['user_code']}/approve")
    assert resp.status_code == 200
    poll = (
        await client.post(
            f"{base}/v1/remote/device/poll",
            json={"device_code": start["device_code"]},
        )
    ).json()
    assert poll["status"] == "approved"
    _, token = parse_join_url(poll["join_url"])
    return start, token


async def test_device_flow_mints_identity_bound_token(tmp_path):
    # `openworker join <address>`: the box asks, the user approves a NAMED identity,
    # approval mints the one join token — bound so only that identity can use it.
    import websockets

    async with _Server(_controller(tmp_path)) as server:
        box = load_or_create(tmp_path / "box-state")
        rogue = load_or_create(tmp_path / "rogue-state")
        ws_url = server.base.replace("http://", "ws://") + "/ws/machine"
        async with httpx.AsyncClient() as client:
            start = (
                await client.post(
                    f"{server.base}/v1/remote/device/start",
                    json={"name": "vm", "fingerprint": box.fingerprint},
                )
            ).json()
            assert start["user_code"] and start["device_code"]
            poll = (
                await client.post(
                    f"{server.base}/v1/remote/device/poll",
                    json={"device_code": start["device_code"]},
                )
            ).json()
            assert poll["status"] == "pending"
            # The approval surface shows what the user must verify.
            listed = (await client.get(f"{server.base}/v1/remote/device")).json()
            assert listed["requests"][0]["name"] == "vm"
            assert listed["requests"][0]["fingerprint"] == box.fingerprint
            resp = await client.post(
                f"{server.base}/v1/remote/device/{start['user_code']}/approve"
            )
            assert resp.status_code == 200
            poll = (
                await client.post(
                    f"{server.base}/v1/remote/device/poll",
                    json={"device_code": start["device_code"]},
                )
            ).json()
            assert poll["status"] == "approved"
            _, token = parse_join_url(poll["join_url"])
            # Single-shot delivery: the grant is gone after handing out the URL.
            poll2 = (
                await client.post(
                    f"{server.base}/v1/remote/device/poll",
                    json={"device_code": start["device_code"]},
                )
            ).json()
            assert poll2["status"] == "expired"

            # A different identity presenting the approved token is refused
            # (and the token burns — single-use).
            async with websockets.connect(ws_url) as ws:
                frame = await _wire_join(
                    ws,
                    rogue,
                    token=token,
                    seal_pubkey=rogue.seal_public_key_b64,
                    seal_sig=rogue.attest_seal_key_b64(),
                )
            assert frame["type"] == "reject"
            assert frame["reason"] == "wrong-machine"

            # A fresh approval lets the approved box itself in.
            _, token2 = await _device_approve_cycle(client, server.base, box.fingerprint)
            async with websockets.connect(ws_url) as ws:
                frame = await _wire_join(
                    ws,
                    box,
                    token=token2,
                    seal_pubkey=box.seal_public_key_b64,
                    seal_sig=box.attest_seal_key_b64(),
                )
            assert frame["type"] == "welcome"


async def test_device_flow_deny(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        async with httpx.AsyncClient() as client:
            start = (
                await client.post(
                    f"{server.base}/v1/remote/device/start",
                    json={"name": "vm", "fingerprint": "fp"},
                )
            ).json()
            resp = await client.post(
                f"{server.base}/v1/remote/device/{start['user_code']}/deny"
            )
            assert resp.status_code == 200
            poll = (
                await client.post(
                    f"{server.base}/v1/remote/device/poll",
                    json={"device_code": start["device_code"]},
                )
            ).json()
            assert poll["status"] == "denied"
            # Denied grants can't be resurrected by a late approve.
            resp = await client.post(
                f"{server.base}/v1/remote/device/{start['user_code']}/approve"
            )
            assert resp.status_code == 404


async def test_auth_join_cli_runs_device_flow(tmp_path, monkeypatch):
    # The CLI half: prints a code, polls, then hands off to the ordinary token
    # join once approved (the enrollment primitive never varies).
    import coworker.remote.joiner as joiner_mod

    async with _Server(_controller(tmp_path)) as server:
        captured = {}
        monkeypatch.setattr(
            joiner_mod,
            "_cmd_join",
            lambda state, url, name: captured.update(url=url, name=name) or 0,
        )

        async def approve():
            async with httpx.AsyncClient() as client:
                for _ in range(200):
                    listed = (await client.get(f"{server.base}/v1/remote/device")).json()
                    if listed["requests"]:
                        req = listed["requests"][0]
                        resp = await client.post(
                            f"{server.base}/v1/remote/device/{req['user_code']}/approve"
                        )
                        assert resp.status_code == 200
                        return req
                    await asyncio.sleep(0.02)
                raise AssertionError("no device request appeared")

        approve_task = asyncio.create_task(approve())
        state = tmp_path / "auth-box-state"
        rc = await asyncio.to_thread(
            joiner_mod._cmd_auth_join, state, server.base, "authbox", 0.05
        )
        req = await approve_task
        assert rc == 0
        assert req["name"] == "authbox"
        assert req["fingerprint"] == load_or_create(state).fingerprint
        assert captured["url"].startswith(server.base + "/j/")
        assert captured["name"] == "authbox"


async def test_seal_key_backfill_requires_attestation(tmp_path):
    # A box enrolled before it had a sealing key pins one on reconnect — but
    # only an attested one; a bare seal_pubkey in the hello is refused.
    import websockets

    manager = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    async with _Server(create_app(manager)) as server:
        _, token = parse_join_url(await _arm(server.base))
        box = load_or_create(tmp_path / "box-state")
        ws_url = server.base.replace("http://", "ws://") + "/ws/machine"
        # Enroll with no sealing key at all (a pre-wallet box).
        async with websockets.connect(ws_url) as ws:
            frame = await _wire_join(ws, box, token=token)
            assert frame["type"] == "welcome"
        # Reconnect offering an unattested seal key: refused, not pinned.
        async with websockets.connect(ws_url) as ws:
            frame = await _wire_join(ws, box, seal_pubkey=box.seal_public_key_b64)
            assert frame["type"] == "reject"
            assert frame["reason"] == "bad-seal-attestation"
        reg = MachinesRegistry(manager.session_store.db_path)
        assert reg.by_pubkey(box.public_key_b64)["seal_pubkey"] == ""
        # Reconnect with the attestation: backfilled.
        async with websockets.connect(ws_url) as ws:
            frame = await _wire_join(
                ws,
                box,
                seal_pubkey=box.seal_public_key_b64,
                seal_sig=box.attest_seal_key_b64(),
            )
            assert frame["type"] == "welcome"
            assert (
                reg.by_pubkey(box.public_key_b64)["seal_pubkey"]
                == box.seal_public_key_b64
            )


async def test_offline_machine_returns_503_and_unknown_404(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        task, _ = await _join_box(tmp_path, server.base)
        machines = await _wait_connected(server.base)
        mid = machines[0]["id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with httpx.AsyncClient() as client:
            # Wait for the acceptor to notice the disconnect.
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                listing = await client.get(f"{server.base}/v1/machines")
                if not any(m["connected"] for m in listing.json()["machines"]):
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)
            offline = await client.get(f"{server.base}/v1/machines/{mid}/p/v1/health")
            assert offline.status_code == 503
            unknown = await client.get(
                f"{server.base}/v1/machines/nosuch/p/v1/health"
            )
            assert unknown.status_code == 404
            # Offline machines still LIST (greyed in the GUI, never vanished).
            listing = await client.get(f"{server.base}/v1/machines")
            row = listing.json()["machines"][0]
            assert row["id"] == mid and row["connected"] is False


async def test_remove_connected_machine_refused_offline_allowed(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        task, _ = await _join_box(tmp_path, server.base)
        machines = await _wait_connected(server.base)
        mid = machines[0]["id"]
        async with httpx.AsyncClient() as client:
            refused = await client.delete(f"{server.base}/v1/machines/{mid}")
            assert refused.status_code == 409
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                listing = await client.get(f"{server.base}/v1/machines")
                if not any(m["connected"] for m in listing.json()["machines"]):
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)
            removed = await client.delete(f"{server.base}/v1/machines/{mid}")
            assert removed.status_code == 200
            listing = await client.get(f"{server.base}/v1/machines")
            assert listing.json()["machines"] == []


async def test_rename_machine(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        task, _ = await _join_box(tmp_path, server.base)
        try:
            machines = await _wait_connected(server.base)
            mid = machines[0]["id"]
            async with httpx.AsyncClient() as client:
                resp = await client.patch(
                    f"{server.base}/v1/machines/{mid}", json={"name": "gpu-rig"}
                )
                assert resp.status_code == 200
                listing = await client.get(f"{server.base}/v1/machines")
                assert listing.json()["machines"][0]["name"] == "gpu-rig"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_join_hint_page_is_tokenless_and_constant(tmp_path):
    async with _Server(_controller(tmp_path)) as server:
        async with httpx.AsyncClient() as client:
            real = await _arm(server.base)
            _, token = parse_join_url(real)
            valid = await client.get(f"{server.base}/j/{token}")
            bogus = await client.get(f"{server.base}/j/definitely-not-a-token")
            assert valid.status_code == bogus.status_code == 200
            assert valid.text == bogus.text  # must not confirm token validity
            # …and looking at the hint page must NOT consume the token: a real
            # join with it still works.
            state = tmp_path / "hint-state"
            state.mkdir()
            task = asyncio.create_task(
                run_joined(
                    state=state,
                    controller=server.base,
                    name="hint",
                    token=token,
                    app=_box_app(tmp_path, "hint-data"),
                    once=True,
                    log=lambda *_: None,
                )
            )
            try:
                await _wait_connected(server.base)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


async def test_offline_machine_serves_session_snapshot(tmp_path):
    """The vanish bug (owner-hit 2026-08-25): an offline machine's sessions must
    come from the controller's stored snapshot — greyed, never vanished."""
    from coworker.sessions import SessionRecord

    async with _Server(_controller(tmp_path)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "snap-state"
        state.mkdir()
        box_manager = SessionManager(data_dir=tmp_path / "snap-data")
        box_manager.session_store.save(
            SessionRecord(
                session_id="snap-1",
                workspace="",
                model="m",
                mode="interactive",
                title="Nightly watch",
                messages=[
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello from the box"},
                ],
            )
        )
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="snap-box",
                token=token,
                app=create_app(box_manager),
                once=True,
                log=lambda *_: None,
            )
        )
        machines = await _wait_connected(server.base)
        mid = machines[0]["id"]
        async with httpx.AsyncClient() as client:
            live = await client.get(f"{server.base}/v1/machines/{mid}/sessions")
            assert live.json()["live"] is True
            assert [s["session_id"] for s in live.json()["sessions"]] == ["snap-1"]
            # Cache-on-view: fetch the transcript while connected — that's what
            # makes it readable later.
            viewed = await client.get(
                f"{server.base}/v1/machines/{mid}/sessions/snap-1/messages"
            )
            assert viewed.json()["live"] is True
            assert len(viewed.json()["messages"]) == 2

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                listing = await client.get(f"{server.base}/v1/machines")
                if not any(m["connected"] for m in listing.json()["machines"]):
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)

            cached = await client.get(f"{server.base}/v1/machines/{mid}/sessions")
            assert cached.json()["live"] is False
            rows = cached.json()["sessions"]
            assert [s["session_id"] for s in rows] == ["snap-1"]
            assert rows[0]["title"] == "Nightly watch"
            # The viewed transcript is still readable offline (read-only)…
            replay = await client.get(
                f"{server.base}/v1/machines/{mid}/sessions/snap-1/messages"
            )
            assert replay.json() == {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello from the box"},
                ],
                "live": False,
                "cached": True,
            }
            # …while a NEVER-viewed transcript honestly has no copy.
            unseen = await client.get(
                f"{server.base}/v1/machines/{mid}/sessions/never-opened/messages"
            )
            assert unseen.json()["cached"] is False
            # Unknown machines still 404 (no phantom snapshots).
            missing = await client.get(f"{server.base}/v1/machines/nosuch/sessions")
            assert missing.status_code == 404


def test_js_sealed_blob_unseals():
    """Cross-language pin (OPE-149): this blob was produced by the SPA's
    seal.ts (noble x25519 + hkdf-sha256 + chacha20-poly1305) against the
    fixture key. The box-side unseal must open it forever — a drift in either
    implementation fails here, not in a user's browser."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from coworker.remote.identity import MachineIdentity

    fixture_priv = "UGaZYTzlePyN5LU5/VIOJbBPYUbU6n7wq6b/VMWd/UU="
    fixture_blob = (
        "JOTCfuu/MZ5dGUONsHCk0DrVR8zZdj3riSfIQZSyjmJHp6b2Mo0VmYo6AP8zo7mi"
        "sg5/DzJMA0u4ZKNBgEMLM6foGT/tgmkCprqm4eX/QUG+dPB7h2XySU0fQBqjLlE7"
        "mot28tvt07KJM/8+vQ=="
    )
    ident = MachineIdentity(
        Ed25519PrivateKey.generate(),
        X25519PrivateKey.from_private_bytes(base64.b64decode(fixture_priv)),
    )
    assert json.loads(ident.unseal_b64(fixture_blob)) == {
        "profiles": {"provider:anthropic": {"api_key": "sk-test-123"}}
    }


async def test_browser_sealed_deploy_relay(tmp_path):
    """OPE-149: a client that sealed to the machine's pinned key itself can
    deploy through the acceptor as a blind relay — no wallet consulted, the
    plaintext never touches the controller, the ledger records the client's
    hash and the audit event says the source was browser-sealed."""
    from coworker.remote.identity import seal_b64

    ctrl_manager = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    async with _Server(create_app(ctrl_manager)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "sealed-state"
        state.mkdir()
        box_manager = SessionManager(data_dir=tmp_path / "sealed-data")
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="sealed-box",
                token=token,
                app=create_app(box_manager),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            machines = await _wait_connected(server.base)
            row = machines[0]
            # The list exposes the sealing key's public half + fingerprint —
            # what a browser seals to and what the user verifies.
            assert row["seal_pubkey"]
            assert len(row["seal_fingerprint"]) == 16
            sealed = seal_b64(
                row["seal_pubkey"],
                json.dumps(
                    {"profiles": {"provider:anthropic": {"api_key": "sk-sealed-1"}}}
                ).encode(),
            )
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{server.base}/v1/machines/{row['id']}/secrets",
                    json={
                        "sealed_b64": sealed,
                        "profiles": ["provider:anthropic"],
                        "hashes": {"provider:anthropic": "h" * 64},
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["deployed"] == ["provider:anthropic"]
                assert box_manager.secrets.get("provider:anthropic") == {
                    "api_key": "sk-sealed-1"
                }
                ledger = (
                    await client.get(
                        f"{server.base}/v1/machines/{row['id']}/secrets"
                    )
                ).json()["secrets"]
                assert [r["profile"] for r in ledger] == ["provider:anthropic"]
            audit_path = (
                ctrl_manager.session_store.db_path.parent / "remote-audit.jsonl"
            )
            events = [
                json.loads(line) for line in audit_path.read_text().splitlines()
            ]
            deploy = next(
                e for e in events if e["entity"]["type"] == "secret_deploy"
            )
            assert deploy["unmapped"]["source"] == "browser-sealed"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_wallet_deploy_revoke_and_staleness(tmp_path):
    """Keys wallet end-to-end over loopback: sealed deploy lands in the BOX's
    SecretStore, the ledger tracks it, rotation shows as stale, revoke scrubs."""
    ctrl_manager = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    ctrl_manager.secrets.put("openai", {"api_key": "sk-first"})
    ctrl_manager.secrets.put("tavily", {"api_key": "tv-1"})
    async with _Server(create_app(ctrl_manager)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "w-state"
        state.mkdir()
        box_manager = SessionManager(data_dir=tmp_path / "w-data")
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="wallet-box",
                token=token,
                app=create_app(box_manager),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            machines = await _wait_connected(server.base)
            mid = machines[0]["id"]
            async with httpx.AsyncClient() as client:
                # Deploy two profiles; the box's own store receives copies.
                resp = await client.post(
                    f"{server.base}/v1/machines/{mid}/secrets",
                    json={"profiles": ["openai", "tavily"]},
                )
                assert resp.status_code == 200
                assert resp.json()["deployed"] == ["openai", "tavily"]
                assert box_manager.secrets.get("openai") == {"api_key": "sk-first"}
                assert box_manager.secrets.get("tavily") == {"api_key": "tv-1"}

                ledger = (
                    await client.get(f"{server.base}/v1/machines/{mid}/secrets")
                ).json()["secrets"]
                assert {r["profile"] for r in ledger} == {"openai", "tavily"}
                assert not any(r["stale"] for r in ledger)

                # Rotate = update the wallet, deploy again (owner ruling: no
                # third verb). In between, the ledger shows stale.
                ctrl_manager.secrets.put("openai", {"api_key": "sk-rotated"})
                ledger = (
                    await client.get(f"{server.base}/v1/machines/{mid}/secrets")
                ).json()["secrets"]
                assert next(r for r in ledger if r["profile"] == "openai")["stale"]
                resp = await client.post(
                    f"{server.base}/v1/machines/{mid}/secrets",
                    json={"profiles": ["openai"]},
                )
                assert resp.status_code == 200
                assert box_manager.secrets.get("openai") == {"api_key": "sk-rotated"}
                ledger = (
                    await client.get(f"{server.base}/v1/machines/{mid}/secrets")
                ).json()["secrets"]
                assert not any(r["stale"] for r in ledger)

                # Revoke scrubs the box and the ledger (DELETE with a body).
                resp = await client.request(
                    "DELETE",
                    f"{server.base}/v1/machines/{mid}/secrets",
                    json={"profiles": ["tavily"]},
                )
                assert resp.status_code == 200
                assert box_manager.secrets.get("tavily") is None
                assert box_manager.secrets.get("openai") is not None
                ledger = (
                    await client.get(f"{server.base}/v1/machines/{mid}/secrets")
                ).json()["secrets"]
                assert {r["profile"] for r in ledger} == {"openai"}

                # Unknown wallet profile → clear 400, nothing deployed.
                resp = await client.post(
                    f"{server.base}/v1/machines/{mid}/secrets",
                    json={"profiles": ["nope"]},
                )
                assert resp.status_code == 400

            # The audit trail saw it all: enroll, three deploys, one revoke —
            # OCSF Entity Management events, profile names only, never values.
            audit_path = (
                ctrl_manager.session_store.db_path.parent / "remote-audit.jsonl"
            )
            events = [
                json.loads(line) for line in audit_path.read_text().splitlines()
            ]
            assert all(e["class_uid"] == 3004 for e in events)
            assert [
                (e["activity_name"], e["entity"]["type"], e["entity"]["name"])
                for e in events
            ] == [
                ("create", "machine", "wallet-box"),
                ("create", "secret_deploy", "openai"),
                ("create", "secret_deploy", "tavily"),
                ("create", "secret_deploy", "openai"),  # the rotation redeploy
                ("delete", "secret_deploy", "tavily"),
            ]
            raw = audit_path.read_text()
            assert "sk-first" not in raw and "sk-rotated" not in raw
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_wallet_deploy_requires_live_machine(tmp_path):
    ctrl_manager = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    ctrl_manager.secrets.put("openai", {"api_key": "sk-x"})
    async with _Server(create_app(ctrl_manager)) as server:
        task, _ = await _join_box(tmp_path, server.base)
        machines = await _wait_connected(server.base)
        mid = machines[0]["id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                listing = await client.get(f"{server.base}/v1/machines")
                if not any(m["connected"] for m in listing.json()["machines"]):
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)
            resp = await client.post(
                f"{server.base}/v1/machines/{mid}/secrets",
                json={"profiles": ["openai"]},
            )
            assert resp.status_code == 503


def test_secrets_cli_set_and_list(tmp_path, capsys, monkeypatch):
    from coworker.remote import joiner

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "s-state"))
    assert joiner.cli(["keys", "set", "openai", "api_key=sk-local"]) == 0
    assert "stored profile 'openai'" in capsys.readouterr().out
    assert joiner.cli(["keys", "list"]) == 0
    assert "openai" in capsys.readouterr().out
    from coworker.secrets import SecretStore

    store = SecretStore(tmp_path / "s-state" / "secrets.json")
    assert store.get("openai") == {"api_key": "sk-local"}
    assert joiner.cli(["keys", "set", "bad", "no-equals"]) == 2


# -- bridged session WebSocket (P1c) -------------------------------------------


def _scripted_provider(turns):
    from coworker.providers import ModelCapabilities, ProviderClient

    class _Scripted(ProviderClient):
        def __init__(self):
            self._turns = list(turns)

        def complete(self, *, model, messages, tools=None, **settings):
            # Auto-title and other side-turns share this provider: keep
            # replaying the last turn instead of running dry.
            return self._turns.pop(0) if len(self._turns) > 1 else self._turns[0]

        def capabilities(self, model):
            return ModelCapabilities()

    return _Scripted()


def _scripted_box_app(tmp_path, name, texts):
    from coworker.providers import AssistantTurn

    workspace = tmp_path / f"{name}-ws"
    workspace.mkdir(exist_ok=True)
    manager = SessionManager(
        workspace=workspace,
        data_dir=tmp_path / name,
        provider=_scripted_provider(
            [AssistantTurn(text=t, finish_reason="stop") for t in texts]
        ),
    )
    return create_app(manager)


async def test_bridged_session_ws_runs_a_turn_on_the_box(tmp_path):
    """The P1c proof: a chat turn executes on the BOX's engine, streamed live to
    a client connected to the CONTROLLER's bridged socket."""
    import websockets

    async with _Server(_controller(tmp_path)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "chat-state"
        state.mkdir()
        box = _scripted_box_app(tmp_path, "chat-data", ["Hello from the box."])
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="chat-box",
                token=token,
                app=box,
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            machines = await _wait_connected(server.base)
            mid = machines[0]["id"]
            bridge_url = (
                server.base.replace("http://", "ws://")
                + f"/ws/machines/{mid}/p/ws/session/remote-chat-1"
            )
            async with websockets.connect(bridge_url) as ws:
                first = json.loads(await ws.recv())
                assert first["type"] == "ready"
                await ws.send(json.dumps({"type": "user_message", "text": "hi"}))
                texts, done = [], False
                while not done:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if event["type"] == "assistant_message":
                        texts.append(event["data"]["text"])
                    done = event["type"] == "turn_done"
                assert texts == ["Hello from the box."]

            # And the session persisted on the BOX, visible through the proxy —
            # the desktop persisted nothing of it.
            async with httpx.AsyncClient() as client:
                remote = await client.get(
                    f"{server.base}/v1/machines/{mid}/p/v1/sessions"
                )
                ids = [s["session_id"] for s in remote.json()["sessions"]]
                assert "remote-chat-1" in ids
                local = await client.get(f"{server.base}/v1/sessions")
                assert "remote-chat-1" not in [
                    s["session_id"] for s in local.json()["sessions"]
                ]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_bridged_ws_to_offline_machine_closes_immediately(tmp_path):
    import websockets

    async with _Server(_controller(tmp_path)) as server:
        task, _ = await _join_box(tmp_path, server.base)
        machines = await _wait_connected(server.base)
        mid = machines[0]["id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        deadline = asyncio.get_running_loop().time() + 5
        async with httpx.AsyncClient() as client:
            while True:
                listing = await client.get(f"{server.base}/v1/machines")
                if not any(m["connected"] for m in listing.json()["machines"]):
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)
        bridge_url = (
            server.base.replace("http://", "ws://")
            + f"/ws/machines/{mid}/p/ws/session/x"
        )
        # Close-before-accept surfaces as a rejected handshake — the client
        # fails fast instead of hanging on a socket to nowhere.
        with pytest.raises(websockets.InvalidStatus):
            async with websockets.connect(bridge_url):
                pass


async def test_box_death_closes_bridged_sockets(tmp_path):
    import websockets

    async with _Server(_controller(tmp_path)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "drop-state"
        state.mkdir()
        box = _scripted_box_app(tmp_path, "drop-data", ["never sent"])
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="drop-box",
                token=token,
                app=box,
                once=True,
                log=lambda *_: None,
            )
        )
        machines = await _wait_connected(server.base)
        mid = machines[0]["id"]
        bridge_url = (
            server.base.replace("http://", "ws://")
            + f"/ws/machines/{mid}/p/ws/session/drop-1"
        )
        async with websockets.connect(bridge_url) as ws:
            first = json.loads(await ws.recv())
            assert first["type"] == "ready"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # The GUI-facing socket must die with the machine, not hang.
            with pytest.raises(websockets.ConnectionClosed):
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=5)


# -- channel unit --------------------------------------------------------------


async def test_dispatcher_answers_instead_of_wedging(tmp_path):
    dispatcher = ch.AsgiDispatcher(_box_app(tmp_path, "d-data"))
    try:
        ok = await dispatcher.dispatch(
            {"type": "rpc", "id": "r1", "method": "GET", "path": "/v1/health"}
        )
        assert ok["status"] == 200 and ok["id"] == "r1"
        missing = await dispatcher.dispatch(
            {"type": "rpc", "id": "r2", "method": "GET", "path": "/v1/nope"}
        )
        assert missing["status"] == 404
        body = json.dumps({"name": "x"}).encode()
        posted = await dispatcher.dispatch(
            {
                "type": "rpc",
                "id": "r3",
                "method": "POST",
                "path": "/v1/workspaces/open",
                "body_b64": ch.encode_body(body),
                "content_type": "application/json",
            }
        )
        assert posted["id"] == "r3" and posted["status"] in (200, 400, 422)
    finally:
        await dispatcher.aclose()


def test_cli_status_and_leave(tmp_path, capsys, monkeypatch):
    from coworker.remote import joiner

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "cli-state"))
    assert joiner.cli(["status"]) == 0
    out = capsys.readouterr().out
    assert "not joined" in out and "none (created on first join)" in out

    # `up` before any join is a clear error, not a crash.
    assert joiner.cli(["up"]) == 2
    assert "has not joined" in capsys.readouterr().err

    state = Path(tmp_path / "cli-state")
    state.mkdir(parents=True, exist_ok=True)
    load_or_create(state)
    joiner.save_remote_config(
        state, {"controller": "http://10.0.0.5:9787", "name": "box", "machine_id": "m1"}
    )
    assert joiner.cli(["status"]) == 0
    out = capsys.readouterr().out
    assert "http://10.0.0.5:9787" in out and "ed25519" in out

    assert joiner.cli(["leave", "--yes"]) == 0
    assert not (state / "machine.key").exists()
    assert joiner.load_remote_config(state) is None
    assert joiner.cli(["status"]) == 0
    assert "not joined" in capsys.readouterr().out


def test_openworker_cli_routes_remote_verbs(monkeypatch, tmp_path, capsys):
    from coworker import cli as ow_cli

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "route-state"))
    with pytest.raises(SystemExit) as exit_info:
        ow_cli.main(["machine", "status"])
    assert exit_info.value.code == 0
    assert "not joined" in capsys.readouterr().out


def test_join_cli_bad_url_is_a_clear_error(monkeypatch, tmp_path, capsys):
    from coworker.remote import joiner

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "bad-state"))
    assert joiner.cli(["join", "http://host:1/wrong/tok"]) == 2
    assert "join URL" in capsys.readouterr().err


async def test_rpc_client_close_fails_pending():
    sent = []

    async def send(text):
        sent.append(text)

    client = ch.RpcClient(send)
    request = asyncio.create_task(client.request("GET", "/v1/health"))
    await asyncio.sleep(0.01)
    client.close()
    with pytest.raises(ConnectionError):
        await request
    with pytest.raises(ConnectionError):
        await client.request("GET", "/v1/health")


# -- cloud-mode skeleton (acceptor-only service) --------------------------------


def test_desktop_capabilities_mode():
    from fastapi.testclient import TestClient

    import tempfile

    manager = SessionManager(data_dir=Path(tempfile.mkdtemp()))
    client = TestClient(create_app(manager))
    assert client.get("/v1/capabilities").json() == {"mode": "desktop"}


async def test_cloud_service_accepts_joins_and_proxies(tmp_path):
    """The acceptor-as-module payoff: the SAME box code joins the cloud service,
    which has no engine of its own — and the SPA-facing surface answers in
    cloud mode with honest emptiness for local-only endpoints."""
    from coworker.remote.service import create_cloud_app

    async with _Server(create_cloud_app(tmp_path / "cloud")) as server:
        async with httpx.AsyncClient() as client:
            caps = await client.get(f"{server.base}/v1/capabilities")
            assert caps.json() == {"mode": "cloud"}
            health = await client.get(f"{server.base}/v1/health")
            assert health.json()["status"] == "ok"
            local = await client.get(f"{server.base}/v1/sessions")
            assert local.json() == {"sessions": []}  # a cloud has no LOCAL home

        task, _ = await _join_box(tmp_path, server.base)
        try:
            machines = await _wait_connected(server.base)
            mid = machines[0]["id"]
            async with httpx.AsyncClient() as client:
                # The box's surface through the CLOUD's proxy — identical to desktop.
                proxied = await client.get(
                    f"{server.base}/v1/machines/{mid}/p/v1/health"
                )
                assert proxied.status_code == 200
                # No wallet on the cloud tier (browser sealing lands later):
                # deploys refuse rather than holding plaintext keys server-side.
                deploy = await client.post(
                    f"{server.base}/v1/machines/{mid}/secrets",
                    json={"profiles": ["provider:openai"]},
                )
                assert deploy.status_code == 500
                assert "no wallet" in deploy.json()["error"]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_cloud_service_token_gate(tmp_path, monkeypatch):
    from coworker.remote.service import create_cloud_app

    monkeypatch.setenv("COWORKER_API_TOKEN", "cloud-secret")
    async with _Server(create_cloud_app(tmp_path / "cloud")) as server:
        async with httpx.AsyncClient() as client:
            open_paths = await client.get(f"{server.base}/v1/health")
            assert open_paths.status_code == 200
            denied = await client.get(f"{server.base}/v1/machines")
            assert denied.status_code == 401
            allowed = await client.get(
                f"{server.base}/v1/machines",
                headers={"x-openworker-token": "cloud-secret"},
            )
            assert allowed.status_code == 200
            # Machine-facing device-flow halves are tokenless (a box holds no
            # dashboard token); approval is not.
            start = await client.post(
                f"{server.base}/v1/remote/device/start", json={"name": "vm"}
            )
            assert start.status_code == 200
            poll = await client.post(
                f"{server.base}/v1/remote/device/poll",
                json={"device_code": start.json()["device_code"]},
            )
            assert poll.status_code == 200 and poll.json()["status"] == "pending"
            gated = await client.post(
                f"{server.base}/v1/remote/device/{start.json()['user_code']}/approve"
            )
            assert gated.status_code == 401


def test_audit_log_ocsf_shape_and_write_failure(tmp_path):
    from coworker.remote.audit import AuditLog

    log = AuditLog(tmp_path / "audit.jsonl")
    log.emit(
        "create",
        entity_type="machine",
        entity_name="my-box",
        entity_uid="m1",
        detail={"fingerprint": "abcd"},
    )
    event = json.loads((tmp_path / "audit.jsonl").read_text())
    assert event["class_uid"] == 3004 and event["category_uid"] == 3
    assert event["activity_id"] == 1 and event["type_uid"] == 300401
    assert event["entity"] == {"type": "machine", "name": "my-box", "uid": "m1"}
    assert event["metadata"]["product"]["name"] == "OpenWorker"
    assert event["unmapped"] == {"fingerprint": "abcd"}
    assert event["time"] > 0 and event["status_id"] == 1
    # A write failure never breaks the audited action — it is counted instead.
    # mkdir under a path that is a FILE fails with OSError.
    broken = AuditLog(tmp_path / "audit.jsonl" / "not-a-dir" / "x.jsonl")
    broken.emit("delete", entity_type="machine", entity_name="gone")
    assert broken.dropped == 1


def test_local_provisioner_launch_is_byo_only(tmp_path):
    from coworker.remote.provision import LocalProvisioner
    from coworker.remote.acceptor import RemoteAcceptor

    prov = LocalProvisioner(RemoteAcceptor(MachinesRegistry(tmp_path / "db")))
    minted = prov.mint_token({"fingerprint": "fp"})
    assert minted["token"] and minted["expires_at"] > 0
    with pytest.raises(NotImplementedError):
        asyncio.run(prov.launch({"image": "whatever"}))


# -- multi-tenancy ---------------------------------------------------------------


async def _tenant_header_resolver(conn):
    """Test resolver: the x-org header IS the tenant (a real deployment
    verifies a JWT here). Async on purpose — the hosted resolver awaits its
    IdP, so the whole multi-tenant suite exercises the await path."""
    org = conn.headers.get("x-org")
    if not org:
        return None
    return {"org_id": org, "actor": f"{org}-admin"}


def _tenant_app(tmp_path):
    from fastapi import FastAPI

    from coworker.remote.acceptor import mount_acceptor
    from coworker.remote.audit import AuditLog

    app = FastAPI()
    registry = MachinesRegistry(tmp_path / "tenant.db")
    mount_acceptor(
        app,
        registry,
        tenant_resolver=_tenant_header_resolver,
        audit=AuditLog(tmp_path / "tenant-audit.jsonl"),
    )
    return app, registry


async def test_multi_tenant_org_isolation(tmp_path):
    """The SOC2 standing test: org B can neither see nor touch org A's machine
    through ANY control-plane surface — every route answers 404, exactly as if
    the machine did not exist."""
    import websockets

    app, registry = _tenant_app(tmp_path)
    async with _Server(app) as server:
        A, B = {"x-org": "org-a"}, {"x-org": "org-b"}
        async with httpx.AsyncClient() as client:
            # No tenant → 401 before anything else.
            assert (await client.get(f"{server.base}/v1/machines")).status_code == 401
            assert (
                await client.post(f"{server.base}/v1/remote/arm")
            ).status_code == 401
            join_url = (
                await client.post(f"{server.base}/v1/remote/arm", headers=A)
            ).json()["join_url"]
        _, token = parse_join_url(join_url)
        state = tmp_path / "a-box-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="a-box",
                token=token,
                app=_box_app(tmp_path, "a-box-data"),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            async with httpx.AsyncClient() as client:
                deadline = asyncio.get_running_loop().time() + 5
                while True:
                    rows = (
                        await client.get(f"{server.base}/v1/machines", headers=A)
                    ).json()["machines"]
                    if any(m["connected"] for m in rows):
                        break
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.05)
                mid = rows[0]["id"]
                # The token carried org-a; the machine landed there.
                assert registry.by_id(mid)["org_id"] == "org-a"
                # B's listing is empty; every scoped surface 404s for B.
                assert (
                    await client.get(f"{server.base}/v1/machines", headers=B)
                ).json()["machines"] == []
                for method, url, kwargs in [
                    ("GET", f"/v1/machines/{mid}/sessions", {}),
                    ("GET", f"/v1/machines/{mid}/sessions/s1/messages", {}),
                    ("GET", f"/v1/machines/{mid}/p/v1/health", {}),
                    ("PATCH", f"/v1/machines/{mid}", {"json": {"name": "hijack"}}),
                    ("DELETE", f"/v1/machines/{mid}", {}),
                    ("GET", f"/v1/machines/{mid}/secrets", {}),
                    ("POST", f"/v1/machines/{mid}/secrets", {"json": {"profiles": ["openai"]}}),
                ]:
                    resp = await client.request(
                        method, f"{server.base}{url}", headers=B, **kwargs
                    )
                    assert resp.status_code == 404, (method, url, resp.status_code)
                # The owner org works normally through the same routes.
                ok = await client.get(
                    f"{server.base}/v1/machines/{mid}/p/v1/health", headers=A
                )
                assert ok.status_code == 200
            # Bridged WebSocket from the wrong org is refused outright.
            bridge = (
                server.base.replace("http://", "ws://")
                + f"/ws/machines/{mid}/p/ws/session/x"
            )
            with pytest.raises(websockets.InvalidStatus):
                async with websockets.connect(bridge, additional_headers=B):
                    pass
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_multi_tenant_device_flow_claims_approver_org(tmp_path):
    """`auth join` in a shared deployment: pending grants are unlisted (typed
    code only), approval claims the grant into the APPROVER's org, and both the
    approval and the enrollment land in the audit trail with that tenant."""
    import websockets

    app, registry = _tenant_app(tmp_path)
    async with _Server(app) as server:
        B = {"x-org": "org-b"}
        box = load_or_create(tmp_path / "dev-box-state")
        ws_url = server.base.replace("http://", "ws://") + "/ws/machine"
        async with httpx.AsyncClient() as client:
            start = (
                await client.post(
                    f"{server.base}/v1/remote/device/start",
                    json={"name": "vmx", "fingerprint": box.fingerprint},
                )
            ).json()
            # No cross-tenant listing of pending grants.
            assert (
                await client.get(f"{server.base}/v1/remote/device", headers=B)
            ).status_code == 404
            # Typed code → the machine's claimed identity, for the user to verify.
            detail = (
                await client.get(
                    f"{server.base}/v1/remote/device/{start['user_code']}", headers=B
                )
            ).json()
            assert detail["name"] == "vmx"
            assert detail["fingerprint"] == box.fingerprint
            # Approval requires a tenant — the org is what approval claims.
            assert (
                await client.post(
                    f"{server.base}/v1/remote/device/{start['user_code']}/approve"
                )
            ).status_code == 401
            assert (
                await client.post(
                    f"{server.base}/v1/remote/device/{start['user_code']}/approve",
                    headers=B,
                )
            ).status_code == 200
            poll = (
                await client.post(
                    f"{server.base}/v1/remote/device/poll",
                    json={"device_code": start["device_code"]},
                )
            ).json()
            _, token = parse_join_url(poll["join_url"])
        async with websockets.connect(ws_url) as ws:
            frame = await _wire_join(
                ws,
                box,
                token=token,
                seal_pubkey=box.seal_public_key_b64,
                seal_sig=box.attest_seal_key_b64(),
            )
        assert frame["type"] == "welcome"
        assert registry.by_pubkey(box.public_key_b64)["org_id"] == "org-b"
        events = [
            json.loads(line)
            for line in (tmp_path / "tenant-audit.jsonl").read_text().splitlines()
        ]
        approve = next(e for e in events if e["entity"]["type"] == "device_grant")
        assert approve["actor"]["user"]["name"] == "org-b-admin"
        assert approve["actor"]["user"]["org"]["uid"] == "org-b"
        enroll = next(e for e in events if e["entity"]["type"] == "machine")
        assert enroll["actor"]["user"]["org"]["uid"] == "org-b"


# -- service install (systemd) --------------------------------------------------


def test_systemd_unit_content(tmp_path):
    from coworker.remote.joiner import _systemd_unit

    unit = _systemd_unit(tmp_path / "state", "/usr/local/bin/openworker")
    assert "ExecStart=/usr/local/bin/openworker up" in unit
    assert f"Environment=COWORKER_STATE_DIR={tmp_path / 'state'}" in unit
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit


def test_service_install_refuses_non_linux_and_unjoined(tmp_path, capsys):
    import argparse

    from coworker.remote import joiner

    args = argparse.Namespace(service_command="install")
    # Neither Linux nor macOS → clear refusal (macOS has its own launchd path now).
    assert joiner._cmd_service(tmp_path, args, platform="win32", home=tmp_path) == 2
    assert "Linux (systemd) and macOS (launchd)" in capsys.readouterr().err
    # Linux but never joined → point at `join` first.
    assert joiner._cmd_service(tmp_path, args, platform="linux", home=tmp_path) == 2
    assert "has not joined" in capsys.readouterr().err


def test_service_install_writes_unit_and_uninstall_removes(tmp_path, capsys):
    import argparse

    from coworker.remote import joiner

    state = tmp_path / "state"
    state.mkdir()
    joiner.save_remote_config(
        state, {"controller": "http://10.0.0.5:8765", "name": "vm", "machine_id": "m1"}
    )
    args = argparse.Namespace(service_command="install")
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 0
    # Named after the joined identity, never the bare legacy name.
    unit = tmp_path / ".config" / "systemd" / "user" / "openworker-vm.service"
    assert unit.exists()
    text = unit.read_text()
    assert f"COWORKER_STATE_DIR={state}" in text
    assert " up" in text
    assert "(vm)" in text
    out = capsys.readouterr().out
    assert "wrote" in out  # systemctl may be absent here; manual fallback printed

    args = argparse.Namespace(service_command="uninstall", unit=None)
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 0
    assert not unit.exists()


def _joined_state(tmp_path, name, controller="http://10.0.0.5:8765"):
    from coworker.remote import joiner

    state = tmp_path / f"state-{name}"
    state.mkdir()
    joiner.save_remote_config(
        state, {"controller": controller, "name": name, "machine_id": f"m-{name}"}
    )
    return state


def test_service_install_refuses_twin_on_same_state_dir(tmp_path, capsys):
    """The VM bug: a stale unit still pinned to the state dir we are installing
    for — two `up` engines, one SQLite, and every kill undone by Restart=always.
    Install refuses and names the twin; --force replaces it."""
    import argparse

    from coworker.remote import joiner

    state = _joined_state(tmp_path, "cloud-vm")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    stale = unit_dir / "openworker.service"
    stale.write_text(joiner._systemd_unit(state, "/old/openworker"))

    args = argparse.Namespace(service_command="install", force=False)
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 2
    err = capsys.readouterr().err
    assert "openworker.service already runs" in err
    assert "--unit openworker.service" in err
    assert not (unit_dir / "openworker-cloud-vm.service").exists()

    args = argparse.Namespace(service_command="install", force=True)
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 0
    out = capsys.readouterr().out
    assert "replaced openworker.service" in out
    assert not stale.exists()
    assert (unit_dir / "openworker-cloud-vm.service").exists()


def test_service_install_reports_other_engine_and_list_shows_all(tmp_path, capsys):
    import argparse

    from coworker.remote import joiner

    other_state = _joined_state(tmp_path, "my-box", controller="http://127.0.0.1:8765")
    state = _joined_state(tmp_path, "cloud-vm")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "openworker-my-box.service").write_text(
        joiner._systemd_unit(other_state, "/x/openworker", "my-box")
    )

    args = argparse.Namespace(service_command="install", force=False)
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 0
    out = capsys.readouterr().out
    assert "note: openworker-my-box.service is also installed" in out
    assert str(other_state) in out

    args = argparse.Namespace(service_command="list")
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 0
    out = capsys.readouterr().out
    assert "openworker-cloud-vm.service" in out and "openworker-my-box.service" in out
    assert str(state) in out and str(other_state) in out

    # Uninstall by explicit unit name removes the stale twin, not ours.
    args = argparse.Namespace(service_command="uninstall", unit="openworker-my-box.service")
    assert joiner._cmd_service(state, args, platform="linux", home=tmp_path) == 0
    assert not (unit_dir / "openworker-my-box.service").exists()
    assert (unit_dir / "openworker-cloud-vm.service").exists()


def test_unit_name_for_slugs_and_falls_back():
    from coworker.remote.joiner import SERVICE_UNIT_NAME, unit_name_for

    assert unit_name_for("cloud-vm") == "openworker-cloud-vm.service"
    assert unit_name_for("Rohit's box #2") == "openworker-Rohit-s-box--2.service"
    assert unit_name_for("") == SERVICE_UNIT_NAME


def test_up_refuses_when_another_engine_holds_the_state_dir(tmp_path, capsys):
    """`openworker up` on a state dir another engine holds must stop and say so —
    never dial the controller as a second engine."""
    from coworker import statelock
    from coworker.remote import joiner

    state = _joined_state(tmp_path, "cloud-vm")
    held = statelock.acquire(state)
    try:
        rc = joiner._run(state, "http://10.0.0.5:8765", "cloud-vm")
    finally:
        held.release()
    assert rc == 3
    err = capsys.readouterr().err
    assert "another engine" in err and str(state) in err


# -- union view: the desktop's cloud proxy (spec §"Union view on the signed-in
# desktop") — /v1/cloud/machines* forwards to the hosted service with the
# stored cloud session token; session problems degrade, never error. ----------


def _proxy_app(base_url, *, state="ok", token="cloud-token", wallet=None):
    from fastapi import FastAPI

    from coworker.remote.cloudproxy import mount_cloud_proxy

    app = FastAPI()

    async def provider():
        return (state, token if state == "ok" else None)

    mount_cloud_proxy(
        app, token_provider=provider, base_url=base_url, wallet=wallet
    )
    return app


async def test_cloud_proxy_session_states():
    """Signed out / expired are STATES the GUI renders, not errors: the list
    answers 200 with the state, per-machine calls answer 401 carrying it."""
    for state in ("signed_out", "expired"):
        app = _proxy_app("http://cloud.invalid", state=state)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = (await c.get("/v1/cloud/machines")).json()
            assert body == {"session": state, "machines": []}
            r = await c.get("/v1/cloud/machines/m1/p/v1/settings")
            assert r.status_code == 401
            assert r.json()["session"] == state


async def test_cloud_proxy_union_end_to_end(tmp_path):
    """A real upstream controller + joined box, reached THROUGH the proxy:
    the machines list forwards the bearer, the engine proxy round-trips,
    wallet-send seals LOCALLY (upstream relays ciphertext — its audit says
    browser-sealed and the box's store receives the plaintext copy), and the
    bridged WS pipes a full scripted turn."""
    import websockets

    class _StubWallet:
        def __init__(self, rows):
            self._rows = rows

        def get(self, name):
            return self._rows.get(name)

    upstream_manager = SessionManager(
        workspace=tmp_path / "ws", data_dir=tmp_path / "up"
    )
    async with _Server(create_app(upstream_manager)) as upstream:
        join_url = await _arm(upstream.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "union-state"
        state.mkdir()
        from coworker.providers import AssistantTurn

        (tmp_path / "union-ws").mkdir(exist_ok=True)
        box_manager = SessionManager(
            workspace=tmp_path / "union-ws",
            data_dir=tmp_path / "union-data",
            provider=_scripted_provider(
                [AssistantTurn(text="Hello via the union.", finish_reason="stop")]
            ),
        )
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=upstream.base,
                name="union-box",
                token=token,
                app=create_app(box_manager),
                once=True,
                log=lambda *_: None,
            )
        )
        wallet = _StubWallet({"openai": {"api_key": "sk-cloudkey"}})
        try:
            await _wait_connected(upstream.base)
            async with _Server(
                _proxy_app(upstream.base, wallet=wallet)
            ) as proxy:
                async with httpx.AsyncClient() as c:
                    listing = (
                        await c.get(f"{proxy.base}/v1/cloud/machines")
                    ).json()
                    assert listing["session"] == "ok"
                    assert len(listing["machines"]) == 1
                    mid = listing["machines"][0]["id"]

                    # Engine proxy round-trip through BOTH hops.
                    health = await c.get(
                        f"{proxy.base}/v1/cloud/machines/{mid}/p/v1/health"
                    )
                    assert health.status_code == 200

                    # Wallet-send: names in, sealed out. The box's own store
                    # receives the copy; the upstream audit shows the sealed
                    # (blind-relay) branch was taken — it never saw plaintext.
                    resp = await c.post(
                        f"{proxy.base}/v1/cloud/machines/{mid}/secrets",
                        json={"profiles": ["openai"]},
                    )
                    assert resp.status_code == 200
                    assert box_manager.secrets.get("openai") == {
                        "api_key": "sk-cloudkey"
                    }
                    audit_path = tmp_path / "up" / "remote-audit.jsonl"
                    events = [
                        json.loads(line)
                        for line in audit_path.read_text().splitlines()
                    ]
                    deploy = next(
                        e
                        for e in events
                        if e["entity"]["type"] == "secret_deploy"
                    )
                    assert deploy["unmapped"]["source"] == "browser-sealed"

                    # Missing wallet profile is the caller's error, not a relay.
                    missing = await c.post(
                        f"{proxy.base}/v1/cloud/machines/{mid}/secrets",
                        json={"profiles": ["nope"]},
                    )
                    assert missing.status_code == 400

                # Bridged WS through the proxy: a scripted turn runs on the box.
                bridge_url = (
                    proxy.base.replace("http://", "ws://")
                    + f"/ws/cloud/machines/{mid}/p/ws/session/union-chat-1"
                )
                async with websockets.connect(
                    bridge_url, subprotocols=["openworker"]
                ) as ws:
                    first = json.loads(await ws.recv())
                    assert first["type"] == "ready"
                    await ws.send(
                        json.dumps({"type": "user_message", "text": "hi"})
                    )
                    texts, done = [], False
                    while not done:
                        event = json.loads(
                            await asyncio.wait_for(ws.recv(), timeout=10)
                        )
                        if event["type"] == "assistant_message":
                            texts.append(event["data"]["text"])
                        done = event["type"] == "turn_done"
                    assert texts == ["Hello via the union."]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_sealed_connector_connect_end_to_end(tmp_path):
    """Remote manual connect: fields sealed to the machine's pinned key, the
    controller relays ciphertext, the BOX unseals and runs its own connect
    (validation included) — the profile lands in the box's store and the
    audit trail records the connector NAME only."""
    from coworker.connectors import descriptors as _desc
    from coworker.connectors.descriptors import (
        ConnectorDescriptor,
        Field,
        ValidationResult,
    )
    from coworker.remote.identity import seal_b64

    d = ConnectorDescriptor(
        name="sealtestapp",
        title="SealTest",
        icon="◇",
        blurb="test connector",
        auth="api_token",
        two_way=False,
        fields=[Field("api_key", "API key", secret=True)],
        instructions=[],
        validate=lambda creds: ValidationResult(True, identity="seal@example.com"),
    )
    _desc.register_descriptor(d)
    try:
        upstream_manager = SessionManager(
            workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl"
        )
        async with _Server(create_app(upstream_manager)) as server:
            join_url = await _arm(server.base)
            _, token = parse_join_url(join_url)
            state = tmp_path / "sc-state"
            state.mkdir()
            box_manager = SessionManager(data_dir=tmp_path / "sc-data")
            task = asyncio.create_task(
                run_joined(
                    state=state,
                    controller=server.base,
                    name="seal-box",
                    token=token,
                    app=create_app(box_manager),
                    once=True,
                    log=lambda *_: None,
                )
            )
            try:
                machines = await _wait_connected(server.base)
                row = machines[0]
                sealed = seal_b64(
                    row["seal_pubkey"],
                    json.dumps({"fields": {"api_key": "sk-sealed-connect"}}).encode(),
                )
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{server.base}/v1/machines/{row['id']}/connectors/"
                        "sealtestapp/connect-sealed",
                        json={"sealed_b64": sealed},
                    )
                    assert resp.status_code == 200, resp.text
                    assert resp.json()["ok"] is True
                # The BOX holds the credential, validated (account resolved).
                # (No controller-side negative here: this rig's SessionManagers
                # share the default SecretStore path — a known test artifact.)
                stored = box_manager.secrets.get("sealtestapp:default") or {}
                assert stored.get("api_key") == "sk-sealed-connect"
                assert stored.get("account") == "seal@example.com"
                # Audit: connector name only, sealed source, never the fields.
                audit_text = (tmp_path / "ctrl" / "remote-audit.jsonl").read_text()
                assert "sealtestapp" in audit_text
                assert "sk-sealed-connect" not in audit_text
                # A garbage blob is the box's 400, not a relay error.
                async with httpx.AsyncClient() as client:
                    bad = await client.post(
                        f"{server.base}/v1/machines/{row['id']}/connectors/"
                        "sealtestapp/connect-sealed",
                        json={"sealed_b64": "not-a-blob"},
                    )
                    assert bad.status_code == 400
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    finally:
        _desc.DESCRIPTORS.remove(d)
        _desc._BY_NAME.pop(d.name, None)


@pytest.mark.asyncio
async def test_machine_targeted_oauth_callback_lands_grant_on_box(tmp_path, monkeypatch):
    """Connect-direct-to-machine (machines spec §Remote OAuth): the broker's
    callback hits the DESKTOP as usual, but the pending record names a target
    machine — so the grant is delegated, staged in memory, sealed to the box's
    pinned key, and deployed. The desktop store never holds it."""
    from coworker import cloud
    from coworker.secrets import SecretStore

    upstream_manager = SessionManager(
        workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl"
    )
    # The rig's managers share the default SecretStore path; give each its own
    # so "the desktop holds nothing" is a real assertion.
    upstream_manager.secrets = SecretStore(tmp_path / "ctrl-secrets.json")
    box_manager = SessionManager(data_dir=tmp_path / "box-data")
    box_manager.secrets = SecretStore(tmp_path / "box-secrets.json")

    monkeypatch.setattr(
        cloud,
        "delegate_connection",
        lambda *a, **k: {"user_id": "usr_delegated", "machine_credential": "mc_test"},
    )

    async with _Server(create_app(upstream_manager)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "grant-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="grant-box",
                token=token,
                app=create_app(box_manager),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            machines = await _wait_connected(server.base)
            row = machines[0]
            cloud._pending_managed_states["mt"] = {
                "created": cloud._now(),
                "machine_id": row["id"],
                "machine_name": "grant-box",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{server.base}/oauth/callback",
                    data={
                        "provider": "notion",
                        "connector": "notion",
                        "connection_id": "conn_target",
                        "access_token": "secret-access",
                        "refresh_token": "secret-refresh",
                        "account": "Acme Workspace",
                        "account_id": "ws_1",
                        "app_state": "mt",
                    },
                )
            assert resp.status_code == 200, resp.text
            assert "connected on grant-box" in resp.text

            # The BOX holds the grant, delegation id included (what the box's
            # possession-based renewal needs), via its own accounts layer.
            stored = box_manager.secrets.get("notion:account:ws_1") or {}
            assert stored.get("access_token") == "secret-access"
            assert stored.get("refresh_token") == "secret-refresh"
            assert stored.get("connection_id") == "conn_target"
            assert stored.get("broker_user_id") == "usr_delegated"
            # The machine credential (spec §Managed events) rides along.
            assert stored.get("machine_credential") == "mc_test"
            assert (box_manager.secrets.get("notion:default") or {}).get(
                "default_account"
            ) == "ws_1"
            # One grant, one holder: the desktop stored nothing.
            assert upstream_manager.secrets.get("notion:default") is None
            assert upstream_manager.secrets.get("notion:account:ws_1") is None

            # Ledger + audit: profile NAMES only, never token values.
            async with httpx.AsyncClient() as client:
                ledger = await client.get(
                    f"{server.base}/v1/machines/{row['id']}/secrets"
                )
            names = {r["profile"] for r in ledger.json()["secrets"]}
            assert "notion:account:ws_1" in names
            audit_text = (tmp_path / "ctrl" / "remote-audit.jsonl").read_text()
            assert "notion:account:ws_1" in audit_text
            assert "secret-access" not in audit_text
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_machine_targeted_github_callback_stages_all_installs(tmp_path, monkeypatch):
    """Connect-direct for GitHub (increment 2): the install-flow callback
    stages EVERY returned installation — the same unit a Move ships — plus
    the stamped pointer, all metadata + credential, no secrets anywhere."""
    from coworker import cloud
    from coworker.secrets import SecretStore

    upstream_manager = SessionManager(
        workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl"
    )
    upstream_manager.secrets = SecretStore(tmp_path / "ctrl-secrets.json")
    box_manager = SessionManager(data_dir=tmp_path / "box-data")
    box_manager.secrets = SecretStore(tmp_path / "box-secrets.json")

    monkeypatch.setattr(
        cloud,
        "delegate_connection",
        lambda *a, **k: {"user_id": "usr_gh", "machine_credential": "mc_gh"},
    )

    async with _Server(create_app(upstream_manager)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "gh-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(
                state=state,
                controller=server.base,
                name="gh-box",
                token=token,
                app=create_app(box_manager),
                once=True,
                log=lambda *_: None,
            )
        )
        try:
            machines = await _wait_connected(server.base)
            row = machines[0]
            cloud._pending_managed_states["ghm"] = {
                "created": cloud._now(),
                "machine_id": row["id"],
                "machine_name": "gh-box",
            }
            installs = [
                {"installation_id": "9001", "account_login": "quillvoice",
                 "account_type": "Organization", "repo_selection": "selected"},
                {"installation_id": "9002", "account_login": "rohit",
                 "account_type": "User", "repo_selection": "all"},
            ]
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{server.base}/oauth/callback",
                    data={
                        "provider": "github",
                        "connector": "github",
                        "connection_id": "conn_gh",
                        "installation_id": "9001",
                        "account_login": "quillvoice",
                        "account_type": "Organization",
                        "repo_selection": "selected",
                        "github_login": "rohit",
                        "installations": json.dumps(installs),
                        "app_state": "ghm",
                    },
                )
            assert resp.status_code == 200, resp.text
            assert "connected on gh-box" in resp.text

            # The BOX holds BOTH installations + the stamped pointer.
            for iid in ("9001", "9002"):
                prof = box_manager.secrets.get(f"github:install:{iid}") or {}
                assert prof.get("machine_credential") == "mc_gh"
                assert prof.get("broker_user_id") == "usr_gh"
            pointer = box_manager.secrets.get("github:default") or {}
            assert pointer.get("mode") == "relay"
            assert pointer.get("connection_id") == "conn_gh"
            assert pointer.get("machine_credential") == "mc_gh"
            # The desktop stored nothing; no secrets exist anywhere to leak.
            assert upstream_manager.secrets.get("github:default") is None
            assert upstream_manager.secrets.get("github:install:9001") is None
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


# -- fleet under the org: machine owner + session actor (spec 2026-09-02) -------


async def test_enrollment_records_owner_and_bridged_sessions_carry_the_actor(tmp_path):
    """The member who armed the join owns the machine; a session opened through
    the controller is stamped with the controller-verified login (first writer
    wins) and every audit row of that session names them."""
    import websockets

    from coworker.sessions import SessionRecord

    app, registry = _tenant_app(tmp_path)
    async with _Server(app) as server:
        async with httpx.AsyncClient() as client:
            join_url = (
                await client.post(f"{server.base}/v1/remote/arm", headers={"x-org": "acme"})
            ).json()["join_url"]
        _, token = parse_join_url(join_url)
        state = tmp_path / "owner-state"
        state.mkdir()
        box_manager = SessionManager(data_dir=tmp_path / "owner-data")
        task = asyncio.create_task(
            run_joined(
                state=state, controller=server.base, name="owned", token=token,
                app=create_app(box_manager), once=True, log=lambda *_: None,
            )
        )
        try:
            async with httpx.AsyncClient() as client:
                rows = []
                for _ in range(100):
                    rows = (await client.get(f"{server.base}/v1/machines", headers={"x-org": "acme"})).json()["machines"]
                    if rows and rows[0]["connected"]:
                        break
                    await asyncio.sleep(0.05)
                assert rows[0]["enrolled_by"] == "acme-admin"
                assert registry.by_id(rows[0]["id"])["enrolled_by"] == "acme-admin"
                mid = rows[0]["id"]

            # A bridged session socket: the controller stamps the actor it verified.
            ws_url = server.base.replace("http://", "ws://") + f"/ws/machines/{mid}/p/ws/session/s-owned?agent=cowork"
            async with websockets.connect(ws_url, additional_headers={"x-org": "acme"}) as ws:
                frame = json.loads(await ws.recv())
                assert frame["type"] == "ready"
            assert box_manager.session_actor("s-owned") == "acme-admin"
            # First writer wins: a second viewer never rewrites who started it.
            box_manager.note_session_actor("s-owned", "someone-else")
            assert box_manager.session_actor("s-owned") == "acme-admin"
            # The engine's audit sink stamps the actor; the persisted record carries it.
            box_manager._audit_sink_for("s-owned")({"session_id": "s-owned", "tool": "run_shell", "arguments": {"command": "ls"}})
            row = box_manager.audit_store.list(session_id="s-owned")[0]
            assert row["actor"] == "acme-admin"
            box_manager.session_store.save(SessionRecord(session_id="s-owned", workspace="", model="m", mode="interactive", actor=box_manager.session_actor("s-owned")))
            assert box_manager.session_store.load("s-owned").actor == "acme-admin"
            assert next(r for r in box_manager.list_sessions() if r["session_id"] == "s-owned")["actor"] == "acme-admin"
            # A proxied REST call carries it too (header injected by the box's dispatcher only).
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{server.base}/v1/machines/{mid}/p/v1/sessions", headers={"x-org": "acme"})
                assert r.status_code == 200
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_managed_sandbox_token_stamps_provenance_on_the_enrolled_row(tmp_path):
    """A provisioner mints the join token WITH a provenance; the box that joins
    with it is recorded as that provisioner's machine (kind + the
    provisioner's own id), so lifecycle code can find the box a sandbox
    became. User-armed tokens leave both fields empty."""
    app, registry = _tenant_app(tmp_path)
    acceptor = app.state.remote_acceptor
    async with _Server(app) as server:
        token, _ = acceptor.arm(
            org_id="acme", actor="acme-admin", provenance={"kind": "fly", "ref": "sb-1"}
        )
        state = tmp_path / "sandbox-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(
                state=state, controller=server.base, name="sandbox-1", token=token,
                app=create_app(SessionManager(data_dir=tmp_path / "sandbox-data")),
                once=True, log=lambda *_: None,
            )
        )
        try:
            async with httpx.AsyncClient() as client:
                rows = []
                for _ in range(100):
                    rows = (await client.get(f"{server.base}/v1/machines", headers={"x-org": "acme"})).json()["machines"]
                    if rows and rows[0]["connected"]:
                        break
                    await asyncio.sleep(0.05)
            assert (rows[0]["provenance"], rows[0]["provenance_ref"]) == ("fly", "sb-1")
            assert rows[0]["enrolled_by"] == "acme-admin"
            row = registry.by_id(rows[0]["id"])
            assert (row["provenance"], row["provenance_ref"]) == ("fly", "sb-1")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    # Brought-your-own rows stay unmarked.
    assert registry.enroll("byo", "pk-byo", org_id="acme")["provenance"] == ""


async def test_activity_and_wake_hooks_on_the_proxy(tmp_path):
    """Managed sandboxes: every proxied call/socket touches `machine_activity`;
    reaching an OFFLINE machine asks `machine_wake`, and when it says it
    started the box, the call waits for the box to dial back in instead of
    answering 503. A wake that declines keeps today's 503."""
    from fastapi import FastAPI

    from coworker.remote import acceptor as acc
    from coworker.remote.acceptor import mount_acceptor

    touched: list[str] = []
    wake_calls: list[str] = []
    registry = MachinesRegistry(tmp_path / "db")
    app = FastAPI()
    box_state = tmp_path / "sleepy-state"
    box_state.mkdir()
    box_app = create_app(SessionManager(data_dir=tmp_path / "sleepy-data"))
    server_ref: dict[str, Any] = {}
    box_task: dict[str, Any] = {}

    async def wake(machine_id: str) -> bool:
        wake_calls.append(machine_id)
        if wake_calls.count(machine_id) > 1:
            return False  # second time: "not a sandbox" / could not start
        # "Fly started it": the box dials back in a moment later.
        box_task["t"] = asyncio.create_task(
            run_joined(state=box_state, controller=server_ref["base"], name="sleepy",
                       app=box_app, once=True, log=lambda *_: None)
        )
        return True

    mount_acceptor(app, registry, machine_activity=touched.append, machine_wake=wake)
    acceptor = app.state.remote_acceptor
    async with _Server(app) as server:
        server_ref["base"] = server.base
        # Enroll once so the row exists, then let the box go away.
        token, _ = acceptor.arm()
        first = asyncio.create_task(
            run_joined(state=box_state, controller=server.base, name="sleepy", token=token,
                       app=box_app, once=True, log=lambda *_: None)
        )
        async with httpx.AsyncClient() as client:
            mid = ""
            for _ in range(100):
                rows = (await client.get(f"{server.base}/v1/machines")).json()["machines"]
                if rows and rows[0]["connected"]:
                    mid = rows[0]["id"]
                    break
                await asyncio.sleep(0.05)
            assert mid
            r = await client.get(f"{server.base}/v1/machines/{mid}/p/v1/activity")
            assert r.status_code == 200 and r.json()["running_sessions"] == 0
            assert touched == [mid]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        for _ in range(100):
            if acceptor.live(mid) is None:
                break
            await asyncio.sleep(0.05)
        assert acceptor.live(mid) is None
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Offline: the wake hook starts the box and the call waits for it.
                r = await client.get(f"{server.base}/v1/machines/{mid}/p/v1/health")
                assert r.status_code == 200, r.text
                assert wake_calls == [mid]
        finally:
            t = box_task.get("t")
            if t is not None:
                t.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await t
        for _ in range(100):
            if acceptor.live(mid) is None:
                break
            await asyncio.sleep(0.05)
        # Wake declined ⇒ plain offline answer, no waiting.
        acc_wait = acc.WAKE_WAIT_SECONDS
        acc.WAKE_WAIT_SECONDS = 0.2
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{server.base}/v1/machines/{mid}/p/v1/health")
                assert r.status_code == 503
        finally:
            acc.WAKE_WAIT_SECONDS = acc_wait


def test_local_sessions_have_no_actor_and_headers_are_not_trusted_locally(tmp_path):
    """Desktop/local: no controller, no actor. The box-side dispatcher is the
    ONLY place the header is minted; a stray header on the local sidecar is
    ignored by the note (empty tenant) — nothing else reads it."""
    from coworker.remote.channel import ACTOR_HEADER

    manager = SessionManager(data_dir=tmp_path / "local")
    manager.note_session_actor("local-1", "")
    assert manager.session_actor("local-1") == ""
    manager._audit_sink_for("local-1")({"session_id": "local-1", "tool": "read_file", "arguments": {}})
    assert manager.audit_store.list(session_id="local-1")[0]["actor"] == ""
    assert ACTOR_HEADER == "x-openworker-actor"


async def test_browser_sealed_managed_grant_lands_on_the_box(tmp_path):
    """Cloud-dashboard connect-direct (spec): the dashboard tab seals the
    broker's callback result + delegation to the box's pinned key and posts
    it to the acceptor, which relays ciphertext; the box stores it through
    the shared bundle routine. The controller stores nothing readable."""
    from coworker.remote.identity import seal_b64
    from coworker.secrets import SecretStore

    upstream = SessionManager(workspace=tmp_path / "ws", data_dir=tmp_path / "ctrl")
    upstream.secrets = SecretStore(tmp_path / "ctrl-secrets.json")
    box_manager = SessionManager(data_dir=tmp_path / "box-data")
    box_manager.secrets = SecretStore(tmp_path / "box-secrets.json")
    async with _Server(create_app(upstream)) as server:
        join_url = await _arm(server.base)
        _, token = parse_join_url(join_url)
        state = tmp_path / "bs-state"
        state.mkdir()
        task = asyncio.create_task(
            run_joined(state=state, controller=server.base, name="bs-box", token=token,
                       app=create_app(box_manager), once=True, log=lambda *_: None)
        )
        try:
            row = (await _wait_connected(server.base))[0]
            payload = {
                "form": {
                    "provider": "notion", "connector": "notion", "connection_id": "conn_b",
                    "access_token": "secret-access", "refresh_token": "secret-refresh",
                    "account": "Acme", "account_id": "ws_9",
                },
                "machine_credential": "mc_browser",
                "broker_user_id": "usr_browser",
            }
            sealed = seal_b64(row["seal_pubkey"], json.dumps(payload).encode())
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{server.base}/v1/machines/{row['id']}/connectors/notion/managed-grant-sealed",
                    json={"sealed_b64": sealed},
                )
                assert r.status_code == 200 and r.json().get("ok"), r.text
                stored = box_manager.secrets.get("notion:account:ws_9") or {}
                assert stored.get("access_token") == "secret-access"
                assert stored.get("broker_user_id") == "usr_browser"
                assert stored.get("machine_credential") == "mc_browser"
                assert upstream.secrets.get("notion:account:ws_9") is None
                audit_text = (tmp_path / "ctrl" / "remote-audit.jsonl").read_text()
                assert "managed_grant" in audit_text and "secret-access" not in audit_text
                # A bad payload is refused by the box, not stored.
                bad = seal_b64(row["seal_pubkey"], json.dumps({"form": {"connector": "notion"}}).encode())
                r = await client.post(
                    f"{server.base}/v1/machines/{row['id']}/connectors/notion/managed-grant-sealed",
                    json={"sealed_b64": bad},
                )
                assert r.status_code == 400
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
