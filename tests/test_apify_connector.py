"""Tests for the Apify connector — descriptor/registry wiring, tool behavior, and
the sample scheduled-refresh task.

No network (coworker.connectors.integration_tools._request is monkeypatched — the
same seam tests/test_connectors.py uses) and no LLM. Every test gets its own
tmp_path secrets file and cache DB file.
"""

from __future__ import annotations

import coworker.connectors.integration_tools as it
from coworker.automation import Schedule, ScheduledTask
from coworker.connectors.apify_tools import make_apify_tools
from coworker.connectors.descriptors import get_descriptor
from coworker.connectors.tool_defs import TOOL_DEFS, approval_for_tool
from coworker.secrets import SecretStore


def _put_profile(secrets, **overrides):
    fields = {"api_token": "apify_api_x", "dataset_id": "acme~jobs"}
    fields.update(overrides)
    secrets.put("apify:default", {**fields, "enabled": True})


def _fake_request(pages, calls):
    queue = list(pages)

    def fake(method, url, *, headers=None, params=None, json=None, auth=None):
        calls.append(
            {"method": method, "url": url, "headers": headers or {}, "params": params}
        )
        if not queue:
            return {"ok": True, "data": []}
        page = queue.pop(0)
        if isinstance(page, dict):
            return page  # a raw {"error": ...} or a wrong-shape payload
        # Mirror a real API: never return more than the caller's requested limit,
        # even if the queued fixture page is larger.
        want = (params or {}).get("limit")
        if isinstance(want, int):
            page = page[:want]
        return {"ok": True, "data": page}

    return fake


def _tools(tmp_path, monkeypatch, pages=(), *, profile_overrides=None, cache_name="cache.db"):
    secrets = SecretStore(tmp_path / "secrets.json")
    if profile_overrides is not False:
        _put_profile(secrets, **(profile_overrides or {}))
    calls: list = []
    monkeypatch.setattr(it, "_request", _fake_request(pages, calls))
    tools = {
        t.__name__: t
        for t in make_apify_tools(secrets, cache_path=str(tmp_path / cache_name))
    }
    return tools, calls


# -- registry / descriptor wiring -------------------------------------------------
def test_descriptor_registered_and_tools_are_read_only():
    d = get_descriptor("apify")
    assert d is not None
    assert d.available is True
    assert d.two_way is False
    assert d.account_field == "dataset_id"
    assert d.instructions
    import re

    assert re.match(r"^#[0-9a-fA-F]{6}$", d.brand_color)
    assert d.logo == "apify"
    assert d.validate is not None

    apify_defs = [t for t in TOOL_DEFS if t.connector == "apify"]
    names = {t.name for t in apify_defs}
    assert names == {"apify_refresh_cache", "apify_search_cache", "apify_cache_status"}
    assert all(t.kind == "read" for t in apify_defs)
    assert all(approval_for_tool(t.name) is False for t in apify_defs)


def test_validate_apify_identity(monkeypatch):
    import coworker.connectors.descriptors as d

    class _Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    def fake(status, payload):
        import httpx

        monkeypatch.setattr(httpx, "request", lambda *a, **k: _Resp(status, payload))

    fake(200, {"data": {"username": "alice"}})
    res = d._validate_apify({"api_token": "t"})
    assert res.ok and res.identity == "@alice"

    fake(401, {"error": "token not found"})
    res = d._validate_apify({"api_token": "bad"})
    assert not res.ok and "not found" in res.error

    fake(200, {})  # unexpected shape must not pass
    res = d._validate_apify({"api_token": "bad"})
    assert not res.ok


# -- not-connected guard -----------------------------------------------------------
def test_tools_error_when_not_connected(tmp_path, monkeypatch):
    tools, calls = _tools(tmp_path, monkeypatch, profile_overrides=False)

    assert "not connected" in tools["apify_refresh_cache"]()["error"]
    assert "not connected" in tools["apify_search_cache"]("jobs")["error"]
    assert "not connected" in tools["apify_cache_status"]()["error"]
    assert calls == []
    assert not (tmp_path / "cache.db").exists()  # guard runs before any cache access


