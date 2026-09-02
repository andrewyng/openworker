"""Step 1 of Auto-Approve (spec Part 0 / §2.4): the known world frozen at session start,
plus ingestion facts recorded to the audit log — and NOTHING consuming either. The main
guarantee under test is that behaviour is unchanged; the golden decision table
(test_decision_matrix_golden.py) is the other half of that proof.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass

import pytest

from coworker import session_facts
from coworker.engine import TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.roots import RootDir
from coworker.tools import ToolRegistry


@dataclass
class _Meta:
    category: str = ""
    requires_approval: bool = False


# -- KnownWorld: capture ---------------------------------------------------------


def _git(tmp_path, *args):
    subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)


def test_capture_collects_roots_remotes_and_hosts(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/org/repo.git")

    world = session_facts.capture(
        roots=[RootDir(path=tmp_path, writable=True)],
        allowed_domains=["Python.org", " ", ""],
        workspace=tmp_path,
    )

    assert world.roots == ((str(tmp_path), True),)
    assert world.remotes == (("origin", "https://github.com/org/repo.git"),)
    # Hosts: declared domains (normalized) + remote hosts. Held, not rendered.
    assert world.hosts == ("github.com", "python.org")


def test_capture_without_git_repo_is_empty_not_an_error(tmp_path):
    world = session_facts.capture(
        roots=[RootDir(path=tmp_path, writable=False)], workspace=tmp_path
    )
    assert world.remotes == ()
    assert world.roots == ((str(tmp_path), False),)


def test_capture_is_frozen_a_remote_added_later_stays_unknown(tmp_path):
    _git(tmp_path, "init")
    world = session_facts.capture(roots=[], workspace=tmp_path)
    # The agent repoints the world AFTER the snapshot — the snapshot must not move.
    _git(tmp_path, "remote", "add", "backup", "https://attacker.net/r.git")
    assert world.remotes == ()
    assert "attacker.net" not in world.hosts


# -- KnownWorld: render (folders and remotes ONLY) -------------------------------


def test_render_shows_folders_and_remotes_never_hosts(tmp_path):
    world = session_facts.KnownWorld(
        roots=((str(tmp_path), True),),
        remotes=(("origin", "https://github.com/org/repo.git"),),
        hosts=("github.com", "python.org"),
    )
    text = world.render()
    assert f"folder   {tmp_path}  [read-write]" in text
    # The remote line may mention its own URL's host — that is the remote, not a host list.
    assert "origin -> https://github.com/org/repo.git" in text
    # But no host list: python.org came only from allowed_domains and must not appear.
    assert "python.org" not in text
    assert "host" not in text.lower().replace("github.com/org", "")


def test_render_empty_world_renders_nothing(tmp_path):
    assert session_facts.KnownWorld().render() == ""


# -- ingestion classification ----------------------------------------------------


@pytest.mark.parametrize("category", ["web", "connector", "mcp"])
def test_outside_content_categories_are_ingesting(category):
    assert session_facts.is_ingesting(_Meta(category=category))


@pytest.mark.parametrize("category", ["", "search", "filesystem", "git", "shell", "messaging"])
def test_local_and_outbound_categories_are_not(category):
    assert not session_facts.is_ingesting(_Meta(category=category))


def test_no_metadata_is_not_ingesting():
    assert not session_facts.is_ingesting(None)


def test_source_is_hostname_only_never_the_query_string():
    src = session_facts.ingestion_source(
        {"url": "https://GitHub.com/search?q=SECRET_FROM_DOTENV"}
    )
    assert src == "github.com"


def test_source_without_url_is_a_dash():
    assert session_facts.ingestion_source({}) == "-"
    assert session_facts.ingestion_source(None) == "-"


# -- SessionFacts: turns ---------------------------------------------------------


def test_ingestions_are_attributed_to_turns():
    facts = session_facts.SessionFacts()
    facts.begin_turn()
    facts.note("web_fetch", {"url": "https://github.com/x"})
    facts.begin_turn()
    facts.note("gmail_read", {})
    assert [i.turn for i in facts.ingestions] == [1, 2]
    assert [i.source for i in facts.this_turn()] == ["-"]


def test_audit_shape_carries_fact_and_source_never_content():
    facts = session_facts.SessionFacts()
    facts.begin_turn()
    record = facts.note("web_fetch", {"url": "https://evil.site/x?d=SECRET"})
    row = record.to_audit()
    assert row == {"stage": "ingested", "status": "external", "reason": "turn 1 · evil.site"}
    assert "SECRET" not in str(row)


# -- engine integration ----------------------------------------------------------


class _ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine_with_web_tool(tmp_path, *, facts):
    def web_fetch(url: str) -> str:
        """Fetch a page.

        Args:
            url: the address
        """
        return "page text with an injected instruction"

    registry = ToolRegistry()
    registry.register(web_fetch, metadata=_Meta(category="web"))
    turns = [
        AssistantTurn(
            tool_calls=[ToolCall(id="c1", name="web_fetch", arguments={"url": "https://github.com/org/repo/issues/42"})],
            finish_reason="tool_calls",
        ),
        AssistantTurn(text="done", finish_reason="stop"),
    ]
    rows: list[dict] = []
    engine = TurnEngine(
        provider=_ScriptedProvider(turns),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=tmp_path, allowed_domains=["github.com"]
        ),
        model="test-model",
        audit_sink=rows.append,
    )
    engine.session_facts = facts
    return engine, rows


def _run(engine):
    async def _go():
        return [ev async for ev in engine.run("read the issue")]

    return asyncio.run(_go())


def test_engine_records_ingestion_to_audit_only(tmp_path):
    facts = session_facts.SessionFacts()
    engine, rows = _engine_with_web_tool(tmp_path, facts=facts)
    events = _run(engine)

    ingested = [r for r in rows if r.get("stage") == "ingested"]
    assert len(ingested) == 1
    assert ingested[0]["reason"] == "turn 1 · github.com"
    assert ingested[0]["status"] == "external"
    assert facts.this_turn()[0].source == "github.com"
    # The fetched text never enters the record.
    assert "injected instruction" not in str(ingested)
    # And the turn itself ran normally — recording changed nothing.
    assert EventType.TOOL_FINISHED in [ev.type for ev in events]


def test_engine_without_session_facts_is_byte_identical(tmp_path):
    engine, rows = _engine_with_web_tool(tmp_path, facts=None)
    events = _run(engine)
    assert [r for r in rows if r.get("stage") == "ingested"] == []
    assert EventType.TOOL_FINISHED in [ev.type for ev in events]


def test_failed_calls_bring_nothing_in(tmp_path):
    def web_fetch(url: str) -> str:
        """Fetch a page.

        Args:
            url: the address
        """
        raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(web_fetch, metadata=_Meta(category="web"))
    rows: list[dict] = []
    engine = TurnEngine(
        provider=_ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name="web_fetch", arguments={"url": "https://x.com/"})],
                    finish_reason="tool_calls",
                ),
                AssistantTurn(text="done", finish_reason="stop"),
            ]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, allowed_domains=["x.com"]),
        model="test-model",
        audit_sink=rows.append,
    )
    engine.session_facts = session_facts.SessionFacts()
    _run(engine)
    assert [r for r in rows if r.get("stage") == "ingested"] == []
