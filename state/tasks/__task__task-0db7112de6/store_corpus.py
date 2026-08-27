"""Store this week's corpus into local Qdrant (collection: default).

Schema mirrors existing point:
  payload = {"document": str, "metadata": {"topic","arxiv_id","date","source"}}
Vector: 384-dim MiniLM (fastembed) under name "fast-all-minilm-l6-v2".
Duplicate check: scroll existing points -> skip if arxiv_id already present
                 or if the title text is already in a stored document.
"""
import json, urllib.request, uuid
import fastembed

QDRANT, COLL, VECTOR_NAME = "http://localhost:6333", "default", "fast-all-minilm-l6-v2"
DATE = "2026-08-20"

T_QUANTUM   = "Quantum computing: algorithms, error correction, hardware, quantum ML"
T_MATERIALS = "Materials science: DFT, ML interatomic potentials, crystal structure generation"
T_MLSYSTEMS = "ML systems: inference efficiency, quantization, MoE architectures, long context"
T_AGENTS    = "Agents: tool use, retrieval, evaluation, multi-agent systems"

# (topic, arxiv_id, title, one-sentence finding in own words — all read 2026-08-20)
PAPERS = [
    (T_QUANTUM, "2608.18985", "RushHour: A Dynamically Reconfigurable Lattice-Surgery Architecture",
     "Dynamic lattice surgery with a hardware-compiler co-design (RushHour ISA + Lattice Management Unit) lets one unified span cover the whole area/time trade-off of FTQC; on 6 state-of-the-art compilers it runs 86% of benchmarks where others need 1.2-3.5x larger chips and is 2.0-7.2x faster on space-constrained early-FTQC chips"),
    (T_QUANTUM, "2608.18512", "Integer Linear Programming Decoder for Abelian and Non-Abelian Topological Codes",
     "Formulating topological-code decoding as integer linear programming with anyon-fusion linear constraints handles arbitrary (abelian and non-abelian) topological orders including correlated error species, beating matching and clustering decoders on thresholds for Z2, Z3, and D4 topological phases"),
    (T_QUANTUM, "2608.16857", "Fault-Tolerant Quantum Computation with Adversarial Errors",
     "Proving fault-tolerance against an adaptive adversary corrupting N^{1-o(1)} qudits per round via a new family of subsystem product codes with large dimension/distance, low-weight checks, and transversal non-Clifford gates — resolving a key bottleneck toward quantum PCPs and demonstrating FTQC survives global worst-case non-Markovian noise"),
    (T_QUANTUM, "2608.15760", "Machine Learning Approaches to Decoding Topological Quantum Codes",
     "A survey chapter framing topological-code decoding as a learning problem: discriminative / generative / RL formulations, neural decoder building blocks, real-time constraints, and open challenges toward scalable fault-tolerant quantum computing"),
    (T_MATERIALS, "2608.19041", "Universal Machine-learning Molecular Dynamics at the Speed of Empirical Potentials",
     "DPA4C, a co-designed equivariant MLIP with compressed CUDA operators, spans a 49-fold parameter range; the largest variant matches MACE-Omat accuracy at ~100x higher measured throughput, and the most compact reduces the fastest existing universal MLIP's energy/force/stress errors by 61.4%/48.1%/34.3% at 1.92x its saturated throughput"),
    (T_MATERIALS, "2608.17716", "Atomistic Structure Generation and Neural-Network Screening of Hard Carbons to Identify High-Capacity Sodium Storage",
     "Combining universal ML interatomic potentials with the RAFFLE structure-generation framework to build 13,096 experimentally matched hard-carbon models (up to 4,378 atoms), then using a lightweight NN surrogate on frozen-potential descriptors plus geometric void features to identify candidates exceeding 800 mAh/g for sodium storage"),
    (T_MATERIALS, "2608.15776", "ALKEMIE Agent: an autonomous platform for computational materials design",
     "An agentic platform integrating retrieval-augmented generation, a materials-computation knowledge base, database-supported provenance, AI-assisted structure modeling, bounded task execution, and tool-calling iteration in a traceable control loop, demonstrated across recommendation, phonon, MLIP training, LAMMPS, AIMC, and active-learning screening tasks"),
    (T_MATERIALS, "2608.15609", "Graph neural network prediction of temperature-dependent hydrogen diffusion and thermal conductivity tensors of tungsten",
     "A rotation-equivariant GNN surrogate reads a tungsten atomic configuration (with helium bubbles and grain boundaries) and returns full transport tensors — temperature-dependent hydrogen diffusion and thermal conductivity — in milliseconds, replacing hours of embedded-atom-method MD and anchoring the fusion-divertor transport prediction to first-principles ML potentials"),
    (T_MLSYSTEMS, "2608.18261", "Cacheable by Design? Training Mixture-of-Experts Routers for Locality Against the Edge Memory-Bandwidth Wall: A Pre-Registered Negative Result with a Systems Measurement Study",
     "Pre-registered negative result: training MoE router locality losses to reduce cache misses (up to 60% fewer) fails at every configuration to meet the <=1% perplexity gate, and a 340M rung shows the tax does not shrink with scale; a training-free cache-aware rerouting stack combined with the trained component achieves ~80% miss reduction at <=3.4% perplexity"),
    (T_MLSYSTEMS, "2608.15602", "FluxBin: Flexible LUT-based Ultra-low-bit LLM Inference by Algorithm-Kernel Synergy",
     "Binary-weight LLM inference via a look-up-table CUDA kernel fused with a row-column binary-algorithm co-design: up to 5.92x speedup and 10.19x energy reduction, with 70B-scale models deployable on a single A100 at 4x memory reduction and accuracy comparable to heavy fine-tuning methods"),
    (T_MLSYSTEMS, "2608.16947", "A Constant-Competitive Algorithm for Dynamic Mixture-of-Experts Serving",
     "Proves a Theta(1) competitive randomized algorithm for dynamic MoE serving (experts replicated across k+1 GPUs), improving on the prior O(sqrt(log k)) bound and closing a gap for the integral primal problem with a Lean 4 machine-checked proof of the full reduction"),
    (T_MLSYSTEMS, "2608.13756", "The Integer Alibi: Localizing Cross-Kernel Divergence in INT8-Quantized LLM Inference",
     "The core finding is that INT32 dot products are mathematically exact and bit-reproducible, so any end-to-end divergence between CUTLASS and Triton INT8 GEMM kernels must originate in scale application or output rounding, not in the accumulation itself; the paper presents a methodological framework for diagnosing this class of cross-kernel discrepancies at the bit level"),
    (T_AGENTS, "2608.18554", "CentaurBench: Benchmarking LLM Capabilities on Augmenting vs. Automating Real-World Work Tasks",
     "A unified framework where an assistant LLM guides a lower-capacity worker LLM across 7 economically grounded tasks: rankings in automation vs augmentation modes are only modestly correlated, and the automation winner loses on 5 of 7 augmentation tasks, showing that automation ability is an incomplete proxy for assistance quality"),
    (T_AGENTS, "2608.18398", "LEDGER: Claim-to-Evidence Trace Graphs for Auditing LLM Agents",
     "A layered tracing/review system that builds typed evidence-and-decision graphs over raw agent execution logs, grouping Trace Records into Evidence and Workflow Nodes and linking claims to their supporting actions, artifacts, and checks for evidence-centered audit of long-horizon tool-using agents"),
    (T_AGENTS, "2608.17275", "When Agents Act on Web3: An Attack-Surface Survey of MCP, Skills, and Tool Calling",
     "A survey of agent tool-calling (MCP/skills) on public blockchains argues that blockchain layer properties (irreversibility, signing authority, continuous autonomy, sequence-level composition) turn normally-recoverable agent failures into standing irrecoverable loss; maps the attack surface with a risk-matrix, and finds current protections stop fewer than 30% of attacks and model-level safety rejects fewer than 3% of risky calls"),
]

