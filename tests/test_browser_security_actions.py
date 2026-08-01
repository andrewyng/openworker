from __future__ import annotations

import math

import pytest

from coworker.browser_security.actions import (
    ApprovalExpired,
    ApprovalInvalid,
    ApprovalRequired,
    BrowserActionPolicy,
    BrowserActionPolicyError,
    BrowserActionRequest,
)


def _request(**overrides):
    values = {
        "session_id": "session-1",
        "tab_id": "tab-1",
        "snapshot_id": "snap-1",
        "ref": "e7",
        "origin": "https://example.com",
        "action": "browser_click",
        "arguments": {"button": "left"},
        "target": {"role": "button", "accessible_name": "Open details"},
    }
    values.update(overrides)
    return BrowserActionRequest.build(**values)


def test_read_and_navigation_actions_do_not_require_confirmation():
    policy = BrowserActionPolicy()
    for action in (
        "browser_open_url",
        "browser_snapshot",
        "browser_screenshot",
        "browser_hover",
        "browser_scroll",
    ):
        decision = policy.classify(_request(action=action))
        assert not decision.requires_confirmation


@pytest.mark.parametrize(
    "label",
    [
        "Send message",
        "Publish now",
        "Purchase",
        "Book trip",
        "Transfer funds",
        "Subscribe",
        "Accept terms",
        "Delete account",
        "Cancel subscription",
        "Change password",
        "Authorize OAuth access",
    ],
)
def test_consequential_controls_always_require_confirmation(label):
    decision = BrowserActionPolicy().classify(
        _request(target={"role": "button", "accessible_name": label})
    )
    assert decision.requires_confirmation
    assert "consequential_control" in decision.reasons


def test_form_submit_and_enter_in_form_require_confirmation():
    policy = BrowserActionPolicy()
    click = _request(
        target={
            "role": "button",
            "accessible_name": "Save",
            "inside_form": True,
            "submits_form": True,
        }
    )
    press = _request(
        action="browser_press",
        arguments={"key": "Enter"},
        target={"role": "textbox", "inside_form": True},
    )
    assert "form_submission" in policy.classify(click).reasons
    assert "form_submission" in policy.classify(press).reasons


def test_ambiguous_continue_requires_confirmation_unless_proven_safe():
    policy = BrowserActionPolicy()
    unsafe = _request(target={"role": "button", "accessible_name": "Continue"})
    safe = _request(
        target={
            "role": "button",
            "accessible_name": "Continue",
            "consequence_known_safe": True,
        }
    )
    assert "ambiguous_control" in policy.classify(unsafe).reasons
    assert not policy.classify(safe).requires_confirmation


@pytest.mark.parametrize(
    "classification",
    ["secret", "credential", "personal_data", "connector_data", "local_file_content"],
)
def test_sensitive_input_requires_confirmation(classification):
    request = _request(
        action="browser_fill",
        arguments={"text": "sensitive"},
        target={"role": "textbox"},
        data_classification=[classification],
    )
    assert BrowserActionPolicy().classify(request).requires_confirmation


def test_unknown_future_action_fails_toward_confirmation():
    decision = BrowserActionPolicy().classify(_request(action="browser_magic"))
    assert decision.requires_confirmation
    assert "unknown_browser_action" in decision.reasons


def test_approval_is_bound_to_every_state_and_argument_field_and_single_use():
    policy = BrowserActionPolicy(binding_key=b"b" * 32)
    original = _request(
        target={"role": "button", "accessible_name": "Send message"}
    )
    approval = policy.issue_approval(original)

    changed_requests = [
        _request(
            session_id="session-2",
            target={"role": "button", "accessible_name": "Send message"},
        ),
        _request(
            tab_id="tab-2",
            target={"role": "button", "accessible_name": "Send message"},
        ),
        _request(
            snapshot_id="snap-2",
            target={"role": "button", "accessible_name": "Send message"},
        ),
        _request(ref="e8", target={"role": "button", "accessible_name": "Send message"}),
        _request(
            origin="https://other.example",
            target={"role": "button", "accessible_name": "Send message"},
        ),
        _request(
            action="browser_press",
            arguments={"key": "Enter"},
            target={
                "role": "textbox",
                "accessible_name": "Send message",
                "inside_form": True,
            },
        ),
        _request(
            arguments={"button": "right"},
            target={"role": "button", "accessible_name": "Send message"},
        ),
        _request(
            target={"role": "button", "accessible_name": "Publish now"},
        ),
    ]
    for changed in changed_requests:
        with pytest.raises(ApprovalInvalid):
            policy.consume_approval(approval.token, changed)

    assert policy.consume_approval(approval.token, original) == approval
    with pytest.raises(ApprovalInvalid):
        policy.consume_approval(approval.token, original)


def test_approval_expiry_and_session_revocation():
    now = [100.0]
    policy = BrowserActionPolicy(clock=lambda: now[0], approval_ttl_seconds=10)
    request = _request(target={"role": "button", "accessible_name": "Delete account"})
    expired = policy.issue_approval(request)
    now[0] = 111.0
    with pytest.raises(ApprovalExpired):
        policy.consume_approval(expired.token, request)

    now[0] = 200.0
    approval = policy.issue_approval(request)
    assert policy.revoke_session("session-1") == 1
    with pytest.raises(ApprovalInvalid):
        policy.consume_approval(approval.token, request)


def test_safe_action_cannot_mint_spurious_approval():
    policy = BrowserActionPolicy()
    with pytest.raises(ApprovalRequired):
        policy.issue_approval(_request(action="browser_snapshot"))


def test_noncanonical_origin_and_non_json_arguments_are_rejected():
    policy = BrowserActionPolicy()
    direct = BrowserActionRequest(
        session_id="s",
        tab_id="t",
        snapshot_id="n",
        ref="e1",
        origin="https://EXAMPLE.com:443",
        action="browser_click",
        arguments={},
    )
    with pytest.raises(BrowserActionPolicyError):
        policy.classify(direct)
    with pytest.raises(BrowserActionPolicyError):
        policy.classify(_request(arguments={"x": math.nan}))
