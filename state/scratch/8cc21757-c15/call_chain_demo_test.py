"""Tests for the agpack multi-agent call-chain demo.

These drive agpack's *real* delegation + audit API (delegation.verify,
audit.replay/LedgerCorrupt) end-to-end. If the agpack trust API changes,
these tests change with it.

`agpack` is imported from the read-only bundle via the conftest that adds
its source dir to sys.path.
"""

import pytest

import call_chain_demo as cc
from agpack.sandbox.capabilities import Scope
from agpack.sandbox.limits import Budget


@pytest.fixture(scope="module")
def chain():
    return cc.run_call_chain()


def test_leaf_token_verifies(chain):
    sc = chain["scribe"]
    assert sc.chain[0].agent_id == "researcher"   # iss == root
    assert sc.chain[-1].agent_id == "scribe"       # sub == leaf
    assert sc.iss == sc.chain[0].agent_id
    assert sc.sub == sc.chain[-1].agent_id
    assert sc.token_id == sc.chain[-1].token_id
    # verify_at is strictly < exp, and the token is current.
    assert chain["verify_at"] < chain["exp"]
    assert cc.verify_assert(sc, chain["verify_at"], chain["exp"]) is sc


def test_chain_is_line_and_narrowing(chain):
    hops = chain["scribe"].chain
    # chain is a connected line: each parent_hop_id is the previous token_id.
    for i in range(1, len(hops)):
        assert hops[i].parent_hop_id == hops[i - 1].token_id
    # acyclic: every token id and every agent id unique.
    assert len({h.token_id for h in hops}) == len(hops)
    # narrowing: fuel only shrinks root -> leaf.
    fuels = [h.budget.fuel_max for h in hops]
    assert fuels == sorted(fuels, reverse=True)
    assert fuels[0] > fuels[1] > fuels[2] > 0
    # scope is the same capability each hop (read) — narrowing is expressed
    # through the resource namespace + budget, which tighten each hop.
    assert hops[0].scope == Scope.MEMORY_GET
    assert hops[1].scope == Scope.MEMORY_GET
    assert hops[2].scope == Scope.MEMORY_GET
    # resource namespace becomes more specific each hop.
    assert hops[0].resource.startswith("mem.researcher")
    assert hops[1].resource.startswith("mem.analyst")
    assert hops[2].resource.startswith("mem.scribe")


def test_root_and_analyst_tokens_also_verify(chain):
    sc = chain["scribe"]
    exp = chain["exp"]
    va = chain["verify_at"]
    assert cc.verify_assert(chain["root"], va, exp) is chain["root"]
    # analyst token: root -> analyst line also verifies.
    analyst_token = chain["analyst"]
    assert analyst_token.chain[0].agent_id == "researcher"
    assert analyst_token.chain[-1].agent_id == "analyst"
    assert cc.verify_assert(analyst_token, va, exp) is analyst_token


def test_replay_reconstructs_chain(chain):
    r = cc.run_replay(chain["scribe"])
    assert r["matches_chain"] is True
    assert len(r["records"]) == 3  # one delegate record per hop
    assert r["reconstructed"][0][1] == "researcher"
    assert r["reconstructed"][1][1] == "analyst"
    assert r["reconstructed"][2][1] == "scribe"


def test_replay_rejects_corrupt_record(chain):
    assert cc.run_replay_corrupt(chain["scribe"]) is True


def test_reject_forged_hop(chain):
    sc = chain["scribe"]
    forged_hop = _dc_replace(sc.chain[2], sig=bytes(64))
    forged = cc._chain_from_root([sc.chain[0], sc.chain[1], forged_hop])
    with pytest.raises(cc.DelegationViolation):
        cc.verify(forged, logical_now_unix=chain["verify_at"], keys=chain["keys"])


def test_reject_expired(chain):
    # A token expiring exactly at the logical clock is expired (strict check).
    sc = chain["scribe"]
    with pytest.raises(cc.DelegationViolation):
        cc.verify(sc, logical_now_unix=chain["exp"], keys=chain["keys"])


def test_reject_broken_chain(chain):
    sc = chain["scribe"]
    severed = cc._chain_from_root([sc.chain[0], sc.chain[2]])
    with pytest.raises(cc.DelegationViolation):
        cc.verify(severed, logical_now_unix=chain["verify_at"], keys=chain["keys"])


def test_reject_budget_growth(chain):
    sc = chain["scribe"]
    grown_hop = cc._issue_hop(
        "scribe", parent=sc.chain[1], scope=Scope.MEMORY_GET,
        resource="mem.scribe.final_report",
        budget=Budget(fuel_max=999, memory_pages_max=64, wall_time_ms=5000),
        expires_at_unix=sc.expires_at_unix,
    )
    grown = cc._chain_from_root([sc.chain[0], sc.chain[1], grown_hop])
    with pytest.raises(cc.DelegationViolation):
        cc.verify(grown, logical_now_unix=chain["verify_at"], keys=chain["keys"])


def test_end_to_end_runs(chain):
    cc.run_replay(chain["scribe"])
    rej = cc.run_rejections(chain["scribe"], chain["keys"], chain["verify_at"])
    assert len(rej) == 4
    assert all(isinstance(label, str) and isinstance(msg, str) for label, msg in rej)


# small local alias so the test stays readable without importing stdlib
def _dc_replace(obj, **kw):
    from dataclasses import replace
    return replace(obj, **kw)
