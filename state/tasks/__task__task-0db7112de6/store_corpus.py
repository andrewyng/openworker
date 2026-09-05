"""Store 2026-08-31 weekly corpus into local Qdrant (collection: default).

Schema mirrors prior runs (store_corpus.py):
  payload = {"document": str, "metadata": {topic, arxiv_id, date, source:"arxiv-weekly"}}
Vector: 384-dim MiniLM (fastembed) under name "fast-all-minilm-l6-v2".

Dedup: scroll existing points, skip if arxiv_id already present OR title text
      already in a stored document. All 10 candidates are new vs the 11 stored
      IDs (2608.24979..2608.27351), but we re-check defensively.
"""
import json, urllib.request, uuid, fastembed

QDRANT, COLL, VECTOR_NAME = "http://localhost:6333", "default", "fast-all-minilm-l6-v2"
DATE = "2026-08-31"

T_QUANTUM   = "Quantum computing: algorithms, error correction, hardware, quantum ML"
T_MATERIALS = "Materials science: DFT, ML interatomic potentials, crystal structure generation"
T_MLSYSTEMS = "ML systems: inference efficiency, quantization, MoE architectures, long context"
T_AGENTS    = "Agents: tool use, retrieval, evaluation, multi-agent systems"

# (topic, id, published_date, tag ON-FOCUS/ADJACENT, title, one-sentence finding, read 2026-08-31)
PAPERS = [
    (T_AGENTS,  "2608.27969", "2026-08-28", "ON-FOCUS", "openJiuwen: Beyond Static Harnesses for Long-Horizon Coding Agents",
     "An open-source agent harness built for composability (shared execution substrate plus Rail-based capability composition across single agents, delegated sub-agents, and Swarm Flow) and runtime adaptivity, where evolving runtime evidence dynamically rewrites context, feedback, and task control. Hits 82.6% on SWE-bench Verified and 87.19% on Terminal-Bench 2.1, exceeding the strongest selected official-leaderboard point estimates by 3.4 and 3.39 points - a live reference for building and maintaining a verifiable long-horizon coding harness."),
    (T_AGENTS,  "2608.28497", "2026-08-28", "ON-FOCUS", "On the Maintenance and Co-evolution of Agent Plugins: An Empirical Study of Claude Code Plugin Marketplaces",
     "Empirical study of 1,926 repos, 8,351 plugins, and 77,773 commits finds agent plugins are predominantly feature-driven with an OSS feature-commit rate twice that of traditional software, and that natural-language instruction files co-evolve with their implementation scripts at above-chance rates (78% functionally coupled) - a new maintenance-dependency class not seen in traditional software engineering."),
    (T_AGENTS,  "2608.28447", "2026-08-28", "ON-FOCUS", "Learning to Use Tools: Reinforcement Learning for Tool-Integrated Mathematical Reasoning",
     "Teaching an LLM calculator tool-calling via SFT then on-policy RL (RLOO/RLOO++/GRPO/DAPO) with only verifiable final-answer rewards on the Countdown task: tool integration cuts arithmetic and verification errors, and Tool-DAPO lifts pass@1 from 35.8% to 66.0%; includes a fresh 1,024-problem held-out benchmark with no training overlap."),
    (T_AGENTS,  "2608.26385", "2026-08-26", "ON-FOCUS", "Why RAGs Hallucinate: Penalty-Aware Evaluation of Retrieval-Augmented Generation Systems with Knowledge-Gap Canaries",
     "Shows volume-based scoring rewards guessing (answering everything outscores declining when knowledge is unavailable) and introduces penalty-aware scoring (correct +1, wrong -4, abstain 0) with knowledge-gap canaries and a retrieval/generation/abstention failure-attribution pipeline that reorders commercial RAG systems by roughly sixfold on canary-violation rates; all data released for audit."),
    (T_MLSYSTEMS, "2608.28044", "2026-08-28", "ON-FOCUS", "Characterization of Request and Token Energy Costs for LLM Inference Workloads on GPU Platforms",
     "Argues token-normalized energy is an incomplete serving metric because GPU energy accrues over the inference window: a decomposed model with fixed prefill, fixed setup cost, and per-token marginal energy shows output length and batching cut token energy while total request energy can rise, and MoE widens the dense-vs-MoE energy gap at low concurrency - so energy-aware serving should optimize request energy and token energy jointly."),
    (T_MLSYSTEMS, "2608.28444", "2026-08-28", "ON-FOCUS", "Sliding-window beats linear attention",
     "Shows post-training-free Sliding Window Attention with sinks performs as well as or better than post-trained Linear Attention across several LLMs, and massively outperforms it on long-context tasks (needle-in-haystack and BABILong at 2-10x) while needing no post-training and using less memory - arguing for switching to SWA to cut inference memory cost rather than retrofitting linear attention."),
    (T_MLSYSTEMS, "2608.28003", "2026-08-28", "ON-FOCUS", "A Method for Layer Bit-Width Allocation in LLM Quantization via Performance Maximization Under a Quality-Degradation Constraint",
     "Formulates per-layer quantization bit allocation for Gemma-3-1B in TensorRT-LLM as latency maximization under a quality-loss budget using a prior layer-sensitivity profile, finding short-context Attention quantization can actually slow execution while FFN and lm_head benefit, and reaching up to 19.1% latency reduction with negligible quality degradation."),
    (T_MATERIALS, "2608.28100", "2026-08-28", "ADJACENT", "uMOF: A Universal Database, Benchmark, and Machine Learning Interatomic Potentials for Metal-Organic Frameworks",
     "Releases the largest r2SCAN-D4 DFT dataset for MOFs (85,524 configs, 19,950 frameworks, 79 elements) plus a literature-mined 3,986-verified benchmark and two MACE-foundation universal MLIPs; on gas-adsorption enthalpies via Widom insertion they beat even MOF-specialized baselines trained on datasets up to three orders of magnitude larger, cutting error by more than 80% to within experimental uncertainty."),
    (T_MATERIALS, "2608.26962", "2026-08-27", "ADJACENT", "Packora: Systematic Design for Generative Molecular Crystal Structure Prediction",
     "A flow-based generative model jointly predicting atomic coordinates and lattice for molecular crystal structure prediction (multi-component and organometallic), evaluated with generation ranking to isolate generator quality. Achieves the best matched-budget coverage across six benchmarks, higher experimental-form recovery, and faster ranking convergence via cacheable pairwise reasoning and balanced pairwise/single representation scaling."),
    (T_QUANTUM, "2608.26272", "2026-08-26", "ADJACENT", "Fault-tolerant quantum computation cannot be achieved with constant spacetime overhead",
     "Proves an unavoidable logarithmic contribution to cumulative spacetime overhead for preserving quantum memory under general adaptive protocols even under an optimistic noise model, gives a positive-rate CSS code attaining the bound, and derives circuit-size bounds for subsystem spacetime codes - a fundamental resource limit on fault-tolerant quantum computing."),
]

