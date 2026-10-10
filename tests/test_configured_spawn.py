"""Configurations on the box (connectors-across-machines spec §10.4): a spawn spec
starts a session (coworker, model, checkout, skills, instructions); a reply-only
frame posts one line; a configured event to an existing session carries its own
framing; titles are unique per machine; the picker learns which rows are leads."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from coworker.connectors.base import MessageEvent, SessionSource
from coworker.server.manager import SessionManager, configured_opening
from tests.test_subscriptions import ScriptedProvider


def _github_event(*, kind="pr_open", number="42", configuration=None, reply_only=None, known_names=(), name="", sender="octocat", repo="acme/site"):
    frame = {
        "provider": "github", "installation_id": "101", "owner_repo": repo, "number": number,
        "kind": kind, "name": name, "event": "pull_request", "sender": sender,
        "title": "Add retry loop", "body": "Retries the worker fetch.",
        "url": f"https://github.com/{repo}/pull/{number}", "head_ref": "feat/retry", "base_ref": "main",
    }
    ev = MessageEvent(
        text=f"[PR opened in {repo}#{number}: Add retry loop] Retries the worker fetch.",
        source=SessionSource(platform="github", chat_id=f"{repo}#{number}", user_id=sender, user_name=sender, chat_name=f"{repo}#{number}", chat_type="channel", team_id="101"),
        raw=frame,
        mentions_me=True,
        configuration=configuration,
        reply_only=reply_only,
        known_names=list(known_names),
    )
    return ev


def _connect_github(mgr):
    mgr.secrets.put("github:default", {"token": "ghp_test", "enabled": True})


class FakeGateway:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def deliver(self, target, text):
        self.sent.append((target, text))
        return type("R", (), {"ok": True})()


def _mgr(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    _connect_github(mgr)
    return mgr


def test_reply_only_posts_one_line_and_starts_nothing(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.gateway = FakeGateway()  # must NOT be used for GitHub (its send needs a desktop session)
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "coworker.connectors.github_send.send_github_comment",
        lambda secrets, chat_id, text: posted.append((chat_id, text)) or {"ok": True},
    )
    delivered: list = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append(session_id)

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    before = {r["session_id"] for r in mgr.list_sessions()}
    ev = _github_event(kind="mention", name="reviwer", reply_only="unknown_name", known_names=["reviewer", "sec-lead"])
    asyncio.run(mgr._dispatch_inbound(ev))
    assert delivered == []
    assert {r["session_id"] for r in mgr.list_sessions()} == before
    assert mgr.gateway.sent == []
    (chat_id, text) = posted[0]
    assert chat_id == "acme/site#42"
    assert "`reviwer`" in text and "`reviewer`" in text and "`sec-lead`" in text


def test_spawn_starts_a_session_from_the_spec(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    delivered: list[tuple[str, str]] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append((session_id, message))

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    cfg = {
        "config_id": "cfg1", "event": "pr_open", "name": "",
        "spawn": {"kind": "new", "machine_id": "m-a", "persona": "cowork", "models": ["ollama:qwen3-coder:30b"],
                  "base_dir": "", "worktree": True, "skills": ["semgrep-review"], "instructions": "Correctness first, then security."},
    }
    asyncio.run(mgr._dispatch_inbound(_github_event(configuration=cfg)))
    assert len(delivered) == 1
    sid, opening = delivered[0]
    assert "A pull request was opened on GitHub in acme/site#42: Add retry loop (by octocat)." in opening
    # The origin block names the exact reply call (spec §11.3); the opaque handle is gone.
    assert "From GitHub · acme/site · pull request #42" in opening
    assert 'github_reply(owner="acme", repo="site", number=42, body=…) — pre-approved for this thread; github_review(owner="acme", repo="site", pull_number=42' in opening
    assert "github:acme/site#42" not in opening and "data to review" in opening
    row = next(r for r in mgr.list_sessions() if r["session_id"] == sid)
    assert row["agent"] == "cowork" and row["origin"] == "github" and row["title"].startswith("PR #42")
    assert row["model"] == "ollama:qwen3-coder:30b"  # the spec's first entry (cowork has no list)
    rec = mgr.session_store.load(sid)
    assert rec.spawn["config_id"] == "cfg1" and rec.spawn["instructions"] == "Correctness first, then security."
    # Spec §11.5 defaults: auto-approve + send approvals to the Inbox; the reviewer may
    # judge this session's calls with nobody attending (the human opted in).
    assert rec.mode == "auto-approve" and rec.spawn["approval_mode"] == "auto-approve"
    assert mgr.unattended.is_unattended(sid) is True and mgr.reviewer_opted(sid) is True
    assert mgr.get_engine(sid).is_attended() is True
    assert rec.spawn["workspace_setup"] == "agent"
    assert Path(rec.workspace).is_dir()
    # Session-only: the instructions join the standing rules for this session, no other.
    assert "For this session: Correctness first" in mgr._user_rules_for(sid)
    other = mgr.get_engine("plain", agent="cowork")
    mgr.save("plain", other)
    assert "Correctness first" not in mgr._user_rules_for("plain")
    # Skills listed in the spec are enabled for the session; the thread is owned.
    assert mgr.session_skills.get(sid) == {"semgrep-review": True}
    assert mgr.mention_sessions.get("github:acme/site#42") == sid
    # The thread grant covers replying and reviewing on THIS pull request, and it
    # survives a rebuild (re-derived from the thread map, not from the live engine).
    for tool in ("send_message", "github_reply", "github_review"):
        assert mgr.get_engine(sid).permissions.task_rules[tool] == {"github:acme/site#42"}, tool
    mgr._engines.pop(sid)
    assert mgr.get_engine(sid).permissions.task_rules["github_review"] == {"github:acme/site#42"}
    # A per-turn save never clears the spawn record.
    mgr.save(sid, mgr.get_engine(sid))
    assert mgr.session_store.load(sid).spawn["config_id"] == "cfg1"


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _local_origin(tmp_path) -> Path:
    """A bare acme/site.git with main and a PR #42 head, served over file://."""
    origin = tmp_path / "origin" / "acme" / "site.git"
    origin.parent.mkdir(parents=True)
    _git("init", "--bare", "-b", "main", str(origin))
    src = tmp_path / "seed"
    _git("clone", "-q", str(origin), str(src))
    _git("-C", str(src), "config", "user.email", "t@t")
    _git("-C", str(src), "config", "user.name", "t")
    (src / "README.md").write_text("main\n")
    _git("-C", str(src), "add", ".")
    _git("-C", str(src), "commit", "-qm", "main")
    _git("-C", str(src), "push", "-q", "origin", "main")
    _git("-C", str(src), "checkout", "-qb", "feat/retry")
    (src / "worker.ts").write_text("retry()\n")
    _git("-C", str(src), "add", ".")
    _git("-C", str(src), "commit", "-qm", "pr")
    _git("-C", str(src), "push", "-q", "origin", "feat/retry:refs/pull/42/head")
    return tmp_path / "origin"


