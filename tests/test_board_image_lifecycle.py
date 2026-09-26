"""Board-owned copies survive capture/worktree cleanup, with bounded scoped ingestion."""
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from coworker.roots import RootDir
from coworker.server.app import create_app
from coworker.teams import Actor, BoardError, Role
from coworker.teams.attachments import AttachmentStore, MAX_ATTACHMENT_BYTES, read_image_file, stored_name
from coworker.teams.tools import board_tools
from test_team_live_gaps import manager
from test_team_reader_recovery import create_item

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=")


def attach_tool(manager, roots):
    team = manager.teams.for_lead_session("lead")
    return next(t for t in board_tools(
        manager.team_store, space=team.space, actor=Actor(id="sam", role=Role.WORKER),
        attachments=manager.attachment_store, roots=lambda: roots,
    ) if t.__name__ == "attach_image")


def test_image_survives_source_deletion_and_actual_worktree_removal(manager, tmp_path):
    repo, checkout = tmp_path / "source-repo", tmp_path / "worker-checkout"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-c", "user.name=Rohit C Prasad", "-c", "user.email=rohit.prasad15@gmail.com",
                        "-c", "core.hooksPath=/dev/null", *args], cwd=repo, check=True, capture_output=True)
    git("init")
    git("commit", "--allow-empty", "-m", "Initialize synthetic attachment test")
    git("worktree", "add", "--detach", str(checkout), "HEAD")
    source = checkout / "proof.png"
    source.write_bytes(PNG)
    space, _, item = create_item(manager)
    result = attach_tool(manager, [RootDir(checkout)])(item["id"], "proof.png", "Criterion: invoice total is correct")
    assert result["stored"] is True
    ref = result["ref"]
    stored = stored_name(ref)
    assert manager.attachment_store.path_for(stored).parent != checkout
    source.unlink()
    git("worktree", "remove", str(checkout))
    assert not checkout.exists()
    # Re-open the blob store and retrieve through the board's authenticated API,
    # not a workspace file URL. The board remains on its original machine.
    manager.attachment_store = AttachmentStore(manager.attachment_store.root)
    client = TestClient(create_app(manager))
    response = client.get("/v1/sessions/lead/board/attachment", params={"name": stored})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG
    detail = manager.team_store.get_item(space, item["id"], actor=Actor(id="lead", role=Role.LEAD))
    assert detail["comments"][-1]["body"] == "Criterion: invoice total is correct"
    assert ref in detail["refs"]


def test_agent_attachment_tracks_live_grants_and_resolves_relative_paths(manager, tmp_path):
    _, _, item = create_item(manager)
    workspace, extra = tmp_path / "workspace", tmp_path / "extra"
    workspace.mkdir(); extra.mkdir()
    (workspace / "proof.png").write_bytes(PNG)
    (extra / "proof.png").write_bytes(PNG)
    roots = [RootDir(workspace)]
    attach = attach_tool(manager, roots)
    assert attach(item["id"], "proof.png")["stored"]
    assert "error" in attach(item["id"], str(extra / "proof.png"))
    roots.append(RootDir(extra))
    assert attach(item["id"], str(extra / "proof.png"))["stored"]
    roots.clear()
    assert "error" in attach(item["id"], str(workspace / "proof.png"))


def test_wrong_item_denied_before_reading_or_storing(manager, tmp_path, monkeypatch):
    _, _, item = create_item(manager, "maya")
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized attachment must not read a file")
    monkeypatch.setattr("coworker.teams.attachments.read_image_file", forbidden)
    result = attach_tool(manager, [RootDir(tmp_path)])(item["id"], "proof.png")
    assert "error" in result
    assert not manager.attachment_store.root.exists()


