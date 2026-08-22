"""The brain — threads (identity + supersession) and recall (the read path)."""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.brain.recall import recall, score_thread, terms
from coworker.brain.threads import Thread, load, load_all, parse, save, slugify
from coworker.tools.brain import brain_tools


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    return tmp_path / "brain"


def _thread(brain: Path, tid: str, **kw) -> Thread:
    t = Thread(id=tid, title=kw.pop("title", tid), **kw)
    save(t, brain)
    return t


# -- thread file ------------------------------------------------------------------------


def test_round_trips_through_the_file(brain):
    t = Thread(id="phase-2", title="Phase 2", now="Step 1 shipped.", tags=["opt", "chem"])
    t.add("Landed evolve().", when="2026-08-22", source="PHASE2.md")
    t.add("Roadmap drafted.", when="2026-08-20")
    save(t, brain)

    back = load("phase-2", brain)
    assert back.title == "Phase 2" and back.now == "Step 1 shipped."
    assert back.tags == ["opt", "chem"] and back.state == "active"
    # Newest first, and the source survives the round trip.
    assert [e.when for e in back.history] == ["2026-08-22", "2026-08-20"]
    assert back.history[0].source == "PHASE2.md"


def test_history_is_newest_first_and_not_duplicated(brain):
    t = Thread(id="x", title="X")
    t.add("same finding", when="2026-08-22")
    t.add("same finding", when="2026-08-22")
    # A rollup job that re-reads yesterday's report must not enter it twice.
    assert len(t.history) == 1
    t.add("newer", when="2026-08-23")
    assert t.history[0].text == "newer"
    assert t.updated == "2026-08-23"


def test_history_is_bounded(brain):
    t = Thread(id="x", title="X")
    for i in range(60):
        t.add(f"entry {i}", when=f"2026-08-{(i % 28) + 1:02d}")
    save(t, brain)
    # Past a few dozen entries a thread stops being readable; older detail lives in the
    # dated reports it points at.
    assert len(load("x", brain).history) <= 40


def test_unparseable_frontmatter_still_yields_a_thread(brain):
    d = brain / "threads"
    d.mkdir(parents=True)
    (d / "broken.md").write_text("---\n: not: yaml:\n---\n**Now:** still readable.\n")
    t = load("broken", brain)
    # A malformed thread must degrade, not vanish: losing memory silently is the worst outcome.
    assert t is not None and t.now == "still readable."


def test_slugify_rejects_nonsense():
    assert slugify("OpenEvolve Phase 2!") == "openevolve-phase-2"
    assert slugify("   ") == ""


def test_active_threads_sort_first(brain):
    _thread(brain, "old", state="resolved", updated="2026-08-22")
    _thread(brain, "live", state="active", updated="2026-01-01")
    assert [t.id for t in load_all(brain)] == ["live", "old"]


# -- recall -----------------------------------------------------------------------------


def test_terms_drops_stopwords():
    assert terms("what do I know about the openEvolve phase") == ["know", "openevolve", "phase"]


def test_title_beats_a_passing_mention(brain):
    about = Thread(id="qdrant-corpus", title="Qdrant corpus", now="15 papers stored.")
    passing = Thread(id="unrelated", title="Unrelated", now="Nothing here.")
    passing.add("mentioned qdrant once", when="2026-08-01")
    words = terms("qdrant")
    assert score_thread(about, words) > score_thread(passing, words)


def test_recall_finds_the_thread_and_leads_with_its_state(brain):
    t = Thread(id="phase-2", title="OpenEvolve Phase 2", now="Step 1 of 4 shipped.",
               tags=["openevolve"])
    t.add("evolve() landed", when="2026-08-22")
    save(t, brain)

    out = recall("openevolve phase", base=brain, corpus_roots=[]).as_dict()
    assert out["threads"][0]["title"] == "OpenEvolve Phase 2"
    assert out["threads"][0]["now"] == "Step 1 of 4 shipped."
    assert out["threads"][0]["recent"]


def test_recall_searches_the_dated_corpus(brain, tmp_path):
    reports = tmp_path / "__task__demo"
    reports.mkdir()
    (reports / "papers-2026-08-20.md").write_text("A finding about tokamak confinement.\n")
    (reports / "papers-2026-08-22.md").write_text("A newer finding about tokamak confinement.\n")

    out = recall("tokamak", base=brain, corpus_roots=[reports]).as_dict()
    dates = [c["date"] for c in out["corpus"]]
    # Newest first: in a running record the recent statement supersedes the older one.
    assert dates == sorted(dates, reverse=True)
    assert "tokamak" in out["corpus"][0]["text"].lower()