# ---- duplicate check: scroll existing corpus ----
req = urllib.request.Request(f"{QDRANT}/collections/{COLL}/points/scroll",
                             data=json.dumps({"limit": 200, "with_payload": True}).encode())
points = json.load(urllib.request.urlopen(req))["result"]["points"]
stored_arxiv, stored_docs = set(), []
for p in points:
    d = p["payload"]
    aid = (d.get("metadata") or {}).get("arxiv_id")
    if aid:
        stored_arxiv.add(aid)
    doc = d.get("document") or ""
    stored_docs.append(doc)
    for tok in doc.split():
        if tok.startswith("arXiv:"):
            stored_arxiv.add(tok[6:])
print(f"existing points: {len(points)}, arxiv ids: {len(stored_arxiv)}")

# ---- filter ----
to_store, skipped = [], []
for topic, aid, title, finding in PAPERS:
    if aid in stored_arxiv:
        skipped.append((aid, title, "arxiv_id already present"))
    elif any(title.lower() in doc.lower() for doc in stored_docs):
        skipped.append((aid, title, "title already present"))
    else:
        info = f"{title}. {finding}. arXiv:{aid}"
        to_store.append({
            "id": str(uuid.uuid4()),
            "payload": {
                "document": info,
                "metadata": {"topic": topic, "arxiv_id": aid, "date": DATE, "source": "arxiv-weekly"},
            },
        })

if skipped:
    print("SKIPPED (duplicate):")
    for a, t, r in skipped:
        print(f"  {a} {t} — {r}")
else:
    print("no duplicates found")

if not to_store:
    raise SystemExit("nothing to store — all 15 were duplicates; aborting without changes")

# ---- embed + upsert ----
# NOTE (discovered 2026-08-20): on this Qdrant 1.19.0 build the POST
# /collections/<c>/points upsert endpoint is a no-op shim (returns
# "ok" with empty result, nothing persists). The PUT /collections/<c>/points
# endpoint with the standard {"points":[...]} body is the real one.
model = fastembed.TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')
vectors = list(model.embed([t["payload"]["document"] for t in to_store]))
body = json.dumps({
    "points": [
        {
            "id": t["id"],
            "vector": {VECTOR_NAME: [float(x) for x in v]},
            "payload": t["payload"],
        }
        for t, v in zip(to_store, vectors)
    ],
    "wait": True,
}).encode()
req = urllib.request.Request(f"{QDRANT}/collections/{COLL}/points", data=body, method="PUT",
                             headers={"Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req))
print(f"upsert response: {resp}")

# ---- verify ----
req = urllib.request.Request(f"{QDRANT}/collections/{COLL}/points/scroll",
    data=json.dumps({"filter": {
        "must": [{"key": "metadata.source", "match": {"value": "arxiv-weekly"}}]
    }, "with_payload": True, "limit": 200}).encode())
stored_now = json.load(urllib.request.urlopen(req))["result"]["points"]
this_run = [p for p in stored_now if (p["payload"].get("metadata") or {}).get("date") == DATE]
print(f"arxiv-weekly points now: {len(stored_now)}; this run: {len(this_run)}")
