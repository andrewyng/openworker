"""Automation suggestions — the rules that turn observed activity into what to schedule."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coworker.automation.suggestions import (
    RepoSignal,
    Signals,
    Suggestion,
    _derive_identity,
    cadence_of,
    scan_repos,
    subject_text,
    suggest,
)


class _Task:
    def __init__(self, title: str, instructions: str = "") -> None:
        self.title = title
        self.instructions = instructions


# -- cadence ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cron, cadence",
    [
        ("0 4 * * *", "daily"),
        ("30 5 * * 0", "weekly"),
        ("0 7 1 * *", "monthly"),
        ("30 7 1 1,4,7,10 *", "quarterly"),
        ("0 8 1 1 *", "yearly"),
        ("nonsense", "other"),
        (None, "other"),
    ],
)
def test_cadence_of(cron, cadence):
    assert cadence_of(cron) == cadence


# -- dedupe corpus ---------------------------------------------------------------------


def test_subject_text_ignores_paths():
    # A path is plumbing, not subject matter. Left in, "/home/me/OpenWorker/knowledge" makes
    # the string "openworker" look covered by a job that has nothing to do with that repo.
    tasks = [_Task("Knowledge base ingest", "Write findings to /home/me/OpenWorker/knowledge.")]
    text = subject_text(tasks)
    assert "knowledge" in text  # the title still counts
    assert "openworker" not in text


def test_subject_text_stops_before_shared_boilerplate():
    # Every automation carries an appended focus preamble naming each project; matching against
    # it would make every subject look already-covered.
    tasks = [
        _Task(
            "Morning news briefing",
            "Write me a briefing on what happened.\n" + "x" * 600 + "\nProjects: acme, widget",
        )
    ]
    text = subject_text(tasks)
    assert "briefing" in text
    assert "widget" not in text


# -- identity --------------------------------------------------------------------------


def test_identity_is_the_author_dominating_several_repos():
    # No configured git identity is the common case when commits are made through an agent.
    per_repo = {
        Path("/a"): {"me@x": 40, "other@y": 1},
        Path("/b"): {"me@x": 12},
        Path("/upstream"): {"maintainer@z": 200, "contrib@z": 90},
    }
    assert _derive_identity(per_repo) == {"me@x"}


def test_identity_refuses_to_guess_from_one_repo():
    # Dominating a single repo could just as easily be a clone of somebody else's project;
    # claiming it would mislabel every other repo's commits as not-yours.
    assert _derive_identity({Path("/a"): {"someone@x": 50}}) == set()


def test_identity_refuses_on_a_tie():
    per_repo = {
        Path("/a"): {"one@x": 10},
        Path("/b"): {"one@x": 10},
        Path("/c"): {"two@y": 10},
        Path("/d"): {"two@y": 10},
    }
    assert _derive_identity(per_repo) == set()


def test_repo_signal_excludes_upstream_clones():
    tracked = RepoSignal("/llama.cpp", "llama.cpp", commits=0, others=234, last_days=None)
    mine = RepoSignal("/mine", "mine", commits=19, others=0, last_days=2)
    assert not tracked.mine and mine.mine


def _git(repo: Path, *args: str, **env) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={"HOME": str(repo), "PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", **env},
    )


def test_scan_repos_counts_my_commits(tmp_path):
    home = tmp_path / "home"
    repo = home / "proj"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("hi")
    _git(repo, "add", ".")
    _git(
        repo, "-c", "user.name=Me", "-c", "user.email=me@example.com",
        "commit", "-qm", "mine",
        GIT_AUTHOR_NAME="Me", GIT_AUTHOR_EMAIL="me@example.com",
        GIT_COMMITTER_NAME="Me", GIT_COMMITTER_EMAIL="me@example.com",
    )
    (home / "notarepo").mkdir()

    signals = scan_repos(home)
    assert [r.name for r in signals] == ["proj"]
    # One author, one repo — too little to derive an identity, so nothing is claimed as mine.
    assert signals[0].commits + signals[0].others == 1


# -- rules -----------------------------------------------------------------------------


def _signals(**kw) -> Signals:
    base = dict(task_count=6, cadences={"daily"}, task_text="", personas={"repo-ops", "research"})
    base.update(kw)
    return Signals(**base)


def keys(out: list[Suggestion]) -> set[str]:
    return {s.key for s in out}


def test_suggests_the_next_rung_of_the_ladder():
    out = suggest(_signals(cadences={"daily"}))
    assert "ladder-weekly" in keys(out)
    # …and not two rungs up: a monthly report with no weeklies to read is an empty report.
    assert "ladder-monthly" not in keys(out)


def test_a_covered_ladder_suggests_nothing():
    out = suggest(_signals(cadences={"daily", "weekly", "monthly", "quarterly", "yearly"}))
    assert not {k for k in keys(out) if k.startswith("ladder-")}


def test_active_repo_with_no_automation():
    repo = RepoSignal("/home/me/acme", "acme", commits=19, others=0, last_days=2)
    out = suggest(_signals(repos=[repo]))
    hit = next(s for s in out if s.key == "repo-health-acme")
    # The evidence, with its numbers, is the whole difference from a template.
    assert "19" in hit.reason and "acme" in hit.reason
    assert hit.agent == "repo-ops"


def test_repo_already_watched_is_not_suggested():
    repo = RepoSignal("/home/me/acme", "acme", commits=19, others=0, last_days=2)
    covered = _signals(repos=[repo], task_text="spec drift check\naudit my acme specs")
    assert "repo-health-acme" not in keys(suggest(covered))


def test_upstream_clone_is_not_suggested():
    clone = RepoSignal("/home/me/llama.cpp", "llama.cpp", commits=0, others=234, last_days=None)
    assert "repo-health-llama.cpp" not in keys(suggest(_signals(repos=[clone])))


def test_quiet_repo_gets_a_different_suggestion():
    quiet = RepoSignal("/home/me/old", "old", commits=9, others=0, last_days=40)
    out = keys(suggest(_signals(repos=[quiet])))
    assert "quiet-projects" in out


def test_vector_store_with_nothing_writing_to_it():
    out = suggest(_signals(mcp={"qdrant"}))
    hit = next(s for s in out if s.key == "kb-ingest")
    assert hit.requires == ["qdrant"]
    # Already covered → silent.
    assert "kb-ingest" not in keys(suggest(_signals(mcp={"qdrant"}, task_text="knowledge base ingest")))


def test_connector_without_an_automation():
    out = suggest(_signals(connectors={"slack"}))
    assert "connector-slack" in keys(out)
    assert "connector-slack" not in keys(suggest(_signals(connectors={"slack"}, task_text="weekly slack digest")))


def test_outside_view_needs_a_schedule_worth_challenging():
    assert "outside-view" not in keys(suggest(_signals(task_count=2)))
    assert "outside-view" in keys(suggest(_signals(task_count=6)))


def test_ranked_by_evidence_and_capped():
    repos = [
        RepoSignal(f"/home/me/r{i}", f"r{i}", commits=5 + i * 10, others=0, last_days=1)
        for i in range(8)
    ]
    out = suggest(_signals(repos=repos), limit=4)
    assert len(out) == 4
    assert [s.score for s in out] == sorted((s.score for s in out), reverse=True)


def test_every_suggestion_can_explain_itself():
    repo = RepoSignal("/home/me/acme", "acme", commits=19, others=0, last_days=2)
    out = suggest(_signals(repos=[repo], mcp={"qdrant"}, connectors={"slack"}))
    assert out, "expected suggestions for this signal set"
    for s in out:
        # A suggestion with no reason is a template wearing a suggestion's clothes.
        assert s.reason.strip()
        assert s.instructions.strip() and s.cron.strip()
        assert cadence_of(s.cron) == s.cadence
