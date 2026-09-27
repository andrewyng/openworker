"""Machine-held Slack inbound (machines spec §Managed events): the poll
transport that drains the broker's sealed queue, its wiring into the relay
adapter, and the delegation stamping that arms it."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coworker.connectors.machine_poll import MachinePollTransport
from coworker.remote.identity import load_or_create, seal_b64
from coworker.server import SessionManager, create_app


def _transport(tmp_path, events_batches, interval=0.0):
    """A transport whose poll rounds are canned; unseal = a real box identity."""
    identity = load_or_create(tmp_path / "box-id")
    t = MachinePollTransport(
        "https://broker.test",
        connection_id="conn_1",
        user_id="usr_1",
        credential="mc_t",
        unseal=identity.unseal_b64,
        interval=interval,
    )
    batches = list(events_batches)

    async def _fake_poll_once():
        return batches.pop(0) if batches else []

    t._poll_once = _fake_poll_once
    return t, identity


def _sealed(identity, frame: dict) -> dict:
    return {
        "event_key": "k",
        "sealed_b64": seal_b64(identity.seal_public_key_b64, json.dumps(frame).encode()),
    }


@pytest.mark.asyncio
async def test_transport_unseals_and_orders_frames(tmp_path):
    identity = load_or_create(tmp_path / "box-id")
    one = _sealed(identity, {"provider": "slack", "n": 1})
    two = _sealed(identity, {"provider": "slack", "n": 2})
    t, _ = _transport(tmp_path, [[one, two]])
    await t.open()
    assert (await t.recv())["n"] == 1
    assert (await t.recv())["n"] == 2
    await t.close()


@pytest.mark.asyncio
async def test_transport_skips_garbage_keeps_going(tmp_path):
    identity = load_or_create(tmp_path / "box-id")
    good = _sealed(identity, {"provider": "slack", "n": 1})
    t, _ = _transport(
        tmp_path, [[{"event_key": "bad", "sealed_b64": "not-a-blob"}, good]]
    )
    await t.open()
    # The garbage blob is logged and dropped; the good frame still arrives.
    assert (await t.recv())["n"] == 1
    await t.close()


@pytest.mark.asyncio
async def test_transport_close_ends_recv(tmp_path):
    t, _ = _transport(tmp_path, [], interval=30.0)
    await t.open()
    task = asyncio.create_task(t.recv())
    await asyncio.sleep(0.05)
    await t.close()
    assert await asyncio.wait_for(task, timeout=1.0) is None


@pytest.mark.asyncio
async def test_hub_dispatches_polled_frames(tmp_path):
    """The RelayHub can't tell this transport from the desktop WebSocket —
    frames fan out to the registered provider handler exactly the same."""
    from coworker.connectors.relay_client import RelayHub

    identity = load_or_create(tmp_path / "box-id")
    frame = {"provider": "slack", "kind": "event", "text": "hi"}
    t, _ = _transport(tmp_path, [[_sealed(identity, frame)]], interval=5.0)
    hub = RelayHub("unused://", lambda: "", transport_factory=lambda: t)
    seen: list[dict] = []

    async def handler(f):
        seen.append(f)

    hub.register("slack", handler)
    assert await hub.start()
    await hub.wait_dispatched(1)
    await hub.stop()
    assert seen and seen[0]["text"] == "hi"


def test_make_adapter_prefers_poll_transport_on_boxes(tmp_path):
    from coworker.connectors import make_adapter
    from coworker.connectors.relay_client import SlackRelayAdapter
    from coworker.secrets import EphemeralSecretStore

    secrets = EphemeralSecretStore()
    profile = {
        "mode": "relay",
        "managed": True,
        "connection_id": "conn_1",
        "broker_user_id": "usr_1",
        "machine_credential": "mc_t",
    }
    identity = load_or_create(tmp_path / "box-id")
    adapter = make_adapter(
        "slack",
        profile,
        secrets=secrets,
        machine_unseal=identity.unseal_b64,
        machine_poll_base="https://broker.test",
    )
    assert isinstance(adapter, SlackRelayAdapter)
    transport = adapter._hub._transport_factory()
    assert isinstance(transport, MachinePollTransport)
    # Desktop (no unseal): the stamped profile alone never takes this branch.
    assert (
        make_adapter("slack", profile, secrets=secrets, machine_poll_base="x") is None
    )


# --- delegation stamping (the sidecar side that arms the transport) -----------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    with TestClient(app) as c:
        c.manager = manager
        yield c


def test_slack_delegate_dedupes_and_stamps_pointer(client, monkeypatch):
    """Every Slack workspace shares ONE broker connection: the handoff must
    delegate once (a second call would rotate the credential) and stamp the
    grant into each team profile AND the pointer the poll adapter reads."""
    from coworker import cloud

    secrets = client.manager.secrets
    secrets.put(
        "slack:default", {"type": "oauth", "managed": True, "mode": "relay", "enabled": True}
    )
    for team in ("T1", "T2"):
        secrets.put(
            f"slack:team:{team}",
            {
                "type": "oauth",
                "managed": True,
                "bot_token": f"xoxb-{team}",
                "team_id": team,
                "connection_id": "conn_slack",
            },
        )
    calls: list[tuple[str, str]] = []

    def fake_delegate(_s, _c, connection_id, *, seal_pubkey="", machine_id=""):
        calls.append((connection_id, seal_pubkey))
        return {"user_id": "usr_1", "machine_credential": "mc_minted"}

    monkeypatch.setattr(cloud, "delegate_connection", fake_delegate)
    r = client.post(
        "/v1/connectors/slack/delegate", json={"seal_pubkey": "SEALPUB"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert calls == [("conn_slack", "SEALPUB")]  # deduped: one call, key passed
    for team in ("T1", "T2"):
        prof = secrets.get(f"slack:team:{team}")
        assert prof["broker_user_id"] == "usr_1"
        assert prof["machine_credential"] == "mc_minted"
    pointer = secrets.get("slack:default")
    assert pointer["connection_id"] == "conn_slack"
    assert pointer["broker_user_id"] == "usr_1"
    assert pointer["machine_credential"] == "mc_minted"


def test_slack_relay_handoff_is_portable_now(client):
    """Machine events lifted the cloud-runtime gate: relay Slack moves, its
    unit = every team profile + the pointer, delegation required."""
    from coworker.connectors.setup import connector_handoff_info

    secrets = client.manager.secrets
    secrets.put(
        "slack:default", {"type": "oauth", "managed": True, "mode": "relay", "enabled": True}
    )
    secrets.put(
        "slack:team:T1",
        {
            "type": "oauth",
            "managed": True,
            "bot_token": "xoxb-1",
            "team_id": "T1",
            "connection_id": "conn_slack",
        },
    )
    info = connector_handoff_info(secrets, "slack")
    assert info["portable"] is True
    assert info["needs_delegation"] is True
    assert set(info["profiles"]) == {"slack:team:T1", "slack:default"}


def test_revoke_connector_connections_by_name(client, monkeypatch):
    """The drill finding: a machine-scope disconnect of a managed grant needs
    the DESKTOP to revoke at the broker (the box has no session) — by
    connector NAME, since the desktop forgot the profiles at handoff."""
    from coworker import cloud

    client.manager.secrets.put(
        cloud.CLOUD_AUTH_PROFILE, {"access_token": "sess", "expires": 9999999999}
    )
    calls: list[str] = []

    class _Resp:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def json(self):
            return self._body

    def fake_get(url, headers=None, timeout=None):
        return _Resp(
            200,
            {
                "connections": [
                    {"connection_id": "conn_slack", "connector": "slack", "status": "connected"},
                    {"connection_id": "conn_dead", "connector": "slack", "status": "disconnected"},
                    {"connection_id": "conn_other", "connector": "notion", "status": "connected"},
                ]
            },
        )

    def fake_post(url, headers=None, timeout=None):
        calls.append(url)
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(cloud.httpx, "get", fake_get)
    monkeypatch.setattr(cloud.httpx, "post", fake_post)
    r = client.post("/v1/cloud/connections/slack/revoke")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "revoked": 1}
    # Only the LIVE slack connection was disconnected — never other
    # connectors, never already-dead rows.
    assert len(calls) == 1 and calls[0].endswith("/v1/connections/conn_slack/disconnect")


# --- increment 2: GitHub on the same spine ------------------------------------


def _seed_github_install(secrets, installation_id="9001"):
    from coworker.connectors.github_installs import managed_connect_install

    return managed_connect_install(
        secrets,
        {
            "installation_id": installation_id,
            "account_login": "quillvoice",
            "account_type": "Organization",
            "github_login": "rohit",
            "repo_selection": "selected",
            "connection_id": "conn_gh",
        },
    )


def test_github_handoff_is_portable_metadata_only(client):
    """A GitHub handoff moves NO secrets: installation routing metadata plus
    the pointer — tokens mint on demand via the machine credential."""
    from coworker.connectors.setup import connector_handoff_info

    secrets = client.manager.secrets
    _seed_github_install(secrets)
    info = connector_handoff_info(secrets, "github")
    assert info["portable"] is True
    assert info["needs_delegation"] is True
    assert set(info["profiles"]) == {"github:install:9001", "github:default"}


def test_github_delegate_stamps_pointer(client, monkeypatch):
    from coworker import cloud

    secrets = client.manager.secrets
    _seed_github_install(secrets)
    monkeypatch.setattr(
        cloud,
        "delegate_connection",
        lambda *a, **k: {"user_id": "usr_1", "machine_credential": "mc_gh"},
    )
    r = client.post("/v1/connectors/github/delegate", json={"seal_pubkey": "PUB"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    pointer = secrets.get("github:default")
    assert pointer["connection_id"] == "conn_gh"
    assert pointer["machine_credential"] == "mc_gh"
    assert secrets.get("github:install:9001")["machine_credential"] == "mc_gh"


def test_github_token_mints_by_credential_without_session(monkeypatch, tmp_path):
    """The box fallback: no cloud session → mint on the delegated route with
    the pointer's stamps, bearer = machine credential."""
    from coworker import cloud
    from coworker.config import Config
    from coworker.secrets import EphemeralSecretStore

    secrets = EphemeralSecretStore()
    secrets.put(
        "github:default",
        {
            "mode": "relay",
            "managed": True,
            "connection_id": "conn_gh",
            "broker_user_id": "usr_1",
            "machine_credential": "mc_gh",
        },
    )
    seen = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"token": "ghs_BOX", "expires_at": ""}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update({"url": url, "json": json, "auth": headers["Authorization"]})
        return _Resp()

    monkeypatch.setattr(cloud.httpx, "post", fake_post)
    cloud._GITHUB_TOKEN_CACHE.clear()
    token = cloud.github_installation_token(secrets, Config(), "9001", force=True)
    assert token == "ghs_BOX"
    assert seen["url"].endswith("/v1/machine/github/mint")
    assert seen["json"]["connection_id"] == "conn_gh"
    assert seen["auth"] == "Bearer mc_gh"
    cloud._GITHUB_TOKEN_CACHE.clear()


