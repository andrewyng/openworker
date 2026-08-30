#!/usr/bin/env python3
"""KB ingest 2026-08-27 — ingest today's automation outputs.

The `default` collection had been wiped (only an empty vectorless
`fdot_surplus_documents` remained); this run recreated `default` with the
named 384-dim Cosine vector `fast-all-minilm-l6-v2` and stores today's findings.

Embedding: fastembed, matching collection `default` so stored points are
retrievable by the named vector. Dedup: POST /points/query (cosine, threshold
0.72, named-vector form) BEFORE PUT — a point already stored is skipped.
READERS key on payload "document" (NOT the qdrant-store tool's "information").
"""
import json
import urllib.request
import uuid

BASE = "http://127.0.0.1:6333"
COLL = "default"
VEC_NAME = "fast-all-minilm-l6-v2"
J = "Knowledge base — ingest yesterday's findings"
D = "2026-08-27"

PB = "/home/iconbaypark2900/OpenWorker/__task__task-1b2c4d3f13/papers-2026-08-27.md"
BR = "/home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-27.md"
RA = "/home/iconbaypark2900/OpenWorker/__task__task-717e4360ea/repo-activity-2026-08-27.md"
MB = "/home/iconbaypark2900/OpenWorker/__task__task-c685e22a0b/morning-briefing-2026-08-27.md"

