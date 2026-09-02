"""Matrix mention routing and session key tests."""

from __future__ import annotations

from coworker.connectors.base import SessionSource, parse_target
from coworker.connectors.matrix_routing import (
    dm_mention_routes_to_thread,
    effective_session_scope,
    mention_thread_target,
)
from coworker.connectors.matrix_settings import MatrixSettings


def _settings(**kwargs) -> MatrixSettings:
    base = {
        "homeserver_url": "https://h",
        "access_token": "t",
    }
    base.update(kwargs)
    return MatrixSettings.from_profile(base)


def test_effective_session_scope_auto_is_thread():
    assert effective_session_scope(_settings(session_scope="auto")) == "thread"


def test_mention_thread_target_room_scope_per_user():
    src = SessionSource(
        platform="matrix",
        chat_id="!ops:example.org",
        user_id="@alice:example.org",
        thread_id="$t1",
    )
    target = mention_thread_target(
        _settings(session_scope="room", group_sessions_per_user=True),
        src,
        "$m1",
    )
    platform, room, thread = parse_target(target)
    assert platform == "matrix"
    assert room == "!ops:example.org"
    assert thread and thread.startswith("@user:")


def test_mention_thread_target_thread_scope_isolates_users():
    src_alice = SessionSource(
        platform="matrix",
        chat_id="!ops:example.org",
        user_id="@alice:example.org",
        thread_id="$t1",
    )
    src_bob = SessionSource(
        platform="matrix",
        chat_id="!ops:example.org",
        user_id="@bob:example.org",
        thread_id="$t1",
    )
    settings = _settings(session_scope="thread", group_sessions_per_user=True)
    assert mention_thread_target(settings, src_alice, "$m1") != mention_thread_target(
        settings, src_bob, "$m1"
    )


def test_dm_mention_routes_when_enabled():
    s = _settings(dm_mention_threads=True)
    assert dm_mention_routes_to_thread(s, mentions_me=True, is_dm=True)
    assert not dm_mention_routes_to_thread(s, mentions_me=False, is_dm=True)
    assert not dm_mention_routes_to_thread(s, mentions_me=True, is_dm=False)