def test_escape_and_symlink_to_ungranted_file_are_denied(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "private.png"
    outside.write_bytes(PNG)
    (root / "escape.png").symlink_to(outside)
    for path in ("../private.png", "escape.png", str(outside)):
        with pytest.raises(BoardError, match="outside"):
            read_image_file(path, roots=[RootDir(root)])


def test_special_files_and_oversize_inputs_are_refused(tmp_path):
    source = tmp_path / "large.png"
    with source.open("wb") as file:
        file.truncate(MAX_ATTACHMENT_BYTES + 1)
    with pytest.raises(BoardError, match="10MB"):
        read_image_file(source, roots=[RootDir(tmp_path)])
    with pytest.raises((BoardError, OSError)):
        read_image_file(tmp_path, roots=[RootDir(tmp_path)])


def test_concurrent_identical_uploads_dedupe_without_shared_temp_race(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(pool.map(lambda i: store.put(PNG, f"shot-{i}.png"), range(32)))
    assert len({stored_name(ref) for ref in refs}) == 1
    assert store.path_for(stored_name(refs[0])).read_bytes() == PNG
    assert len(list(store.root.iterdir())) == 1


def test_corrupt_existing_copy_is_not_reported_as_success(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    ref = store.put(PNG, "proof.png")
    store.path_for(stored_name(ref)).write_bytes(b"corrupt")
    with pytest.raises(BoardError, match="integrity"):
        store.put(PNG, "proof.png")


def test_failed_copy_does_not_publish_board_evidence(manager, tmp_path, monkeypatch):
    space, _, item = create_item(manager)
    (tmp_path / "proof.png").write_bytes(PNG)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(manager.attachment_store, "put", fail)
    result = attach_tool(manager, [RootDir(tmp_path)])(item["id"], "proof.png")
    assert result == {"error": "disk full"}
    assert not manager.team_store.get_item(space, item["id"], actor=Actor(id="lead", role=Role.LEAD))["refs"]


def test_manager_agent_tool_uses_current_engine_roots(manager, tmp_path):
    engine = manager.get_engine("lead")
    tools = manager._team_tools_for("lead", type("Agent", (), {"team": "lead", "name": "swe-lead"})(),
                                    manager.session_store.load("lead"), manager.default_workspace)
    attach = next(t for t in tools if t.__name__ == "attach_image")
    _, _, item = create_item(manager)
    source = Path(manager.default_workspace) / "proof.png"
    source.write_bytes(PNG)
    assert attach(item["id"], str(source))["stored"]
    engine.roots.clear()
    assert "error" in attach(item["id"], str(source))


def test_failed_atomic_publish_leaves_no_success_or_staging_file(tmp_path, monkeypatch):
    store = AttachmentStore(tmp_path / "attachments")
    def fail(*args):
        raise OSError("publish failed")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="publish failed"):
        store.put(PNG, "proof.png")
    assert list(store.root.iterdir()) == []


def test_source_changes_do_not_change_attached_bytes(manager, tmp_path):
    _, _, item = create_item(manager)
    source = tmp_path / "proof.png"
    source.write_bytes(PNG)
    result = attach_tool(manager, [RootDir(tmp_path)])(item["id"], str(source))
    source.write_bytes(b"overwritten by a later capture")
    assert manager.attachment_store.path_for(stored_name(result["ref"])).read_bytes() == PNG


@pytest.mark.parametrize("item_id", [0, -1, True, "1"])
def test_attachment_rejects_invalid_item_ids_before_read(manager, tmp_path, item_id):
    result = attach_tool(manager, [RootDir(tmp_path)])(item_id, "not-present.png")
    assert "positive integer" in result["error"]


def test_wrong_item_on_http_upload_does_not_create_blob(manager):
    _, _, item = create_item(manager, "maya")
    token = manager.board_tokens.mint("sam", "worker")
    client = TestClient(create_app(manager))
    result = client.post("/v1/board/items/attach", headers={"Authorization": f"Bearer {token}"}, json={
        "space": manager.teams.for_lead_session("lead").space, "id": item["id"],
        "filename": "proof.png", "data_b64": base64.b64encode(PNG).decode(),
    })
    assert result.status_code in (400, 403)
    assert not manager.attachment_store.root.exists()
