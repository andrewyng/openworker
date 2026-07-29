"""Production lifecycle coverage for retained tool outputs."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from coworker.agent import build_engine
from coworker.agents import chat_agent
from coworker.automation import Schedule, ScheduledTask
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord
from coworker.tool_outputs import (
    SessionToolOutputStore,
    ToolOutputStoreError,
    session_output_key,
)


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _record(session_id: str, workspace: Path) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        workspace=str(workspace),
        model="m",
        mode="interactive",
        agent="cowork",
    )


def test_build_engine_wires_explicit_store_and_reserves_retrieval_tool(tmp_path):
    store = SessionToolOutputStore(tmp_path, "explicit")
    engine = build_engine(
        agent=chat_agent(),
        provider=_Provider(),
        tool_output_store=store,
    )

    assert engine.tool_output_store is store
    assert engine.registry.names()[-1] == "read_tool_output"

    def read_tool_output():
        return "shadowed"

    with pytest.raises(ValueError, match="reserved or already registered"):
        build_engine(
            agent=chat_agent(),
            provider=_Provider(),
            extra_tools=[read_tool_output],
            tool_output_store=store,
        )


def test_build_engine_direct_callers_get_durable_or_ephemeral_store(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("coworker.agent.state_dir", lambda: tmp_path / "state")

    durable = build_engine(
        agent=chat_agent(),
        provider=_Provider(),
        session_id="durable",
    )
    assert durable.tool_output_store.directory == (
        tmp_path / "state" / "tool-outputs" / session_output_key("durable")
    )
    assert durable._ephemeral_output_dir is None

    ephemeral = build_engine(agent=chat_agent(), provider=_Provider())
    ephemeral_root = Path(ephemeral._ephemeral_output_dir.name)
    assert ephemeral_root.is_dir()
    assert ephemeral.tool_output_store.directory.is_relative_to(ephemeral_root)
    ephemeral._ephemeral_output_dir.cleanup()
    assert not ephemeral_root.exists()


def test_manager_uses_session_store_for_live_and_scheduled_engines(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())

    live = manager.get_engine("live", workspace=str(workspace), agent="cowork")
    assert live is not None
    assert live.tool_output_store.directory == (
        manager._data_base / "tool-outputs" / session_output_key("live")
    )

    task = ScheduledTask(
        title="Daily brief",
        instructions="Write a brief",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(workspace),
        agent="cowork",
    )
    scheduled = manager._build_task_engine(task, session_id="__run__scheduled")
    assert scheduled.tool_output_store.directory == (
        manager._data_base / "tool-outputs" / session_output_key("__run__scheduled")
    )


def test_delete_session_removes_retained_outputs_even_without_live_engine(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("delete-me", tmp_path))
    store = manager.tool_output_store("delete-me")
    store.put("call", "tool", "retained")

    assert manager.delete_session("delete-me")["ok"] is True
    assert not store.directory.exists()


def test_delete_running_session_blocks_stale_save_and_defers_output_cleanup(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    engine = manager.get_engine("running", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("running", engine)
    store = manager.tool_output_store("running")
    store.put("call", "tool", "retained")
    manager.mark_running("running")

    assert manager.delete_session("running")["ok"] is True
    assert store.directory.is_dir()

    engine.messages.append({"role": "assistant", "content": "stale completion"})
    manager.save("running", engine)
    assert manager.session_store.exists("running") is False
    assert (
        manager.get_engine("running", workspace=str(tmp_path), agent="cowork") is None
    )

    manager.mark_idle("running")
    assert not store.directory.exists()
    manager.save("running", engine)
    assert manager.session_store.exists("running") is False


def test_delete_between_durable_resume_lookup_and_claim_blocks_resume(
    tmp_path, monkeypatch
):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    session_id = "durable-resume-race"
    manager.session_store.save(_record(session_id, tmp_path))

    class ResumableEngine:
        resumed = False

        async def resume(self):
            self.resumed = True
            yield None

    engine = ResumableEngine()
    manager._engines[session_id] = engine
    original_get_engine = manager.get_engine

    def get_engine_then_delete(requested_session_id):
        stale_engine = original_get_engine(requested_session_id)
        assert manager.delete_session(requested_session_id)["ok"] is True
        return stale_engine

    monkeypatch.setattr(manager, "get_engine", get_engine_then_delete)
    item = SimpleNamespace(session_id=session_id, tool_call_id="call")

    asyncio.run(manager._durable_resume(item))

    assert engine.resumed is False
    assert manager.is_running(session_id) is False
    assert manager.session_store.exists(session_id) is False


def test_delete_and_inflight_save_are_one_lifecycle_transaction(tmp_path, monkeypatch):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    engine = manager.get_engine("racing", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("racing", engine)

    save_entered = threading.Event()
    release_save = threading.Event()
    delete_started = threading.Event()
    original_save = manager.session_store.save

    def blocked_save(record):
        save_entered.set()
        assert release_save.wait(timeout=5)
        original_save(record)

    monkeypatch.setattr(manager.session_store, "save", blocked_save)
    save_thread = threading.Thread(target=manager.save, args=("racing", engine))

    def delete() -> None:
        delete_started.set()
        manager.delete_session("racing")

    delete_thread = threading.Thread(target=delete)
    save_thread.start()
    assert save_entered.wait(timeout=5)
    delete_thread.start()
    assert delete_started.wait(timeout=5)
    release_save.set()
    save_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert not save_thread.is_alive()
    assert not delete_thread.is_alive()
    assert manager.session_store.exists("racing") is False


def test_deleted_session_cannot_be_resurrected_by_root_mutation(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("deleted-root", tmp_path))
    manager.delete_session("deleted-root")

    result = manager.add_root("deleted-root", str(tmp_path), writable=True)

    assert result["ok"] is False
    assert manager.get_roots("deleted-root") == []
    assert manager.session_store.exists("deleted-root") is False


def test_deleting_unknown_ids_does_not_accumulate_tombstones(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())

    for index in range(100):
        assert manager.delete_session(f"missing-{index}")["ok"] is False

    assert manager._deleted_sessions == set()


def test_manager_reuses_session_output_store_for_paged_integrity_cache(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    first = manager.tool_output_store("same")
    reopened = manager.tool_output_store("same", create=False)

    assert reopened is first

    first.delete_all()
    recreated = manager.tool_output_store("same")
    assert recreated is not first
    assert recreated.put("call", "tool", "works").ref


def test_manager_creates_only_one_store_for_concurrent_callers(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    barrier = threading.Barrier(16)
    stores = []

    def open_store() -> None:
        barrier.wait(timeout=5)
        stores.append(manager.tool_output_store("shared"))

    threads = [threading.Thread(target=open_store) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len({id(store) for store in stores}) == 1


def test_manager_never_reuses_cached_store_through_replaced_symlink(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    store = manager.tool_output_store("replaced")
    store.delete_all()
    outside = tmp_path / "outside"
    outside.mkdir()
    store.directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolOutputStoreError, match="unsafe"):
        manager.tool_output_store("replaced")


def test_session_delete_tolerates_unsafe_orphan_store_without_following_it(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("unsafe-delete", tmp_path))
    store = manager.tool_output_store("unsafe-delete")
    store.delete_all()
    manager._tool_output_stores.pop("unsafe-delete")
    outside = tmp_path / "outside-delete"
    outside.mkdir()
    store.directory.symlink_to(outside, target_is_directory=True)

    result = manager.delete_session("unsafe-delete")

    assert result["ok"] is True
    assert outside.is_dir()


def test_orphan_gc_preserves_known_and_live_sessions(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("known", tmp_path))
    manager.tool_output_store("known").put("c", "t", "keep-known")

    live = manager.tool_output_store("live")
    live.put("c", "t", "keep-live")
    manager._engines["live"] = object()

    orphan = manager.tool_output_store("orphan")
    orphan.put("c", "t", "drop")
    unrelated = manager._data_base / "tool-outputs" / ("g" * 64)
    unrelated.mkdir()

    old = time.time() - 48 * 60 * 60
    for path in (live.directory, orphan.directory, unrelated):
        os.utime(path, (old, old))

    assert manager.collect_tool_output_orphans(grace_seconds=24 * 60 * 60) == 1
    assert not orphan.directory.exists()
    assert live.directory.is_dir()
    assert manager.tool_output_store("known", create=False).directory.is_dir()
    assert unrelated.is_dir()


def test_global_retention_evicts_oldest_inactive_store_to_cap(tmp_path):
    from coworker.tool_outputs import (
        ToolOutputPolicy,
        collect_retained_output_stores,
    )

    active = SessionToolOutputStore(tmp_path, "active-oldest")
    active.put("c", "t", "a" * 100)
    evict = SessionToolOutputStore(tmp_path, "evict-me")
    evict.put("c", "t", "b" * 100)
    newest = SessionToolOutputStore(tmp_path, "keep-newest")
    newest.put("c", "t", "c" * 100)
    now = time.time()
    os.utime(active.directory, (now - 300, now - 300))
    os.utime(evict.directory, (now - 200, now - 200))
    os.utime(newest.directory, (now - 100, now - 100))

    result = collect_retained_output_stores(
        tmp_path,
        known_session_ids={"active-oldest", "evict-me", "keep-newest"},
        active_session_ids={"active-oldest"},
        orphan_grace_seconds=24 * 60 * 60,
        policy=ToolOutputPolicy(
            max_global_output_bytes=900,
            max_retention_age_seconds=10_000,
            min_disk_headroom_bytes=0,
        ),
        now=now,
    )

    assert result.removed_sessions == 1
    assert active.directory.is_dir()
    assert not evict.directory.exists()
    assert newest.directory.is_dir()
    assert result.remaining_bytes <= 900


def test_manager_retention_expires_old_known_store_but_preserves_live(tmp_path):
    from coworker.tool_outputs import ToolOutputPolicy

    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("known-old", tmp_path))
    known = manager.tool_output_store("known-old")
    known.put("c", "t", "expire")
    live = manager.tool_output_store("live-old")
    live.put("c", "t", "preserve")
    manager._engines["live-old"] = object()
    now = time.time()
    for directory in (known.directory, live.directory):
        os.utime(directory, (now - 200, now - 200))

    removed = manager.collect_tool_output_orphans(
        grace_seconds=24 * 60 * 60,
        policy=ToolOutputPolicy(
            max_global_output_bytes=10_000,
            max_retention_age_seconds=100,
            min_disk_headroom_bytes=0,
        ),
        now=now,
    )

    assert removed == 1
    assert not known.directory.exists()
    assert live.directory.is_dir()


def test_global_retention_never_follows_symlinked_store_paths(tmp_path):
    from coworker.tool_outputs import (
        ToolOutputPolicy,
        collect_retained_output_stores,
    )

    outside = tmp_path / "outside"
    outside_store = outside / ("a" * 64)
    outside_store.mkdir(parents=True)
    sentinel = outside_store / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    policy = ToolOutputPolicy(
        max_global_output_bytes=0,
        max_retention_age_seconds=0,
        min_disk_headroom_bytes=0,
    )

    linked_root = tmp_path / "linked-root"
    linked_root.mkdir()
    (linked_root / "tool-outputs").symlink_to(outside, target_is_directory=True)
    collect_retained_output_stores(linked_root, policy=policy)

    linked_session = tmp_path / "linked-session"
    output_root = linked_session / "tool-outputs"
    output_root.mkdir(parents=True)
    session_link = output_root / ("b" * 64)
    session_link.symlink_to(outside_store, target_is_directory=True)
    collect_retained_output_stores(linked_session, policy=policy)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert session_link.is_symlink()


def test_tool_output_route_is_bounded_paged_and_session_scoped(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("one", tmp_path))
    record = manager.tool_output_store("one").put(
        "call", "tool", "hello durable output"
    )
    manager.session_store.load = Mock(
        side_effect=AssertionError("paging must not parse the transcript")
    )
    client = TestClient(create_app(manager))

    page = client.get(
        f"/v1/sessions/one/tool-outputs/{record.ref}", params={"limit_bytes": 5}
    )
    assert page.status_code == 200
    assert page.json()["content"] == "hello"
    assert page.json()["complete"] is False
    assert client.get(f"/v1/sessions/two/tool-outputs/{record.ref}").status_code == 404
    assert (
        client.get("/v1/sessions/one/tool-outputs/not-a-reference").status_code == 400
    )
    assert (
        client.get(
            f"/v1/sessions/one/tool-outputs/{record.ref}",
            params={"limit_bytes": 8_001},
        ).status_code
        == 400
    )


def test_tool_output_route_maps_missing_and_corrupt_records(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    manager.session_store.save(_record("one", tmp_path))
    store = manager.tool_output_store("one")
    record = store.put("call", "tool", "hello")
    client = TestClient(create_app(manager))

    assert (
        client.get("/v1/sessions/one/tool-outputs/out_" + ("a" * 32)).status_code == 404
    )
    (store.directory / f"{record.ref}.json").write_text("{", encoding="utf-8")
    assert client.get(f"/v1/sessions/one/tool-outputs/{record.ref}").status_code == 409


def test_app_runs_orphan_gc_at_startup(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    original = manager.collect_tool_output_orphans
    manager.collect_tool_output_orphans = Mock(wraps=original)

    with TestClient(create_app(manager)):
        pass

    manager.collect_tool_output_orphans.assert_called_once_with()