# (subject, information, kind, focus, source)
# information MUST be self-contained (it is retrieved alone). Include the id.
ITEMS = [
    # ---- papers-2026-08-27.md (digest says all listed are new vs 08-26) ----
    ("Distributed Trotterization optimal entanglement cost",
     "arXiv:2608.25896 (Feng, Sun, Xiao, Zhao) shows product-formula nonlocal rotations get weaker but more numerous as accuracy rises, so distributed quantum simulation's per-gate teleportation entanglement cost *diverges in the high-accuracy limit* under the standard chain; a repeat-until-success protocol instead makes entanglement consumption adaptive to interaction strength, so total entanglement scales *linearly with evolution time and independent of Trotter error* with a matching quantum-communication lower bound proving the time scaling is optimal — one of the rare product-formula cost results that settles the scaling and proves it tight, directly relevant to how an OpenEvolve Phase 3 QM tier budgets distributed simulation.",
     "paper", "on", PB),
    ("Certified Decoding of Quantum LDPC Codes",
     "arXiv:2608.25545 (Krishnamoorthy et al.) models degenerate maximum-likelihood decoding as probabilistic inference in a Markov random field and builds two decoders: a sampling one attaching a certificate of optimality to each decision (paired-bootstrap, or an exact proof when composed with constant-factor estimators like WISH) and a region-based Bethe-free-energy one that reproduces exact ML and makes exact degenerate ML decoding of the [[72,12,6]] bivariate-bicycle code feasible — so the decode cost, the dominant overhead in any practical QEC layer, is made rigorously auditable and flags exactly the syndromes on which any fast decoder should be distrusted.",
     "paper", "on", PB),
    ("Asymptotically Optimal Purification of Noisy Unitary Channels",
     "arXiv:2608.26061 (Niwa, Yoshida, Murao) derives the optimal query complexity Theta(d^2 p / epsilon) for purifying an unknown d-dimensional unitary channel behind depolarizing noise to infidelity epsilon — beating a naive store-then-purify approach and attaining it with a concrete SU(d)-covariant parallel strategy — sharpening how many noisy-gate uses the certification of 'how good is this gate actually is' fundamentally needs.",
     "paper", "adjacent", PB),
    ("TailSFT Filtered fine-tuning improves post-training performance",
     "arXiv:2608.25756 (Malladi, Jelassi, Foster, Ash, Krishnamurthy) finds RL post-training is most effective when the SFT checkpoint it starts from is already RL-ready, so the lever is the intermediate checkpoint: TailSFT deliberately filters out already-fit sequences during SFT and focuses on the under-modeled tail, raising pass@16 by up to 17% absolute on OLMo-3 7B at minimal overhead, with those higher-coverage checkpoints translating to up to 4% absolute pass@1 gains in subsequent GRPO runs — a stage-aware training philosophy directly suited to training/fine-tuning a reasoning model on a tight hardware budget.",
     "paper", "on", PB),
    ("Reflection Steering disentangling reflection from reasoning",
     "arXiv:2608.25542 (Hu, Wen, Liu, Shen, Yang) is a training-free method that, across matched reasoning settings, contrasts reflective vs. non-reflective hidden states per layer, denoises the reflection direction with PCA, and orthogonalizes it against the general-reasoning direction — so reflection is turned down without touching reasoning, yielding 16.9% fewer reasoning tokens on average with a bounded deployment-time strength parameter alpha to trade token savings for accuracy/stability, and orthogonalizing reflection away from reasoning (rather than adding a label-derived mean-difference vector) is the genuinely new fix.",
     "paper", "on", PB),
    ("How Much Rank Does LoRA Need rank-error bounds for attention",
     "arXiv:2608.26052 (Conangla Planes) gives rank-error bounds for transformer-attention LoRA rather than just empirical sweep plots, turning the 'pick a LoRA rank' heuristic into a quantified bound computable for a given attention head — removing the guesswork from running parameter-efficient fine-tuning on constrained hardware, kept on-shelf for when the fleet needs to LoRA-fine-tune on-device.",
     "paper", "adjacent", PB),
    ("FrontierChallenge evaluating scientific workflow completion",
     "arXiv:2608.24979 (Su, Feng, Chen, et al., submitted 2026-08-25) delivers a benchmark of 300 end-to-end scientific workflows (97 released, spanning quantum chemistry, molecular dynamics, materials, analytical chemistry, life science, electrochemistry) evaluated across 12 frontier models and 3 agent scaffolds; the best configuration completed only 20 of 97 tasks (20.6% Pass Rate) but 75.5% of Claude Code trajectories that did NOT fully complete still ended with a language claim of completion, and high partial scores (Avg. Scores up to 94.9) translated to near-zero Pass Rates in analytical chemistry and electrochemistry — the single strongest empirical anchor yet for the 'the model says it's done but it isn't' silent-failure failure any unattended autonomous-research loop must detect.",
     "paper", "on", PB),
    ("KOPE experience graph memory for hardware kernel optimization",
     "arXiv:2608.25570 (Chen, Hou, Wu, et al.) is a concrete 'the model is fixed; the harness improves' instantiation: an LLM agent in hardware kernel optimization records trajectories with correctness/performance feedback in an Experience Graph Memory and retrieves the relevant experience under a fixed token budget, achieving a 1.54x geometric-mean speedup over the strongest baseline (CANNBot) under the same GLM-5.2 setting, with Experience Graph Memory alone lifting the full-suite pass rate from 55.2% to 84.6% — a second domain's confirmation that structured external memory is the precondition for safe recursive improvement on fixed weights.",
     "paper", "on", PB),
    ("SwarmWorld stigmergic technological evolution in societies of LLM agents",
     "arXiv:2608.26081 (Pal, Wang, Buehler) has homogeneous LLM agents self-organize into evolving technological societies with no assigned roles or recipes, sharing persistent artifacts and executable controllers under a deterministic simulator that keeps running after the agents are removed; it finds shared societies build broader, more resilient technological portfolios than strong best-of-N isolated search but isolated search still wins for the single strongest artifact (a genuine cooperation/search tradeoff), that reuse begins through physical observation rather than communication, and explicit cultural mechanisms help organization but their payoff depends on timescale — the strongest recent demonstration that a decentralized multi-agent research loop can outperform isolated search on portfolio diversity while the single best individual solution can come from an isolated agent.",
     "paper", "on", PB),
    # ---- breakage-2026-08-27.md ----
    ("ollama v0.33.1 MLX-Metal housekeeping release",
     "Ollama released v0.33.1 on 2026-08-26 18:09 UTC (3 commits: MLX Qwen3.8-Flash-Next support, cmake external-compat-patch idempotency, mlxrunner structured output + slow-storage GPU-timeout avoidance) on top of v0.33.0; the ROCm Linux wheel ollama-linux-amd64-rocm.tar.zst is still present in the release, and the only item touching the workstation ROCm/Strix-Halo watch family (cmake: make external compat patches idempotent) is an unverified build-time hardening commit, so 0.33.1 is not a new break vector on this box and the standing instruction stands: on a docker compose pull to >=0.33.1 check available GPU memory (the open containerized-Strix-Halo VRAM-reporting issue from 2026-08-26 remains unresolved).",
     "breakage", "on", BR),
    # ---- repo-activity-2026-08-27.md ----
    ("GitHub MCP gateway unreachable fetch failed 0 of 10",
     "The GitHub MCP gateway that the repo-activity automation reaches via github-list_commits was completely unreachable on 2026-08-27: every call to all 10 tracked repos (including liaison-agentSystem and dcode-stack) failed with 'MCP error -32603: fetch failed', a gateway-level outage distinct from the 2026-08-24/26 'Authentication Failed: Bad credentials' credential failure, leaving 0 of 10 repos verifiable that run and 0 new commits reportable; the shell path still has no GITHUB_TOKEN / gh auth so per-repo data could not be obtained by any path.",
     "breakage", "on", RA),
    # ---- morning-briefing-2026-08-27.md (flagship AI-industry movement, flagged adjacent) ----
    ("Flash-class models GLM-5.3-Flash and Qwen3.8-Flash-Next",
     "Two frontier 'Flash'-class models landed on 2026-08-26 — GLM-5.3-Flash from Z.ai and Qwen3.8-Flash-Next from Qwen — the first fresh frontier entries since DeepSeek V4 Flash Vision on 2026-08-22, per the AI Release Tracker's newest-first list; attribution is limited to date+provider (benchmark/context specifics came back empty, so no specs are asserted) with no new output from OpenAI, Anthropic, Google, Meta, or xAI in the interim.",
     "news", "adjacent", MB),
]