# -- refresh: upsert, idempotency, pagination --------------------------------------
def test_refresh_upserts_and_is_idempotent(tmp_path, monkeypatch):
    page = [
        {"title": "Backend Engineer", "url": "https://x/1", "location": "Remote"},
        {"title": "Data Scientist", "url": "https://x/2", "location": "NYC"},
    ]
    tools, calls = _tools(tmp_path, monkeypatch, pages=[page])
    result = tools["apify_refresh_cache"]()
    assert result["ok"] is True
    assert result["new"] == 2 and result["updated"] == 0
    assert result["total_records"] == 2
    assert result["search_mode"] == "fts5"

    tools2, calls2 = _tools(tmp_path, monkeypatch, pages=[page])
    result2 = tools2["apify_refresh_cache"]()
    assert result2["new"] == 0 and result2["updated"] == 0 and result2["unchanged"] == 2
    assert result2["total_records"] == 2  # no duplicates


def test_refresh_detects_updates(tmp_path, monkeypatch):
    v1 = [{"title": "Backend Engineer", "url": "https://x/1", "desc": "Python role"}]
    tools, _ = _tools(tmp_path, monkeypatch, pages=[v1])
    tools["apify_refresh_cache"]()

    v2 = [{"title": "Backend Engineer", "url": "https://x/1", "desc": "Golang role"}]
    tools2, _ = _tools(tmp_path, monkeypatch, pages=[v2])
    result = tools2["apify_refresh_cache"]()
    assert result["new"] == 0 and result["updated"] == 1

    search_result = tools2["apify_search_cache"]("golang")
    assert search_result["count"] == 1


def test_refresh_paginates_until_short_page(tmp_path, monkeypatch):
    page1 = [{"title": f"Role {i}", "url": f"https://x/{i}"} for i in range(1000)]
    page2 = [{"title": f"Role {i}", "url": f"https://x/{i}"} for i in range(1000, 2000)]
    page3 = [{"title": "Role 2000", "url": "https://x/2000"}]  # short — stops the loop
    tools, calls = _tools(tmp_path, monkeypatch, pages=[page1, page2, page3])

    result = tools["apify_refresh_cache"](max_records=10_000)
    assert result["fetched"] == 2001
    offsets = [c["params"]["offset"] for c in calls]
    assert offsets == [0, 1000, 2000]


def test_refresh_max_records_caps_pagination(tmp_path, monkeypatch):
    page1 = [{"title": f"Role {i}", "url": f"https://x/{i}"} for i in range(1000)]
    page2 = [{"title": f"Role {i}", "url": f"https://x/{i}"} for i in range(1000, 2000)]
    tools, calls = _tools(tmp_path, monkeypatch, pages=[page1, page2])

    result = tools["apify_refresh_cache"](max_records=1500)
    assert result["fetched"] == 1500
    assert len(calls) == 2  # stops once `want` items collected, no third page fetched


def test_refresh_rejects_non_list_payload(tmp_path, monkeypatch):
    tools, _ = _tools(tmp_path, monkeypatch, pages=[{"ok": True, "data": {"oops": True}}])
    result = tools["apify_refresh_cache"]()
    assert "error" in result
    status = tools["apify_cache_status"]()
    assert status["records"] == 0  # cache untouched


def test_refresh_http_error_leaves_cache_intact(tmp_path, monkeypatch):
    good = [{"title": "Engineer", "url": "https://x/1"}]
    tools, _ = _tools(tmp_path, monkeypatch, pages=[good])
    tools["apify_refresh_cache"]()

    tools2, _ = _tools(tmp_path, monkeypatch, pages=[{"error": "HTTP 401"}])
    result = tools2["apify_refresh_cache"]()
    assert result["error"] == "HTTP 401"
    status = tools2["apify_cache_status"]()
    assert status["records"] == 1  # unchanged from the earlier successful refresh


def test_missing_key_field_falls_back_to_hash_and_drops_nothing(tmp_path, monkeypatch):
    items = [{"title": "No URL role A"}, {"title": "No URL role A"}, {"title": "No URL role B"}]
    tools, _ = _tools(tmp_path, monkeypatch, pages=[items])
    result = tools["apify_refresh_cache"]()
    # two identical items collapse to one row; the distinct one is separate — 2 total
    assert result["total_records"] == 2

    tools2, _ = _tools(tmp_path, monkeypatch, pages=[items])
    result2 = tools2["apify_refresh_cache"]()
    assert result2["new"] == 0 and result2["total_records"] == 2  # stable across runs