def test_spawn_provisions_only_a_directory_and_preserves_it(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENWORKER_BASE_DIR", raising=False)
    mgr = _mgr(tmp_path, monkeypatch)
    delivered = []

    async def deliver(sid, message, *, source=None):
        delivered.append((sid, message))

    monkeypatch.setattr(mgr, "deliver_to_session", deliver)
    monkeypatch.setattr("coworker.connectors.integration_tools._run_git",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("harness must not run git")))
    base = tmp_path / "work"
    cfg = {"config_id": "cfg2", "event": "pr_open",
           "spawn": {"kind": "new", "persona": "swe-lead", "base_dir": str(base), "worktree": True}}
    asyncio.run(mgr._dispatch_inbound(_github_event(configuration=cfg)))
    sid, opening = delivered[0]
    rec = mgr.session_store.load(sid)
    directory = base / sid
    assert Path(rec.workspace) == directory.resolve()
    assert not list(directory.iterdir())
    assert rec.spawn["workspace_setup"] == "agent"
    assert "No repository or worktree was created" in opening
    assert "refs/pull/42/head" in opening
    (directory / "keep.txt").write_text("unfinished work")
    mgr.set_session_flags(sid, archived=True)
    mgr.delete_session(sid)
    assert (directory / "keep.txt").read_text() == "unfinished work"