def qdrant(method, path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read().decode())
    if out.get("status") not in ("ok", "completed"):
        raise RuntimeError(f"{path}: {out}")
    return out["result"]


# Dedup + store, with per-item failure isolation (one bad item does not abort the run).
results = {"stored": [], "skipped": []}
for i, (subject, info, kind, focus, src) in enumerate(ITEMS):
    try:
        emb = next(iter(__import__("fastembed").TextEmbedding().embed([info])))
        vec = [float(x) for x in emb]
        assert len(vec) == 384, f"bad dim {len(vec)}"
    except Exception as e:
        results["skipped"].append({"subject": subject, "reason": f"EMBED FAIL {type(e).__name__}: {e}"})
        continue
    try:
        hits = qdrant("POST", f"/collections/{COLL}/points/query", {
            "query": vec, "using": VEC_NAME, "limit": 3,
            "score_threshold": 0.72, "with_payload": True,
        })
        hits = hits if isinstance(hits, list) else (hits or {}).get("points", [])
    except Exception as e:
        results["skipped"].append({"subject": subject, "reason": f"QUERY FAIL {type(e).__name__}: {e}"})
        continue
    if hits:
        top = hits[0]
        p = top.get("payload", {})
        results["skipped"].append({"subject": subject, "score": round(top["score"], 3),
                                   "stored_info": (p.get("document") or "")[:120]})
        continue
    try:
        qdrant("PUT", f"/collections/{COLL}/points", {"points": [{
            "id": str(uuid.uuid4()),
            "vector": {VEC_NAME: vec},
            "payload": {
                "document": info,
                "metadata": {
                    "job": J, "date": D, "cadence": "daily",
                    "focus": focus, "kind": kind, "source": src,
                },
            },
        }]})
        results["stored"].append({"subject": subject, "focus": focus, "kind": kind})
        print(f"STORED {subject!r}")
    except Exception as e:
        results["skipped"].append({"subject": subject, "reason": f"STORE FAIL {type(e).__name__}: {e}"})

cnt = qdrant("GET", f"/collections/{COLL}", {})
print("COLLECTION", json.dumps(cnt.get("result", {}).get("points_count"), ensure_ascii=False), "points")
print("STORED", len(results["stored"]), "SKIPPED", len(results["skipped"]))
with open("/home/iconbaypark2900/openworker-tasks/__task__task-ecad448aa3/ingest_kb.json", "w") as f:
    json.dump({"results": results, "collection": cnt}, f, ensure_ascii=False, indent=2)
