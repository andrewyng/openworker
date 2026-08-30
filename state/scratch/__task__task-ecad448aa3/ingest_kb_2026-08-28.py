#!/usr/bin/env python3
"""KB ingest 2026-08-28 - ingest the 5 automation outputs from the last 24h.

Dedup: POST /collections/default/points/query (cosine, threshold 0.72,
named vector fast-all-minilm-l6-v2) BEFORE PUT - a point already stored
is skipped. READERS key on payload "document". The MCP qdrant tools are
not in this session's toolset, so the direct HTTP path (used since the
2026-08-27 run) is the retrieval path.
"""
import json, time, urllib.request, uuid

BASE = "http://127.0.0.1:6333"
COLL = "default"
VEC_NAME = "fast-all-minilm-l6-v2"
D = "2026-08-28"
J = "Knowledge base — ingest yesterday's findings"

PB = "/home/iconbaypark2900/OpenWorker/__task__task-1b2c4d3f13/papers-2026-08-28.md"
BR = "/home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-28.md"
MB = "/home/iconbaypark2900/OpenWorker/__task__task-c685e22a0b/morning-briefing-2026-08-28.md"
JJ = "/home/iconbaypark2900/OpenWorker/__task__task-c6cf366b90/jobs-2026-08-28.md"

# (subject, information, kind, focus, source) - self-contained "information", id inside.
ITEMS = [
    # ---- papers-2026-08-28.md (digest: all listed are new vs the 2608.26081 08-27 top) ----
    ("OSTRE: Find Rows and Decode for quantum expander codes",
     "arXiv:2608.27211 (Dimiter Ostrev, submitted 2026-08-27) adapts the classical 'Find Erasures and Decode' algorithm to quantum expander codes so decoding runs in linear time and logarithmic parallel depth, sidestepping stabilizer-generator subset bookkeeping, and on reported comparisons corrects more errors demanding less expansion. It is a clean algorithmic cost result for the fault-tolerant QEC *decode* step (not a new code), in the same family as 08-27's 'Certified Decoding of QEC', and it hands back the load-bearing decoder-cost floor the OpenEvolve Phase 3 QM-budget question asked for — though 'corrects more errors' is not a full threshold statement, so it is a strong engineering result to fold into a budget estimate, not yet a logical-qubit bet.",
     "paper", "on", PB),

    ("Evolution Strategies beat GRPO via broader reasoning coverage",
     "arXiv:2608.27351 (Ba, Zheng, Xie, et al., submitted 2026-08-27) explains why Evolution Strategies beat GRPO in LLM reasoning post-training: ES produces broader reasoning coverage (verifier-projected Jensen–Shannon diversity → higher Pass@K) while GRPO collapses in entropy, and despite large whole-model parameter drift ES's gains concentrate in a sparse subset of larger-magnitude updates with held-out evaluation showing NO catastrophic forgetting. It repositions ES as a distinct reasoning paradigm (not a cheaper GRPO stand-in) and is directly actionable for a constrained-hardware local fleet — reasoning gains at smaller memory budget on a sparse, forgetting-free update, and a GRPO-ES combo could inherit GRPO's Pass@1 plus ES's Pass@K.",
     "paper", "on", PB),

    ("Circuit Condensation: post-train to concentrate a behavior's causal circuit",
     "arXiv:2608.27254 (Sai Adith Senthil Kumar, submitted 2026-08-27) post-trains a model to concentrate a behavior's mechanistic circuit into a smaller causal graph (prune low-attribution edges, fit a low-rank adapter, keep the cut only if task and general capability survive) — 8.1x smaller on average, up to 316x across eight models. The diagnostic for WHY it shrinks: running the search with NO weight updates produces larger circuits in 29 of 32 settings, so the weight update, not the search, does the work; and an indirect-object-identification test isolates a sufficient sub-circuit (24 heads, 17 documented) that matches the frozen baseline's next-token distribution and error pattern. It embodies the record's 'produce an auditable minimal verifiable sub-mechanism over a sprawling reconstruction' impulse (Granqvist, Recuris, FrontierChallenge).",
     "paper", "on", PB),

    ("AgentFold: closed-loop agentic search for protein folding model design",
     "arXiv:2608.26747 (Liu, Chen, Cao, et al., submitted 2026-08-27) tests 'can an LLM agent autonomously improve a large tightly-coupled scientific ML system' with AgentFold: closed-loop search over executable code variants on a 2,000+ line ESMFold-style codebase, proposing, implementing, debugging and evaluating variants while storing BOTH successful and failed interventions in structured memory under an MCTS-style policy. Over ~80 variants, ~5,000 GPU-hours and ~170M LLM tokens it improves best lDDT by 7.5% over independent Codex proposals and beats a random-search control. The traces show stable gains come from early soft learnable priors and gated refinement while direct geometric perturbations/geometry-conditioned feedback often destabilize — the real-world analogue of OpenEvolve's own self-improving-agent loop for molecular model design, confirming that external structured memory (successes AND failures) is the precondition for a working loop and that the architecture of the improvement step matters more than raw search breadth.",
     "paper", "on", PB),

    ("HarnessLens: behavior-aware verification for agent-harness evolution",
     "arXiv:2608.27311 (Xu, Zhang, Chen, et al., submitted 2026-08-27) makes agent-harness evolution verification-efficient: HarnessLens derives candidate harness edits from execution trajectories and selectively verifies each candidate only on behavior-relevant tasks through an attributable-evidence gate, exploring the task space and user-configurable harness components jointly. Across three harnesses and four benchmarks it raises held-out performance 7.6–13.6% while using far less evaluation budget than baselines — a concrete budget-aware harness-evolution loop that improves the harness without touching model weights, a direct on-topic confirmation of the record's 'the structure around the model is the load-bearing lever' prior (KOPE, AgentFold) now in the agent-harness domain.",
     "paper", "on", PB),

    # ---- breakage-2026-08-28.md ----
    ("unsloth v0.1.804-beta shipped, adapter-additive, no new gfx1151 break",
     "unsloth shipped v0.1.804-beta on 2026-08-27 13:09 UTC (tag title 'Qwen3.8-Flash-Next + GLM-5.3-Flash'), up from v0.1.803-beta (2026-08-25), verified via the releases API as a model-adapter-additive release with no gfx1151/AMD-specific break. Because unsloth is the package on this box whose 'pip install -U unsloth' has a documented tendency to pull the wrong torch (CUDA instead of ROCm), the practical action on any fresh install stays the standing guardrail from 2026-08-26: re-install torch from the gfx1151 nightly index and check torch.version.hip, set BNB_ROCM_VERSION=71 if bitsandbytes is pulled in, and set UNSLOTH_MOE_BACKEND=native_torch if an MoE-probe import runs. (dependency/breakage watch 2026-08-28)",
     "breakage", "on", BR),

    # ---- jobs-2026-08-28.md ----
    ("UnitedHealthcare Senior AI/ML Engineer — new top match",
     "UnitedHealthcare posted a new Senior AI/ML Engineer (Remote / Hybrid in MN/DC) ~4 days before 2026-08-28 — a top job-pipeline match: the posting names RAG + LLM + NLP + Azure ML stack + vector DB + MLOps + healthcare-underwriting, which maps almost 1:1 onto the candidate's Mutual of Omaha insurance-RAG system and SLM-agent ETL work. The fresher 'Sr' variant waives the degree via 'or equivalent experience' and asks only 2+ yrs Gen AI, met by the resume; the honest gap is the cloud being Azure ML vs the resume's GCP Vertex (a cloud-agnostic RAG + MLOps framing pre-empts it). Drafted as the highest-fit, top-priority role, but per spec drafts only — no application submitted. (jobs-2026-08-28)",
     "job", "on", JJ),

    ("Laurel Applied AI Engineer — strong reach, 7+yr gate",
     "Laurel posted a new Applied AI Engineer (Remote, US) at $254k–$328k total comp per aggregator goremotejob.com — a strong structural LLM-app fit (LLM application engineering + evaluation metrics, Python/TypeScript, full deployment-lifecycle ownership) but a genuine reach: the 7+ yrs SE bar is the honest gate and the candidate's ~2.5 yrs production ML falls short, and Node.js/NestJS/MongoDB/message-queues are not evidenced (the degree is waivable). Drafted as a reach, per spec drafts only — no application submitted. (jobs-2026-08-28)",
     "job", "on", JJ),

    ("CHEQ AI Engineer — near-1:1 stack match but almost certainly in-office",
     "CHEQ posted a new AI Engineer (~3 days before 2026-08-28) — a nearly 1:1 stack match (GCP-first, LangChain/LlamaIndex, RAG, embeddings, + PyTorch/TensorFlow with FastAPI/Django backend) onto the candidate's GCP Vertex + insurance-RAG production work, but almost certainly in-office (Tel Aviv / Bengaluru) so a US-based applicant cannot fill it; recorded as a reach and NOT to be applied to unless a remote-US variant is confirmed, with the location caveat made explicit in the draft. Per spec drafts only — no application submitted. (jobs-2026-08-28)",
     "job", "on", JJ),

    ("Horizon AI-for-Science Fellowship — best match but 2026 cycle closed",
     "Horizon's AI-for-Science Fellowship (100% employment, 'AI forward deployed to DOE national labs' — Idaho/Brookhaven/Princeton Plasma Physics; prorated up to $200k/yr; core bar 2+ yrs building AI models/agents; no clearance, no scientific background required) is arguably the single best-matching fellowship on the map for the OpenEvolve Phase 3 QM/chemistry + Hybrid-QML work, but its 2026 cycle ALREADY CLOSED on 2026-07-31 (28 days before 2026-08-28); the program (Horizon + Renaissance Philanthropy + SeedAI + Fulcrum) is clearly annual, so it should be watched for the next cycle rather than treated as live. (jobs-2026-08-28)",
     "job", "on", JJ),

    # ---- ADJACENT (capped at 3) ----
    ("LLMs can design near-optimal OR algorithms",
     "arXiv:2608.27296 (Jackie Baek, submitted 2026-08-27) studies frontier LLMs as algorithm designers over well-specified operations-research problems (inventory control, queueing networks, assortment optimization): with a single untuned prompt and a fixed-compute Python sandbox the strongest model (gpt-5.6-sol) matches or outperforms the best existing method on almost every instance — even at 'level 2' where the model commits to a fixed algorithm before seeing any evaluation instance. Frontier LLMs can already be a serious empirical baseline for algorithm design, and performance improves sharply across models released in under 8 months — the 'models that improve research / automated research' watch signal. ADJACENT (no algorithm-design project in FOCUS.md drives it, but it bears on the RSI / algorithmic-superintelligence lineage).",
     "paper", "adjacent", PB),

    ("US weighing 7.5% tariff on Chinese goods over 'overcapacity'",
     "On 2026-08-28 the US opened an investigation into alleged Chinese 'overcapacity' and was weighing a new 7.5% tariff on Chinese goods, with Beijing opposing the probe and reserving the right to act — a fresh China-trade escalation on top of the sweeping late-July tariff action, and a supply-chain/policy signal for anyone with China exposure. The Livemint live-updates wire (via Reuters) was directly opened; the countermeasure specifics are search-result only, not fully fetched. (morning briefing 2026-08-28)",
     "news", "adjacent", MB),

    ("Cambridge ERA:AI Fellowship Winter 2027 — reopened",
     "The Cambridge ERA:AI Fellowship (Winter 2027, starts 18 Jan 2027, 10-week research programme, £10,000 stipend + visa support) reopened with a 2026-09-13 deadline and broad eligibility ('Anyone 18 or older, talent-first') across Technical (evaluation/interpretability/robustness/alignment) and Governance streams. ADJACENT — it does not bear on the named OpenEvolve/job-pipeline/quantum-corpus threads, and a US-based applicant faces UK logistics, so it is recorded because it is the freshest genuinely-open fellowship this run but is not a fit if US-only is required. (jobs-2026-08-28)",
     "job", "adjacent", JJ),
]


