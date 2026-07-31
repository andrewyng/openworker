"""Tests for Matrix target encoding — round-trip, invalid input, Slack regression."""

from __future__ import annotations

import pytest

from coworker.connectors.base import (
    SessionSource,
    decode_matrix_target,
    encode_matrix_target,
    format_target,
    parse_target,
)


def test_matrix_target_round_trip():
    room = "!abc:example.org"
    thread = "$event123:example.org"
    enc = encode_matrix_target(room, thread)
    assert enc.startswith("matrix/")
    assert "/thread/" in enc
    assert parse_target(enc) == ("matrix", room, thread)
    room_only = encode_matrix_target(room)
    assert parse_target(room_only) == ("matrix", room, None)
    assert decode_matrix_target(room_only) == (room, None)
    assert decode_matrix_target(enc) == (room, thread)


def test_matrix_target_invalid():
    for bad in ("matrix/", "matrix/not-b64!!!", "matrix/abc/thread/!!!"):
        with pytest.raises(ValueError):
            if bad == "matrix/":
                decode_matrix_target(bad)
            else:
                parse_target(bad)


def test_slack_telegram_regression():
    assert parse_target("telegram:12345") == ("telegram", "12345", None)
    assert parse_target("slack:C1:168.9") == ("slack", "C1", "168.9")
    assert format_target("slack", "C1", "168.9") == "slack:C1:168.9"


def test_session_source_matrix_target():
    s = SessionSource(
        platform="matrix",
        chat_id="!room:example.org",
        thread_id="$t1:example.org",
    )
    assert s.target == encode_matrix_target("!room:example.org", "$t1:example.org")
    assert s.target.startswith("matrix/")
