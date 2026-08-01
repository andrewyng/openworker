from __future__ import annotations

import threading
import time

import pytest

from coworker.browser_external import (
    AuthenticationError,
    ExternalBrowserBridge,
    PairingError,
    ProtocolValidationError,
    TabNotClaimed,
)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def paired(
    bridge: ExternalBrowserBridge, *, browser: str = "chrome"
) -> tuple[str, str]:
    challenge = bridge.create_pairing_code(browser=browser)
    client = bridge.exchange_pairing_code(
        challenge.code,
        client={
            "browser": browser,
            "browser_version": "149.0.0.0",
            "extension_version": "0.1.0",
            "client_id": "test-client",
        },
    )
    return client.session_id, client.session_token


def test_native_host_connects_without_a_human_pairing_secret() -> None:
    bridge = ExternalBrowserBridge()
    client = bridge.connect_native_client(
        client={
            "browser": "chrome",
            "browser_version": "149.0.0.0",
            "extension_version": "0.2.0",
            "client_id": "native-host-test",
        }
    )

    assert client.browser == "chrome"
    assert client.session_token not in repr(client)
    assert bridge.poll_commands(client.session_token, wait_seconds=0) == []

    with pytest.raises(PairingError) as wrong_browser:
        bridge.connect_native_client(client={"browser": "edge"})
    assert wrong_browser.value.code == "BROWSER_MISMATCH"


def claim(bridge: ExternalBrowserBridge, token: str, tab_id: int = 7) -> None:
    bridge.publish_event(
        token,
        {
            "type": "tab_claimed",
            "tab_id": tab_id,
            "title": "Fixture",
            "url": "https://example.test/",
        },
    )


def test_pairing_code_is_one_time_and_browser_bound() -> None:
    bridge = ExternalBrowserBridge()
    challenge = bridge.create_pairing_code(browser="chrome")

    with pytest.raises(PairingError) as mismatch:
        bridge.exchange_pairing_code(
            challenge.code, client={"browser": "edge"}
        )
    assert mismatch.value.code == "BROWSER_MISMATCH"

    # A failed exchange consumes the one-time code as a defensive default.
    with pytest.raises(PairingError) as reused:
        bridge.exchange_pairing_code(challenge.code, client={"browser": "chrome"})
    assert reused.value.code == "INVALID_PAIRING_CODE"

    second = bridge.create_pairing_code(browser="chrome")
    session = bridge.exchange_pairing_code(second.code, client={"browser": "chrome"})
    assert session.browser == "chrome"
    assert session.session_token not in repr(session)
    assert second.code not in repr(second)


def test_expired_pairing_code_is_rejected() -> None:
    clock = FakeClock()
    bridge = ExternalBrowserBridge(clock=clock, wall_clock=clock)
    challenge = bridge.create_pairing_code(ttl_seconds=10)
    clock.advance(11)
    with pytest.raises(PairingError) as expired:
        bridge.exchange_pairing_code(challenge.code, client={"browser": "chrome"})
    assert expired.value.code == "INVALID_PAIRING_CODE"


def test_tab_actions_require_an_explicit_extension_claim() -> None:
    bridge = ExternalBrowserBridge()
    session_id, token = paired(bridge)

    tabs = bridge.enqueue_command(session_id, "tabs", {})
    assert tabs.command == "tabs"
    with pytest.raises(TabNotClaimed) as unclaimed:
        bridge.enqueue_command(session_id, "snapshot", {"tab_id": 7})
    assert unclaimed.value.code == "TAB_NOT_CLAIMED"

    claim(bridge, token)
    snapshot = bridge.enqueue_command(session_id, "snapshot", {"tab_id": 7})
    assert snapshot.request_id
    assert bridge.session_state(session_id)["claimed_tab_ids"] == [7]


def test_command_round_trip_uses_opaque_ids_and_idempotent_results() -> None:
    bridge = ExternalBrowserBridge()
    session_id, token = paired(bridge)
    claim(bridge, token)
    ticket = bridge.enqueue_command(session_id, "snapshot", {"tab_id": 7})

    commands = bridge.poll_commands(token, wait_seconds=0)
    assert len(commands) == 1
    assert commands[0]["request_id"] == ticket.request_id
    assert commands[0]["request_id"] != session_id
    assert commands[0]["command"] == "snapshot"
    assert commands[0]["attempt"] == 1

    result = bridge.submit_result(
        token,
        ticket.request_id,
        ok=True,
        result={"snapshot_id": "snap-1", "snapshot": "[ref=e1] button \"Go\""},
    )
    assert result.ok
    assert bridge.wait_for_result(session_id, ticket.request_id, timeout_seconds=0) == result
    assert (
        bridge.submit_result(
            token,
            ticket.request_id,
            ok=True,
            result={"snapshot_id": "snap-1", "snapshot": "[ref=e1] button \"Go\""},
        )
        == result
    )


def test_long_poll_wakes_when_a_command_arrives() -> None:
    bridge = ExternalBrowserBridge(poll_timeout_seconds=2)
    session_id, token = paired(bridge)
    received: list[list[dict]] = []

    thread = threading.Thread(
        target=lambda: received.append(
            bridge.poll_commands(token, wait_seconds=1.5)
        )
    )
    thread.start()
    time.sleep(0.05)
    ticket = bridge.enqueue_command(session_id, "tabs", {})
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert received[0][0]["request_id"] == ticket.request_id