def test_make_adapter_github_poll_transport_on_boxes(tmp_path):
    from coworker.connectors import make_adapter
    from coworker.connectors.github_relay import GitHubRelayAdapter
    from coworker.secrets import EphemeralSecretStore

    secrets = EphemeralSecretStore()
    _ = secrets  # installs empty is fine
    profile = {
        "mode": "relay",
        "managed": True,
        "connection_id": "conn_gh",
        "broker_user_id": "usr_1",
        "machine_credential": "mc_gh",
    }
    identity = load_or_create(tmp_path / "box-id")
    adapter = make_adapter(
        "github",
        profile,
        secrets=secrets,
        machine_unseal=identity.unseal_b64,
        machine_poll_base="https://broker.test",
    )
    assert isinstance(adapter, GitHubRelayAdapter)
    assert isinstance(adapter._hub._transport_factory(), MachinePollTransport)
    # Desktop shape unchanged: without unseal, relay needs url+sign-in.
    assert make_adapter("github", profile, secrets=secrets) is None


@pytest.mark.asyncio
async def test_github_frame_is_a_mention_for_the_router(tmp_path):
    """Drill finding: every routed GitHub frame is a directed ask (the broker
    only forwards wave-1 triggers), so the adapter must flag mentions_me —
    without it the mention router never spawns a session for GitHub."""
    from coworker.connectors.github_relay import GitHubRelayAdapter
    from coworker.connectors.relay_client import RelayHub

    adapter = GitHubRelayAdapter(RelayHub("unused://", lambda: ""), installs={})
    seen = []

    async def capture(event):
        seen.append(event)

    adapter.handle_message = capture
    await adapter._on_event(
        {
            "provider": "github",
            "kind": "mention",
            "installation_id": "9001",
            "owner_repo": "o/r",
            "number": 1,
            "sender": "rohit",
            "body": "@openworker-agent do the thing",
        }
    )
    assert seen and seen[0].mentions_me is True