# dedup: scroll existing corpus
req = urllib.request.Request(QDRANT + "/collections/" + COLL + "/points/scroll",
                             data=json.dumps({"limit": 400, "with_payload": True}).encode())
pts = json.load(urllib.request.urlopen(req))["result"]["points"]
stored_arxiv, stored_docs = set(), []
for p in pts:
    d = p["payload"]
    aid = (d.get("metadata") or {}).get("arxiv_id")
    if aid:
        stored_arxiv.add(aid)
    doc = d.get("document") or ""
    stored_docs.append(doc)
    for tok in doc.split():
        if tok.startswith("arXiv:"):
            stored_arxiv.add(tok[6:])
print("existing points:", len(pts), "stored arxiv ids:", len(stored_arxiv))

to_store, skipped = [], []
for topic, aid, date, tag, title, finding in PAPERS:
    if aid in stored_arxiv or any(title.lower() in dl.lower() for dl in stored_docs):
        skipped.append((aid, title, "duplicate"))
    else:
        info = title + ". " + finding + ". arXiv:" + aid
        to_store.append({"id": str(uuid.uuid4()),
                         "payload": {"document": info,
                                     "metadata": {"topic": topic, "arxiv_id": aid,
                                                  "date": date, "source": "arxiv-weekly"}}})

if skipped:
    print("SKIPPED (duplicate):")
    for a, t, r in skipped:
        print("  ", a, t, r)

if not to_store:
    raise SystemExit("nothing to store - all duplicates; aborting")

# embed + PUT upsert
model = fastembed.TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')
vectors = list(model.embed([t["payload"]["document"] for t in to_store]))
body = json.dumps({"points": [
    {"id": t["id"], "vector": {VECTOR_NAME: [float(x) for x in v]}, "payload": t["payload"]}
    for t, v in zip(to_store, vectors)], "wait": True}).encode()
req = urllib.request.Request(QDRANT + "/collections/" + COLL + "/points", data=body, method="PUT",
                             headers={"Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req, timeout=60))
print("upsert response:", json.dumps(resp)[:300])

# verify
req = urllib.request.Request(QDRANT + "/collections/" + COLL + "/points/scroll",
    data=json.dumps({"filter": {"must": [{"key": "metadata.source", "match": {"value": "arxiv-weekly"}}]},
                     "with_payload": True, "limit": 400}).encode())
now = json.load(urllib.request.urlopen(req))["result"]["points"]
this_run = [p for p in now if (p["payload"].get("metadata") or {}).get("date") == DATE]
ids_now = [p["payload"]["metadata"]["arxiv_id"] for p in this_run]
print("arxiv-weekly points now:", len(now), "this run stored:", len(this_run))
print("this-run IDs:", ", ".join(ids_now))
