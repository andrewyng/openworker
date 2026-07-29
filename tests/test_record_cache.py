"""Tests for the generic (connector-agnostic) local record cache.

No network, no LLM — this only exercises SQLite + FTS5/LIKE search directly.
Every test gets its own tmp_path DB file; ":memory:" is never used because these
stores are meant to be reopened across calls (mirroring how apify_tools opens a
short-lived connection per tool call).
"""

from __future__ import annotations

from coworker.connectors.record_cache import (
    FieldMap,
    RecordCache,
    extract,
    flatten_strings,
    fts_query,
    has_terms,
    map_item,
)


# -- pure helpers ----------------------------------------------------------------
def test_extract_dotted_path_and_list_index():
    item = {"meta": {"id": "abc"}, "items": [{"name": "first"}, {"name": "second"}]}
    assert extract(item, "meta.id") == "abc"
    assert extract(item, "items.1.name") == "second"
    assert extract(item, "missing.path") is None
    assert extract(item, "items.9.name") is None
    assert extract({"a": 1}, "a.b") is None  # can't descend into a non-container


def test_flatten_strings_excludes_keys_and_numbers():
    text = flatten_strings(
        {"title": "Senior Engineer", "count": 3, "tags": ["python", "remote"]}
    )
    assert "Senior Engineer" in text
    assert "python" in text and "remote" in text
    assert "title" not in text  # keys are schema, not content
    assert "count" not in text and "3" not in text  # bare numbers excluded


def test_map_item_uses_configured_fields():
    fmap = FieldMap(
        key_field="url", title_field="title", url_field="url", text_fields=("title",)
    )
    mapped = map_item({"title": "Backend Engineer", "url": "https://x/1"}, fmap)
    assert mapped["record_key"] == "https://x/1"
    assert mapped["title"] == "Backend Engineer"
    assert mapped["search_text"] == "Backend Engineer"


def test_map_item_missing_key_falls_back_to_stable_hash():
    fmap = FieldMap()
    mapped_a = map_item({"title": "No URL role"}, fmap)
    mapped_b = map_item({"title": "No URL role"}, fmap)
    assert mapped_a["record_key"].startswith("sha1:")
    assert mapped_a["record_key"] == mapped_b["record_key"]  # stable, not random

    mapped_c = map_item({"title": "Different role"}, fmap)
    assert mapped_c["record_key"] != mapped_a["record_key"]


def test_has_terms_and_fts_query():
    assert has_terms("engineering roles")
    assert not has_terms("???")
    assert not has_terms("")
    assert fts_query('"exact phrase"') == '"exact phrase"'
    assert fts_query("what's open?") == '"what\'s" AND "open"'
    assert fts_query("eng*") == '"eng"*'
    assert fts_query("???") == ""


