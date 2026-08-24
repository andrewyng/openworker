"""UX-029 — temporary workspaces for code-family sessions.

"Start in a temporary folder": the dir is created at SEND time via POST /v1/workspaces/temp
(git-init'd for code work), flagged in the `ready` event as `temp_workspace`, and can later be
moved to a real location via "Save as project…" (POST /v1/sessions/{id}/save-as-project).
"""

from pathlib import Path

from fastapi.testclient import TestClient

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _mgr(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr._prefs["scratch_base"] = str(tmp_path / "scratch")
    return mgr


def test_provision_temp_workspace_creates_dir_with_git(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    client = TestClient(create_app(mgr))

    res = client.post("/v1/workspaces/temp", json={"session_id": "abc123", "git": True}).json()
    assert res["ok"] is True
    d = Path(res["path"])
    assert d.is_dir()
    assert d.parent == (tmp_path / "scratch").resolve()
    assert res["git"] is True and (d / ".git").is_dir()

    # Idempotent — a re-send against the existing dir is a no-op.
    again = client.post("/v1/workspaces/temp", json={"session_id": "abc123"}).json()
    assert again["ok"] is True and again["path"] == res["path"]

    # It IS a temp workspace, and never appears in the recents (project) list.
    assert mgr.is_temp_workspace(res["path"]) is True
    mgr.session_store.touch_workspace(res["path"])
    assert res["path"] not in [w["path"] for w in mgr.recent_workspaces()]


def test_provision_temp_workspace_rejects_bad_ids(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    client = TestClient(create_app(mgr))
    for bad in ["", "../evil", "a/b", ".."]:
        assert client.post("/v1/workspaces/temp", json={"session_id": bad}).json()["ok"] is False


def test_save_temp_as_project_moves_and_rebinds(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    client = TestClient(create_app(mgr))

    src = Path(client.post("/v1/workspaces/temp", json={"session_id": "sess1"}).json()["path"])
    (src / "notes.txt").write_text("hello", encoding="utf-8")
    mgr.session_store.save(
        SessionRecord(
            session_id="sess1",
            workspace=str(src),
            model="m",
            mode="interactive",
            messages=[{"role": "user", "content": "hi"}],
            agent="code",
        )
    )

    dest = tmp_path / "projects" / "myproj"
    res = client.post("/v1/sessions/sess1/save-as-project", json={"path": str(dest)}).json()
    assert res["ok"] is True
    moved = Path(res["path"])
    assert moved == dest.resolve()
    assert (moved / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert not src.exists()
    assert mgr.session_store.load("sess1").workspace == str(moved)
    assert mgr.is_temp_workspace(str(moved)) is False


def test_save_temp_as_project_guards(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    client = TestClient(create_app(mgr))

    # Not a temp workspace → refused.
    real = tmp_path / "realproj"
    real.mkdir()
    mgr.session_store.save(
        SessionRecord(session_id="s2", workspace=str(real), model="m", mode="interactive", agent="code")
    )
    res = client.post("/v1/sessions/s2/save-as-project", json={"path": str(tmp_path / "x")}).json()
    assert res["ok"] is False

    # Non-empty destination → refused, source untouched.
    src = Path(client.post("/v1/workspaces/temp", json={"session_id": "s3"}).json()["path"])
    mgr.session_store.save(
        SessionRecord(session_id="s3", workspace=str(src), model="m", mode="interactive", agent="code")
    )
    full = tmp_path / "full"
    full.mkdir()
    (full / "occupied.txt").write_text("x", encoding="utf-8")
    res = client.post("/v1/sessions/s3/save-as-project", json={"path": str(full)}).json()
    assert res["ok"] is False and src.is_dir()
