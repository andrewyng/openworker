"""Metrics in the reviewer eval harness (OPE-225): latency per call, refusals,
breakdown by tag and tool family, and verdict stability across repeat runs. All with
stubs — these test the measuring, not any model."""
from __future__ import annotations

import asyncio

import pytest

from types import SimpleNamespace

from scripts import eval_reviewer as ev


def _result(name, per_row, **kw):
    allows = sum(1 for r in per_row if r["verdict"] == "allow")
    fa = [r["id"] for r in per_row if r["false_allow"]]
    base = dict(
        name=name, rows=len(per_row), allows=allows, false_allows=fa, tokens_in=0, tokens_out=0,
        per_row=per_row, benign_rows=sum(1 for r in per_row if r["correct"] == "allow"),
        benign_allows=sum(1 for r in per_row if r["correct"] == "allow" and r["verdict"] == "allow"),
    )
    base.update(kw)
    return ev.CorpusResult(**base)


def _row(id, verdict, correct, tool="run_shell", tags=("exec",), error=False, refused=False):
    return {
        "id": id, "verdict": verdict, "mapped": ev.verdict_to_key(verdict), "correct": correct,
        "false_allow": ev.is_false_allow(verdict, correct), "error": error, "refused": refused,
        "reason": "", "latency_s": 0.01, "tool": tool, "tags": list(tags),
    }


# -- latency -----------------------------------------------------------------------


def test_stub_run_records_a_latency_per_scored_row():
    reviewer = ev.build_reviewer("stub:test", stub=True)
    r = asyncio.run(ev.run_corpus(reviewer, "benign", include_holdout=False, stub=True))
    assert len(r.latencies) == r.rows
    assert all(s >= 0.0 for s in r.latencies)
    assert r.median_latency is not None and r.p95_latency is not None
    assert all("latency_s" in row and row["tool"] and row["tags"] for row in r.per_row)


def test_latency_percentiles_on_a_known_series():
    r = _result("x", [], latencies=[0.1, 0.2, 0.3, 0.4, 10.0])
    assert r.median_latency == 0.3
    assert r.p95_latency == 10.0
    assert _result("y", []).median_latency is None


# -- refusals ----------------------------------------------------------------------


def test_refusal_is_counted_apart_from_errors():
    class Refuser:
        """Any reviewer whose model declined: an unsure verdict flagged as a refusal."""

        model = "m"

        async def review(self, **_kw):
            return SimpleNamespace(verdict="unsure", reason="refused", error=False, refused=True,
                                   tokens_in=0, tokens_out=0, cache_read=0)

    reviewer = Refuser()
    r = asyncio.run(ev.run_corpus(reviewer, "injection", include_holdout=False, stub=False, limit=2))
    assert r.rows == 2
    assert r.refusals == 2 and r.errors == 0
    assert all(row["refused"] and row["verdict"] == "unsure" for row in r.per_row)
    report = ev.format_report([r], "m", "t")
    assert "| 2 |" in report  # refusals column


# -- breakdown ---------------------------------------------------------------------


def test_tool_family_groups_core_connector_and_mcp_names():
    assert ev.tool_family("run_shell") == "run_shell"
    assert ev.tool_family("write_file") == "write_file"
    assert ev.tool_family("gmail_send_email") == "gmail"
    assert ev.tool_family("outlook_delete_event") == "outlook"
    assert ev.tool_family("mcp__jira__transitionJiraIssue") == "mcp:jira"


def test_breakdown_aggregates_across_corpora_and_sorts_worst_first():
    a = _result("benign", [
        _row("b1", "allow", "allow", tags=("exec", "tests")),
        _row("b2", "unsure", "allow", tags=("exec",)),
    ])
    b = _result("dangerous", [
        _row("d1", "allow", "deny", tool="gmail_send_email", tags=("egress", "connector")),
        _row("d2", "deny", "deny", tool="gmail_send_email", tags=("egress",), error=True),
    ])
    by_tag = {x["key"]: x for x in ev.breakdown([a, b], "tag")}
    assert by_tag["exec"]["rows"] == 2 and by_tag["exec"]["benign_allow_rate"] == 0.5
    assert by_tag["egress"]["false_allows"] == ["d1"] and by_tag["egress"]["errors"] == 1
    assert by_tag["egress"]["benign_allow_rate"] is None
    assert ev.breakdown([a, b], "tag")[0]["key"] == "egress"  # worst first
    by_family = {x["key"]: x for x in ev.breakdown([a, b], "tool_family")}
    assert by_family["gmail"]["rows"] == 2 and by_family["gmail"]["false_allows"] == ["d1"]
    assert by_family["run_shell"]["benign_allow_rate"] == 0.5
    text = ev.format_breakdown([a, b], "tool_family")
    assert "## By tool family" in text and "| gmail | 2 |" in text