# -- RecordCache: upsert idempotency ---------------------------------------------
def test_upsert_is_idempotent_and_no_duplicates(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    items = [
        {"title": "Backend Engineer", "url": "https://x/1", "location": "Remote"},
        {"title": "Data Scientist", "url": "https://x/2", "location": "NYC"},
    ]
    fmap = FieldMap()

    stats1 = cache.upsert("apify:acme", "apify", items, fmap=fmap)
    assert stats1["new"] == 2 and stats1["updated"] == 0 and stats1["total"] == 2

    stats2 = cache.upsert("apify:acme", "apify", items, fmap=fmap)
    assert stats2["new"] == 0
    assert stats2["updated"] == 0
    assert stats2["unchanged"] == 2
    assert stats2["total"] == 2  # still 2 rows, no duplicates

    cache.close()


def test_upsert_detects_updates_and_refreshes_fts(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    fmap = FieldMap()
    v1 = [{"title": "Backend Engineer", "url": "https://x/1", "desc": "Python role"}]
    cache.upsert("apify:acme", "apify", v1, fmap=fmap)

    results, _ = cache.search("apify:acme", "python")
    assert len(results) == 1

    v2 = [{"title": "Backend Engineer", "url": "https://x/1", "desc": "Golang role"}]
    stats = cache.upsert("apify:acme", "apify", v2, fmap=fmap)
    assert stats["new"] == 0 and stats["updated"] == 1 and stats["unchanged"] == 0

    # old text no longer matches, new text does — proves the FTS index was resynced
    assert cache.search("apify:acme", "python")[0] == []
    golang_hits, _ = cache.search("apify:acme", "golang")
    assert len(golang_hits) == 1
    assert golang_hits[0].key == "https://x/1"

    cache.close()


def test_duplicate_record_key_within_one_batch_collapses(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    items = [
        {"title": "A", "url": "https://x/1"},
        {"title": "A again", "url": "https://x/1"},  # same key, later in the batch
    ]
    stats = cache.upsert("apify:acme", "apify", items, fmap=FieldMap())
    assert stats["new"] == 1 and stats["total"] == 1
    cache.close()


def test_upsert_scoped_per_source(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    fmap = FieldMap()
    cache.upsert(
        "apify:one", "apify", [{"title": "Role One", "url": "https://a/1"}], fmap=fmap
    )
    cache.upsert(
        "apify:two", "apify", [{"title": "Role Two", "url": "https://b/1"}], fmap=fmap
    )

    one, _ = cache.search("apify:one", "role")
    two, _ = cache.search("apify:two", "role")
    assert {r.key for r in one} == {"https://a/1"}
    assert {r.key for r in two} == {"https://b/1"}
    cache.close()


# -- RecordCache: search ranking --------------------------------------------------
def test_search_ranks_title_matches_first(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    items = [
        {"title": "Engineering Manager", "url": "https://x/1", "desc": "leads a team"},
        {"title": "Sales Lead", "url": "https://x/2", "desc": "engineering support role"},
        {"title": "Support Rep", "url": "https://x/3", "desc": "handles engineering tickets"},
    ]
    cache.upsert("apify:acme", "apify", items, fmap=FieldMap())

    results, mode = cache.search("apify:acme", "engineering")
    assert mode == "fts5"
    assert len(results) == 3
    assert results[0].key == "https://x/1"  # title match ranks first
    assert [r.rank for r in results] == [1, 2, 3]
    cache.close()


def test_search_never_touches_network(tmp_path, monkeypatch):
    cache = RecordCache(tmp_path / "cache.db")
    cache.upsert(
        "apify:acme",
        "apify",
        [{"title": "Engineer", "url": "https://x/1"}],
        fmap=FieldMap(),
    )

    def _boom(*a, **kw):
        raise AssertionError("search must never make a network call")

    import httpx

    monkeypatch.setattr(httpx, "request", _boom)
    monkeypatch.setattr(httpx, "get", _boom)
    results, _ = cache.search("apify:acme", "engineer")
    assert len(results) == 1
    cache.close()


def test_search_no_terms_returns_empty(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    cache.upsert(
        "apify:acme", "apify", [{"title": "Engineer", "url": "https://x/1"}], fmap=FieldMap()
    )
    results, _ = cache.search("apify:acme", "???")
    assert results == []
    cache.close()


def test_dotted_paths_and_text_fields_scope_search(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    fmap = FieldMap(
        key_field="meta.id",
        title_field="meta.title",
        url_field="meta.link",
        text_fields=("meta.title",),
    )
    items = [
        {
            "meta": {"id": "r1", "title": "Engineer", "link": "https://x/1"},
            "hidden": "unsearchable secret text",
        }
    ]
    cache.upsert("apify:acme", "apify", items, fmap=fmap)

    hits, _ = cache.search("apify:acme", "engineer")
    assert len(hits) == 1 and hits[0].key == "r1"
    # a term present only in an unmapped field must not match
    hidden_hits, _ = cache.search("apify:acme", "secret")
    assert hidden_hits == []
    cache.close()


# -- FTS5 vs LIKE fallback ---------------------------------------------------------
def test_like_fallback_matches_fts5_result_set_and_is_deterministic(tmp_path):
    items = [
        {"title": "Engineering Manager", "url": "https://x/1", "desc": "leads a team"},
        {"title": "Sales Lead", "url": "https://x/2", "desc": "engineering support role"},
        {"title": "Support Rep", "url": "https://x/3", "desc": "handles engineering tickets"},
    ]

    fts = RecordCache(tmp_path / "fts.db", allow_fts5=True)
    fts.upsert("apify:acme", "apify", items, fmap=FieldMap())
    fts_hits, fts_mode = fts.search("apify:acme", "engineering")
    fts.close()

    like = RecordCache(tmp_path / "like.db", allow_fts5=False)
    like.upsert("apify:acme", "apify", items, fmap=FieldMap())
    like_hits1, like_mode = like.search("apify:acme", "engineering")
    like_hits2, _ = like.search("apify:acme", "engineering")
    like.close()

    assert fts_mode == "fts5"
    assert like_mode == "like"
    assert {r.key for r in fts_hits} == {r.key for r in like_hits1}
    # deterministic ordering across repeated calls
    assert [r.key for r in like_hits1] == [r.key for r in like_hits2]
    assert like_hits1[0].key == "https://x/1"  # title match still ranked first


def test_cache_status_reports_counts_and_time(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    empty = cache.status("apify:acme")
    assert empty["records"] == 0 and empty["fetched_at"] is None

    cache.upsert(
        "apify:acme", "apify", [{"title": "Engineer", "url": "https://x/1"}], fmap=FieldMap()
    )
    status = cache.status("apify:acme")
    assert status["records"] == 1
    assert status["fetched_at"] is not None
    assert status["last_refresh"]["new"] == 1
    cache.close()


def test_clear_source_removes_only_that_source(tmp_path):
    cache = RecordCache(tmp_path / "cache.db")
    fmap = FieldMap()
    cache.upsert("apify:one", "apify", [{"title": "A", "url": "https://a/1"}], fmap=fmap)
    cache.upsert("apify:two", "apify", [{"title": "B", "url": "https://b/1"}], fmap=fmap)

    deleted = cache.clear_source("apify:one")
    assert deleted == 1
    assert cache.status("apify:one")["records"] == 0
    assert cache.status("apify:two")["records"] == 1
    cache.close()