def test_recall_skips_the_focus_file(brain, tmp_path):
    reports = tmp_path / "__task__demo"
    reports.mkdir()
    (reports / "FOCUS.md").write_text("tokamak is the current focus\n")
    (reports / "brief-2026-08-22.md").write_text("tokamak result recorded\n")
    out = recall("tokamak", base=brain, corpus_roots=[reports]).as_dict()
    # FOCUS.md is always-loaded context, not a recall result — returning it wastes the budget.
    assert all("FOCUS.md" not in c["source"] for c in out["corpus"])
    assert len(out["corpus"]) == 1


def test_empty_brain_says_so_rather_than_looking_answered(brain):
    out = recall("anything", base=brain, corpus_roots=[]).as_dict()
    assert out["threads"] == [] and out["corpus"] == []
    # "Nothing found" and "nowhere to look" are different answers.
    assert out["note"]


# -- tools ------------------------------------------------------------------------------


def test_note_creates_then_extends_one_thread(brain):
    _, note = brain_tools(brain)
    first = note(thread="OpenEvolve Phase 2", entry="Step 1 shipped.", now="Step 1 of 4 done.")
    assert first["created"] and first["thread"] == "openevolve-phase-2"

    again = note(thread="openevolve-phase-2", entry="Materials example started.")
    assert not again["created"] and again["entries"] == 2

    # Matching by TITLE too, so a rephrasing does not fork a near-duplicate subject.
    by_title = note(thread="OpenEvolve Phase 2", entry="Budget ledger next.")
    assert not by_title["created"] and by_title["entries"] == 3


def test_note_only_replaces_state_when_asked(brain):
    _, note = brain_tools(brain)
    note(thread="x", entry="first", now="state one")
    note(thread="x", entry="second")
    assert load("x", brain).now == "state one"
    note(thread="x", entry="third", now="state two")
    # The state line is the supersession mechanism: it changes only on an explicit new claim.
    assert load("x", brain).now == "state two"


def test_note_records_lifecycle_changes(brain):
    _, note = brain_tools(brain)
    note(thread="x", entry="done", state="resolved")
    assert load("x", brain).state == "resolved"
    note(thread="x", entry="nonsense state ignored", state="banana")
    assert load("x", brain).state == "resolved"


def test_note_rejects_empty_input(brain):
    _, note = brain_tools(brain)
    assert "error" in note(thread="x", entry="   ")
    assert "error" in note(thread="", entry="something")


def test_recall_tool_returns_what_note_wrote(brain):
    rec, note = brain_tools(brain)
    note(thread="Local model reliability", entry="Compaction now counts tool schemas.",
         now="Compaction fixed; silent failures still open.")
    out = rec("compaction")
    assert out["threads"][0]["id"] == "local-model-reliability"
    assert "silent failures" in out["threads"][0]["now"]


def test_tools_carry_schemas():
    rec, note = brain_tools()
    for fn, name in ((rec, "brain_recall"), (note, "brain_note")):
        assert fn.__name__ == name
        assert fn.__coworker_schema__["function"]["name"] == name


def test_brain_is_in_the_catalog():
    import coworker.agents  # noqa: F401  (breaks the catalog/agents import cycle)
    from coworker.agents.base import AgentContext
    from coworker.catalog import CATALOG, expand

    assert "brain" in CATALOG
    # No workspace requirement — the brain is machine-scoped, so a folderless Chat can recall.
    tools = expand(["brain"], AgentContext(workspace=None, executor=None, todo=None))
    assert {t.__name__ for t in tools} == {"brain_recall", "brain_note"}


def test_one_common_word_does_not_carry_a_thread_in(brain):
    wanted = Thread(id="openevolve-phase-2", title="OpenEvolve Phase 2", now="Step 1 of 4 done.")
    unrelated = Thread(id="funding", title="Grants and funding", now="DARPA Phase I, ~$200K.")
    save(wanted, brain)
    save(unrelated, brain)

    out = recall("openevolve phase 2", base=brain, corpus_roots=[]).as_dict()
    ids = [t["id"] for t in out["threads"]]
    # "phase" alone matches the funding thread; only the thread matching more than one term
    # belongs in the answer.
    assert ids == ["openevolve-phase-2"]


def test_the_thread_a_query_names_is_found_off_its_name_alone(brain):
    save(Thread(id="dcode-stack", title="dcode-stack", now="Quiet since August."), brain)
    # "happened" matches nothing; the name alone has to carry it, or asking about a project
    # in a normal sentence returns nothing.
    out = recall("what happened to dcode-stack", base=brain, corpus_roots=[]).as_dict()
    assert [t["id"] for t in out["threads"]] == ["dcode-stack"]


def test_a_single_word_query_still_matches_on_one_term(brain):
    save(Thread(id="funding", title="Grants and funding", now="DARPA Phase I."), brain)
    out = recall("funding", base=brain, corpus_roots=[]).as_dict()
    assert [t["id"] for t in out["threads"]] == ["funding"]