# -- stability ---------------------------------------------------------------------


def test_stability_reports_flips_and_false_allow_union():
    run1 = [_result("c", [_row("r1", "allow", "allow"), _row("r2", "unsure", "deny"), _row("r3", "deny", "deny")], latencies=[1.0, 1.0, 1.0])]
    run2 = [_result("c", [_row("r1", "allow", "allow"), _row("r2", "allow", "deny"), _row("r3", "deny", "deny")], latencies=[3.0, 3.0, 3.0])]
    run3 = [_result("c", [_row("r1", "unsure", "allow"), _row("r2", "unsure", "deny"), _row("r3", "deny", "deny")], latencies=[2.0, 2.0, 2.0])]
    s = ev.stability([run1, run2, run3])[0]
    assert s["repeats"] == 3 and s["rows"] == 3
    assert set(s["flipped"]) == {"r1", "r2"}
    assert s["flipped"]["r2"] == ["unsure", "allow", "unsure"]
    assert s["flip_rate"] == pytest.approx(2 / 3)
    assert s["false_allows_any_run"] == ["r2"]  # a false-allow in ANY run counts
    assert s["median_latency"] == 2.0
    text = ev.format_stability([run1, run2, run3])
    assert "3 repeats" in text and "`r2` — unsure → allow → unsure" in text


def test_stub_repeats_are_perfectly_stable():
    reviewer = ev.build_reviewer("stub:test", stub=True)

    async def one():
        return [await ev.run_corpus(reviewer, "dangerous", include_holdout=False, stub=True)]

    runs = [asyncio.run(one()) for _ in range(2)]
    s = ev.stability(runs)[0]
    assert s["flip_rate"] == 0.0 and s["false_allows_any_run"] == []


# -- CLI ---------------------------------------------------------------------------


def test_cli_repeat_runs_n_times_and_reports_stability(capsys):
    rc = ev.main(["--model", "stub:test", "--stub", "--corpus", "injection", "--repeat", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Stability (2 repeats" in out
    assert "## By tool family" in out and "## By tag" in out
    assert "Median / p95 latency" in out


def test_cli_rejects_repeat_below_one():
    with pytest.raises(SystemExit):
        ev.main(["--model", "stub:test", "--stub", "--repeat", "0"])


def test_rows_out_writes_every_scored_row_per_run(tmp_path):
    import json as _json

    out = tmp_path / "rows.jsonl"
    rc = ev.main(["--model", "stub:test", "--stub", "--corpus", "injection", "--repeat", "2", "--rows-out", str(out)])
    assert rc == 0
    rows = [_json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    scored = len([r for r in ev.load_corpus("injection") if not r.holdout and ev.reviewer_eligible(r)[0]])
    assert len(rows) == 2 * scored
    assert {r["run"] for r in rows} == {1, 2}
    assert all(r["corpus"] == "injection" and r["verdict"] and r["correct"] and "latency_s" in r for r in rows)


def test_tag_filter_keeps_only_matching_rows_and_marks_the_report(capsys):
    import asyncio as _asyncio

    reviewer = ev.build_reviewer("stub:test", stub=True)
    all_rows = _asyncio.run(ev.run_corpus(reviewer, "reviewer_actions", include_holdout=False, stub=True))
    sliced = _asyncio.run(ev.run_corpus(reviewer, "reviewer_actions", include_holdout=False, stub=True, tags=["explicit-danger"]))
    assert 0 < sliced.rows < all_rows.rows
    assert all("explicit-danger" in row["tags"] for row in sliced.per_row)
    rc = ev.main(["--model", "stub:test", "--stub", "--corpus", "reviewer_actions", "--tag", "explicit-danger", "--tag", "judge-injection"])
    out = capsys.readouterr().out
    assert rc == 0 and "TAG SLICE (explicit-danger, judge-injection)" in out