def test_session_directory_outside_the_base_dir_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENWORKER_BASE_DIR", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    mgr = _mgr(tmp_path, monkeypatch)
    recorded = []
    monkeypatch.setattr(mgr.unrouted, "record", lambda *a, **k: recorded.append(k))
    cfg = {"event": "pr_open", "spawn": {"persona": "cowork", "base_dir": str(tmp_path / "elsewhere")}}
    asyncio.run(mgr._dispatch_inbound(_github_event(configuration=cfg)))
    assert recorded and "session directory failed" in recorded[0]["reason"]


def test_configured_event_to_an_existing_session_carries_its_framing(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    delivered: list[tuple[str, str]] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append((session_id, message))

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    mgr.save("sec-lead", mgr.get_engine("sec-lead", agent="cowork"))
    ev = _github_event(kind="pr_merge", configuration={"config_id": "c", "event": "pr_merge", "name": ""})
    ev.target_session_id = "sec-lead"
    asyncio.run(mgr._dispatch_inbound(ev))
    assert [s for s, _ in delivered] == ["sec-lead"]
    assert "A pull request was merged on GitHub in acme/site#42" in delivered[0][1]
    # The promised pre-approval is real: the thread is granted to the existing session —
    # replying AND reviewing on that one pull request, nothing wider.
    rules = mgr.get_engine("sec-lead").permissions.task_rules
    for tool in ("send_message", "github_reply", "github_review"):
        assert rules.get(tool) == {"github:acme/site#42"}, tool


def test_titles_are_unique_per_machine_and_rows_flag_leads(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.save("a", mgr.get_engine("a", agent="cowork"))
    mgr.save("b", mgr.get_engine("b", agent="cowork"))
    assert mgr.rename_session("a", "Reviewer")["ok"] is True
    dup = mgr.rename_session("b", "Reviewer")
    assert dup["ok"] is False and "already named" in dup["error"]
    assert mgr.rename_session("a", "Reviewer")["ok"] is True  # renaming to its own title is fine
    rows = {r["session_id"]: r for r in mgr.list_sessions()}
    assert rows["a"]["lead"] is False
    mgr.session_store.save(type(mgr.session_store.load("a"))(session_id="lead", workspace=str(tmp_path), model="m", mode="interactive", agent="swe-lead"))
    assert {r["session_id"]: r for r in mgr.list_sessions()}["lead"]["lead"] is True


def test_configured_opening_reads_plainly():
    ev = _github_event(kind="issue_open", number="9", configuration={"config_id": "c", "event": "issue_open"})
    text = configured_opening(ev, "github:acme/site#9", "")
    assert text.startswith("An issue was opened on GitHub in acme/site#9: Add retry loop (by octocat).")
    assert "Your session folder" not in text


def test_named_mention_on_a_pr_describes_the_ref_without_cloning(tmp_path, monkeypatch):
    origin = _local_origin(tmp_path)
    monkeypatch.setenv("GITHUB_GIT_URL", f"file://{origin}")
    monkeypatch.delenv("OPENWORKER_BASE_DIR", raising=False)
    mgr = _mgr(tmp_path, monkeypatch)

    async def fake_deliver(session_id, message, *, source=None):
        pass

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    base = tmp_path / "work"
    cfg = {"config_id": "cfg5", "event": "named_mention", "name": "reviewer", "spawn": {"kind": "new", "persona": "cowork", "base_dir": str(base), "worktree": True}}
    ev = _github_event(kind="mention", name="reviewer", configuration=cfg)
    ev.raw["is_pr"] = True
    asyncio.run(mgr._dispatch_inbound(ev))
    rec = next(mgr.session_store.load(r["session_id"]) for r in mgr.list_sessions() if r["origin"] == "github")
    assert rec.spawn["workspace_setup"] == "agent"
    assert not (base / "site").exists()
    assert "refs/pull/42/head" in configured_opening(ev, "", rec.workspace)
    assert rec.title.startswith("PR mention #42")


def test_configured_event_to_a_gated_session_is_parked_not_swallowed(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    delivered: list = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append(session_id)

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    monkeypatch.setattr(mgr, "_inbound_connector_allowed", lambda sid, platform: False)
    recorded: list = []
    monkeypatch.setattr(mgr.unrouted, "record", lambda *a, **k: recorded.append(k.get("reason", "")))
    mgr.save("lead", mgr.get_engine("lead", agent="cowork"))
    ev = _github_event(kind="pr_merge", configuration={"config_id": "c", "event": "pr_merge", "name": ""})
    ev.target_session_id = "lead"
    asyncio.run(mgr._dispatch_inbound(ev))
    assert delivered == [] and recorded and "off for the target session" in recorded[0]


def test_swe_lead_is_reachable_on_github_and_slack():
    from pathlib import Path

    from coworker.personas.manifest import parse_manifest

    m = parse_manifest((Path(__file__).resolve().parents[1] / "coworker/personas/builtin/swe-lead/manifest.md").read_text())
    assert set(m.connectors) == {"github", "slack"}


def test_missing_target_is_parked_without_a_replacement_lead(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    parked = []
    monkeypatch.setattr(mgr.unrouted, "record", lambda *a, **k: parked.append(k))
    async def no_spawn(*args):
        raise AssertionError("targeted events must not fall through to mention spawning")
    monkeypatch.setattr(mgr, "_route_mention", no_spawn)
    ev = _github_event(configuration={"config_id": "cfg", "event": "issue_open"})
    ev.target_session_id = "missing-lead"
    before = mgr.list_sessions()
    asyncio.run(mgr._dispatch_inbound(ev))
    assert mgr.list_sessions() == before
    assert "target session missing-lead is missing" in parked[0]["reason"]


def test_worker_scratch_is_shared_with_lead_and_survives_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_SCRATCH_BASE", str(tmp_path / "scratch"))
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.save("lead", mgr.get_engine("lead", agent="swe-lead"))
    out = mgr.create_team("lead", [{"persona": "swe-worker", "name": "sam"}])
    worker = out["workers"][0]
    sid = worker["session_id"]
    scratch = Path(worker["scratch_directory"])
    assert scratch == tmp_path / "scratch" / "lead" / "workers" / sid
    marker = scratch / "evidence.txt"
    marker.write_text("verified")
    lead = mgr.get_engine("lead")
    engine = mgr.get_engine(sid)
    assert "verified" in str(lead.registry.get("read_file").func(str(marker)))
    assert "verified" in str(engine.registry.get("read_file").func(str(marker)))
    mgr._engines.pop(sid)
    assert mgr._provision_scratch(sid) == str(scratch)
    assert "verified" in str(mgr.get_engine(sid).registry.get("read_file").func(str(marker)))


def test_spawn_applies_the_configured_approval_mode_and_inbox_dial(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)

    async def fake_deliver(session_id, message, *, source=None):
        pass

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    cfg = {"config_id": "cfg9", "event": "pr_open", "name": "", "spawn": {"kind": "new", "persona": "cowork", "models": [], "approval_mode": "interactive", "unattended": False}}
    asyncio.run(mgr._dispatch_inbound(_github_event(configuration=cfg)))
    sid = next(r["session_id"] for r in mgr.list_sessions() if r["title"].startswith("PR #42"))
    rec = mgr.session_store.load(sid)
    assert rec.mode == "interactive" and mgr.unattended.is_unattended(sid) is False
    assert mgr.reviewer_opted(sid) is False and mgr.get_engine(sid).is_attended is None
    # Bypass is the user's call (owner ruling): stored and applied as given; legacy "auto" too.
    cfg = {"config_id": "cfg10", "event": "pr_open", "name": "", "spawn": {"kind": "new", "persona": "cowork", "models": [], "approval_mode": "auto"}}
    asyncio.run(mgr._dispatch_inbound(_github_event(number="43", configuration=cfg)))
    sid = next(r["session_id"] for r in mgr.list_sessions() if r["title"].startswith("PR #43"))
    assert mgr.session_store.load(sid).mode == "bypass-approvals"