def test_parked_message_preserves_mention_flag(tmp_path):
    """Park → allow_deliver must not lose the mention bit (the re-injected
    event takes the §31 mention route)."""
    from coworker.connectors.parked import ParkedStore

    store = ParkedStore(tmp_path / "parked.json")
    item = store.park(
        platform="github",
        chat_id="o/r#1",
        user_id="rohit",
        text="[mention] hi",
        team_id="9001",
        mentions_me=True,
    )
    assert store.pop(item.id).mentions_me is True


def test_send_message_github_posts_issue_comment(monkeypatch):
    """Drill finding: send_message had NO github path ("unknown platform").
    Now it comments on the issue/PR thread, minting via the same auth ladder
    as the read tools — which on a box is the machine credential."""
    from coworker.connectors.tools import make_send_message_tool
    from coworker.secrets import EphemeralSecretStore

    secrets = EphemeralSecretStore()
    secrets.put(
        "github:default",
        {
            "mode": "relay",
            "managed": True,
            "connection_id": "conn_gh",
            "broker_user_id": "usr_1",
            "machine_credential": "mc_gh",
        },
    )
    secrets.put(
        "github:install:9001",
        {"managed": True, "installation_id": "9001", "account_login": "rohitcpbot"},
    )
    from coworker import cloud

    monkeypatch.setattr(
        cloud, "github_installation_token", lambda *a, **k: "ghs_MINTED"
    )
    seen = {}

    class _Resp:
        status_code = 201

        @staticmethod
        def json():
            return {"id": 4242}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update({"url": url, "auth": headers.get("Authorization", "")})
        return _Resp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "post", fake_post)

    tool = make_send_message_tool(secrets)
    out = tool("github:rohitcpbot/dailypuzzle#2", "All set — connected and listening.")
    assert out == {
        "ok": True,
        "message_id": "4242",
        "target": "github:rohitcpbot/dailypuzzle#2",
    }
    assert seen["url"].endswith("/repos/rohitcpbot/dailypuzzle/issues/2/comments")
    assert seen["auth"] == "Bearer ghs_MINTED"