def test_expired_lease_is_redelivered_then_fails_boundedly() -> None:
    clock = FakeClock()
    bridge = ExternalBrowserBridge(
        clock=clock,
        wall_clock=clock,
        command_lease_seconds=5,
        max_delivery_attempts=2,
    )
    session_id, token = paired(bridge)
    ticket = bridge.enqueue_command(session_id, "tabs", {})

    assert bridge.poll_commands(token, wait_seconds=0)[0]["attempt"] == 1
    clock.advance(6)
    assert bridge.poll_commands(token, wait_seconds=0)[0]["attempt"] == 2
    clock.advance(6)
    bridge.cleanup_expired()

    result = bridge.get_result(session_id, ticket.request_id)
    assert result is not None
    assert not result.ok
    assert result.error["code"] == "EXTENSION_UNRESPONSIVE"


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("click", {"tab_id": 7, "snapshot_id": "s", "ref": "e1"}),
        ("fill", {"tab_id": 7, "snapshot_id": "s", "ref": "e1", "text": "private"}),
        ("keypress", {"tab_id": 7, "snapshot_id": "s", "ref": "e1", "key": "Enter"}),
        ("scroll", {"tab_id": 7, "delta_y": 100}),
    ],
)
def test_mutating_lease_is_never_redelivered_when_outcome_is_unknown(
    command: str, params: dict
) -> None:
    clock = FakeClock()
    bridge = ExternalBrowserBridge(
        clock=clock,
        wall_clock=clock,
        command_lease_seconds=5,
        max_delivery_attempts=3,
    )
    session_id, token = paired(bridge)
    claim(bridge, token)
    ticket = bridge.enqueue_command(session_id, command, params)

    leased = bridge.poll_commands(token, wait_seconds=0)
    assert leased[0]["attempt"] == 1
    clock.advance(6)
    assert bridge.poll_commands(token, wait_seconds=0) == []

    result = bridge.get_result(session_id, ticket.request_id)
    assert result is not None and not result.ok
    assert result.error["code"] == "BROWSER_ACTION_OUTCOME_UNKNOWN"


def test_caller_timeout_cancels_queued_ticket_before_extension_can_lease_it() -> None:
    bridge = ExternalBrowserBridge()
    session_id, token = paired(bridge)
    ticket = bridge.enqueue_command(session_id, "tabs", {})

    cancelled = bridge.cancel_command(session_id, ticket.request_id)

    assert cancelled.error["code"] == "BROWSER_COMMAND_CANCELLED"
    assert bridge.poll_commands(token, wait_seconds=0) == []


def test_caller_timeout_marks_leased_mutation_outcome_unknown() -> None:
    bridge = ExternalBrowserBridge()
    session_id, token = paired(bridge)
    claim(bridge, token)
    ticket = bridge.enqueue_command(
        session_id,
        "click",
        {"tab_id": 7, "snapshot_id": "s", "ref": "e1"},
    )
    assert bridge.poll_commands(token, wait_seconds=0)

    cancelled = bridge.cancel_command(session_id, ticket.request_id)

    assert cancelled.error["code"] == "BROWSER_ACTION_OUTCOME_UNKNOWN"
    assert bridge.poll_commands(token, wait_seconds=0) == []


def test_release_event_revokes_access_and_fails_pending_tab_commands() -> None:
    bridge = ExternalBrowserBridge()
    session_id, token = paired(bridge)
    claim(bridge, token)
    ticket = bridge.enqueue_command(session_id, "screenshot", {"tab_id": 7})

    bridge.publish_event(
        token, {"type": "debugger_detached", "tab_id": 7, "reason": "canceled_by_user"}
    )
    result = bridge.get_result(session_id, ticket.request_id)
    assert result is not None
    assert result.error["code"] == "TAB_RELEASED"
    with pytest.raises(TabNotClaimed):
        bridge.enqueue_command(session_id, "snapshot", {"tab_id": 7})


def test_disconnect_invalidates_token_and_completes_pending_work() -> None:
    bridge = ExternalBrowserBridge()
    session_id, token = paired(bridge)
    ticket = bridge.enqueue_command(session_id, "tabs", {})

    bridge.disconnect(token)
    state = bridge.session_state(session_id)
    assert not state["connected"]
    assert state["claimed_tab_ids"] == []
    assert bridge.get_result(session_id, ticket.request_id).error["code"] == "EXTENSION_DISCONNECTED"
    with pytest.raises(AuthenticationError):
        bridge.poll_commands(token, wait_seconds=0)


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("cdp", {"method": "Runtime.evaluate"}),
        ("click", {"tab_id": 1, "snapshot_id": "s", "ref": "e1", "script": "alert(1)"}),
        ("fill", {"tab_id": 1, "snapshot_id": "s", "ref": "e1", "text": object()}),
        ("scroll", {"tab_id": 1}),
        ("inspect", {"tab_id": 1, "snapshot_id": "s", "ref": "e1", "action": "browser_magic"}),
    ],
)
def test_unsafe_or_malformed_commands_are_rejected(command: str, params: dict) -> None:
    bridge = ExternalBrowserBridge()
    session_id, _token = paired(bridge)
    with pytest.raises(ProtocolValidationError):
        bridge.enqueue_command(session_id, command, params)