def qdrant(method, path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read().decode())
    if out.get("status") not in ("ok", "completed"):
        raise RuntimeError(f"{path}: {out}")
    return out["result"]


def embed(text):
    for attempt in range(3):
        try:
            import fastembed
            emb = next(iter(fastembed.TextEmbedding().embed([text])))
            vec = [float(x) for x in emb]
            assert len(vec) == 384, f"bad dim {len(vec)}"
            return vec
        except Exception as e:
            time.sleep(2 + attempt)
            last = e
    raise last


results = {"stored": [], "skipped": []}
for i, (subject, info, kind, focus, src) in enumerate(ITEMS):
    try:
        vec = embed(info)
    except Exception as e:
        results["skipped"].append({"subject": subject, "reason": f"EMBED FAIL {type(e).__name__}: {e}"})
        continue
    # dedup: find-first against live points
    try:
        hits = qdrant("POST", f"/collections/{COLL}/points/query", {
            "query": vec, "using": VEC_NAME, "limit": 3,
            "score_threshold": 0.72, "with_payload": True})
        hits = hits if isinstance(hits, list) else (hits or {}).get("points", [])
    except Exception as e:
        results["skipped"].append({"subject": subject, "reason": f"QUERY FAIL {type(e).__name__}: {e}"})
        continue
    if hits:
        top = hits[0]
        p = top.get("payload", {})
        results["skipped"].append({"subject": subject, "reason": "DUP cosine=%.3f" % top["score"],
                                   "stored_info": (p.get("document") or "")[:140]})
        continue
    try:
        qdrant("PUT", f"/collections/{COLL}/points", {"points": [{
            "id": str(uuid.uuid4()), "vector": {VEC_NAME: vec},
            "payload": {"document": info, "metadata": {
                "job": J, "date": D, "cadence": "daily",
                "focus": focus, "kind": kind, "source": src}}}]})
        results["stored"].append({"subject": subject, "focus": focus, "kind": kind})
        print(f"STORED {subject!r} ({focus}/{kind})")
    except Exception as e:
        results["skipped"].append({"subject": subject, "reason": f"STORE FAIL {type(e).__name__}: {e}"})

cnt = qdrant("GET", f"/collections/{COLL}", {})
print("COLLECTION_POINTS", json.dumps(cnt.get("result", {}).get("points_count"), ensure_ascii=False))
print("STORED", len(results["stored"]), "SKIPPED", len(results["skipped"]))
with open("/home/iconbaypark2900/openworker-tasks/__task__task-ecad448aa3/ingest_kb_2026-08-28.json", "w") as f:
    json.dump({"results": results, "collection": cnt.get("result", {})}, f, ensure_ascii=False, indent=2)