def test_dataset_id_case_preserved_despite_lowercased_account(tmp_path, monkeypatch):
    tools, calls = _tools(
        tmp_path,
        monkeypatch,
        pages=[[{"title": "Engineer", "url": "https://x/1"}]],
        profile_overrides={"dataset_id": "AcMe~JobsMixedCase"},
    )
    result = tools["apify_refresh_cache"]()
    assert result["dataset_id"] == "AcMe~JobsMixedCase"
    assert result["account"] == "acme~jobsmixedcase"  # accounts._norm lowercases it
    assert "AcMe~JobsMixedCase" in calls[0]["url"]  # request used the original case


# -- search -------------------------------------------------------------------------
def test_search_ranks_and_scopes_per_account(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put(
        "apify:account:acme~jobs",
        {"api_token": "t", "dataset_id": "acme~jobs", "account": "acme~jobs"},
    )
    secrets.put(
        "apify:account:other~jobs",
        {"api_token": "t", "dataset_id": "other~jobs", "account": "other~jobs"},
    )
    calls: list = []
    monkeypatch.setattr(
        it,
        "_request",
        _fake_request(
            [
                [{"title": "Engineering Manager", "url": "https://a/1"}],
                [{"title": "Support engineering ticket", "url": "https://b/1"}],
            ],
            calls,
        ),
    )
    tools = {
        t.__name__: t
        for t in make_apify_tools(secrets, cache_path=str(tmp_path / "cache.db"))
    }
    tools["apify_refresh_cache"](account="acme~jobs")
    tools["apify_refresh_cache"](account="other~jobs")

    r1 = tools["apify_search_cache"]("engineering", account="acme~jobs")
    r2 = tools["apify_search_cache"]("engineering", account="other~jobs")
    assert r1["count"] == 1 and r1["results"][0]["url"] == "https://a/1"
    assert r1["account"] == "acme~jobs"
    assert r2["count"] == 1 and r2["results"][0]["url"] == "https://b/1"


def test_search_never_touches_network(tmp_path, monkeypatch):
    tools, _ = _tools(
        tmp_path, monkeypatch, pages=[[{"title": "Engineer", "url": "https://x/1"}]]
    )
    tools["apify_refresh_cache"]()

    def _boom(*a, **kw):
        raise AssertionError("apify_search_cache must never call the network")

    monkeypatch.setattr(it, "_request", _boom)
    result = tools["apify_search_cache"]("engineer")
    assert result["count"] == 1


def test_search_empty_cache_hints_to_refresh(tmp_path, monkeypatch):
    tools, _ = _tools(tmp_path, monkeypatch, pages=[])
    result = tools["apify_search_cache"]("anything")
    assert result["ok"] is True
    assert result["results"] == []
    assert "refresh" in result["hint"]


def test_search_no_terms_returns_error(tmp_path, monkeypatch):
    tools, _ = _tools(tmp_path, monkeypatch)
    result = tools["apify_search_cache"]("???")
    assert "no searchable terms" in result["error"]


# -- status ---------------------------------------------------------------------
def test_cache_status_before_and_after_refresh(tmp_path, monkeypatch):
    tools, _ = _tools(
        tmp_path, monkeypatch, pages=[[{"title": "Engineer", "url": "https://x/1"}]]
    )
    before = tools["apify_cache_status"]()
    assert before["records"] == 0 and before["fetched_at"] is None

    tools["apify_refresh_cache"]()
    after = tools["apify_cache_status"]()
    assert after["records"] == 1
    assert after["fetched_at"] is not None
    assert after["search_mode"] == "fts5"
    assert after["last_refresh"]["new"] == 1


# -- sample scheduled-refresh task -------------------------------------------------
def test_hourly_refresh_scheduled_task_shape(tmp_path):
    task = ScheduledTask(
        title="Refresh Apify cache",
        instructions=(
            "Call apify_refresh_cache to pull the latest items from the connected "
            "Apify dataset into the local cache, then report how many records "
            "were added or updated."
        ),
        schedule=Schedule(kind="cron", cron="0 * * * *"),
        workspace=str(tmp_path),
    )
    assert "apify_refresh_cache" in task.instructions
    # timing belongs in cron, never restated in the prompt text
    for word in ("hour", "hourly", "every"):
        assert word not in task.instructions.lower()
    assert task.task_session_id == f"__task__{task.id}"
    # refresh is kind="read" -> never gated -> nothing needs pre-granting
    assert approval_for_tool("apify_refresh_cache") is False
    assert task.always_allowed_tools == []