async def test_github_frame_carries_the_broker_target_session():
    """Connectors-across-machines spec §3: a subscribed repo's events name
    the responder; the adapter passes it through untouched."""
    from coworker.connectors.github_relay import GitHubRelayAdapter
    from coworker.connectors.relay_client import RelayHub

    adapter = GitHubRelayAdapter(RelayHub("unused://", lambda: ""), installs={})
    seen = []

    async def capture(event):
        seen.append(event)

    adapter.handle_message = capture
    base = {"provider": "github", "kind": "mention", "installation_id": "9001",
            "owner_repo": "o/r", "number": 1, "sender": "rohit", "body": "@ow hi"}
    await adapter._on_event({**base, "target_session_id": "lead-1"})
    await adapter._on_event(base)
    assert [e.target_session_id for e in seen] == ["lead-1", None]


def test_cursor_persists_across_transports(tmp_path):
    """A restart must not replay the broker's ~15 min of queued events (drill
    finding 2026-09-04: a duplicate PR session): the cursor is kept on disk."""
    from coworker.connectors.machine_poll import MachinePollTransport

    path = tmp_path / "poll-cursors" / "conn.cursor"
    t = MachinePollTransport("http://x", connection_id="c", user_id="u", credential="mc", unseal=lambda b: b"", cursor_path=path)
    assert t._cursor == ""
    t._cursor = "1788574089744#81997fbe"
    t._save_cursor()
    t2 = MachinePollTransport("http://x", connection_id="c", user_id="u", credential="mc", unseal=lambda b: b"", cursor_path=path)
    assert t2._cursor == "1788574089744#81997fbe"


@pytest.mark.asyncio
async def test_hub_does_not_block_on_a_slow_handler(tmp_path):
    """Two frames; the first handler takes a while. The second must be handled
    before the first finishes (a review turn must not hold a merge back)."""
    from coworker.connectors.relay_client import RelayHub

    identity = load_or_create(tmp_path / "box-id")
    f1 = {"provider": "slack", "kind": "event", "text": "slow"}
    f2 = {"provider": "slack", "kind": "event", "text": "fast"}
    t, _ = _transport(tmp_path, [[_sealed(identity, f1), _sealed(identity, f2)]], interval=5.0)
    hub = RelayHub("unused://", lambda: "", transport_factory=lambda: t)
    order: list[str] = []
    gate = asyncio.Event()

    async def handler(f):
        if f["text"] == "slow":
            await gate.wait()
        order.append(f["text"])

    hub.register("slack", handler)
    assert await hub.start()
    await hub.wait_dispatched(1)  # "fast" completes while "slow" is still parked
    assert order == ["fast"]
    gate.set()
    await hub.wait_dispatched(2)
    await hub.stop()
    assert order == ["fast", "slow"]
