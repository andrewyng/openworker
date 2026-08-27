#!/usr/bin/env python3
"""KB ingest 2026-08-24: find+store discrete findings from today's automation outputs.

Embedding must match collection `default` (fast-embed/all-MiniLM-L6-v2, named
vector `fast-all-minilm-l6-v2`) so stored points are retrievable by earlier ones.
"""
import json
import urllib.request
import uuid

from fastembed import TextEmbedding

BASE = "http://127.0.0.1:6333"
COLL = "default"
VEC_NAME = "fast-all-minilm-l6-v2"
J = "Knowledge base — ingest yesterday's findings"
D = "2026-08-24"

PB = "/home/iconbaypark2900/OpenWorker/__task__task-1b2c4d3f13/papers-2026-08-24.md"
MB = "/home/iconbaypark2900/OpenWorker/__task__task-c685e22a0b/morning-briefing-2026-08-24.md"
BR = "/home/iconbaypark2900/OpenWorker/__task__task-f471043f3e/breakage-2026-08-24.md"
RA = "/home/iconbaypark2900/OpenWorker/__task__task-717e4360ea/repo-activity-2026-08-24.md"

# (subject, information, kind, focus, source)
ITEMS = [
    ("Davoudi-Stryker QCD T-gate cost",
     "arXiv:2608.21258 (Davoudi and Stryker) shows a ~10^14x reduction in the per-Trotter-step T-gate estimate for SU(Nc) lattice gauge theory because exponentiated-Hamiltonian decomposition in the standard product-formula chain overcounts by far more than the Kan-Nam 2021 bound assumes, and the reduction is parameter-independent of cutoff and system size — a case study in cost estimates being conservatively wrong rather than algorithms improving.",
     "paper", "on", PB),
    ("neural quantum states outlook",
     "arXiv:2608.21291 (Rigo, Wurst, Nutakki, Schmitt, Kennes) is the condensed-matter community's own catalog of where neural quantum states still fail: learning non-trivial sign/phase structure, controlling variational bias, enforcing physical symmetries, scaling optimization to large networks, and stabilizing real-time evolution — the checklist to apply before treating a neural state plus Monte-Carlo sampling as a drop-in scoring tier.",
     "paper", "adjacent", PB),
    ("asymmetric capacity self-refinement",
     "arXiv:2608.21345 (Yang, Harris, Imani, Dourish, Li, Zhang, et al.) is the first per-stage size-scaling study of a generate-critique-revise loop over 6 Qwen3 and 4 Gemma 3 sizes: the generator and the refiner reward size, the critic barely does (a small critic beats no critic but adding critic size buys little), and an undersized refiner actively harms output — so the refinement step cannot be delegated to a cheap model in a local small-model fleet.",
     "paper", "on", PB),
    ("Granqvist judge drug discovery",
     "arXiv:2608.21057 (Granqvist, Mercado, Genheden, AstraZeneca) describes a deployed, human-aligned judge for the ChatInvent agentic drug-discovery assistant: four output-quality dimensions plus deterministic tool-call-correctness checks separating what a judge can and cannot reliably score; few-shot demonstrations on human-annotated examples lift alignment from 0.80 to 0.86 — the honest magnitude of what rubric tuning buys — and informal user phrasings do not degrade output while pre-rewriting the question helps (prompt hygiene upstream beats judge robustness downstream).",
     "paper", "on", PB),
    ("Pratt auditable records AI scientists",
     "arXiv:2608.19511 (Dexter Pratt) proposes a record-keeping layer beneath a community of AI-scientist agents: an immutable, structured history of claims, evidence citations, assumptions, and explicit evidence-scope declarations that is shared by the agents and decoupled from them, with a working implementation and prompt components released (adoptable, not just a proposal).",
     "paper", "on", PB),
    ("Dral agentic computational chemistry",
     "arXiv:2608.18508 (Dral, Nawaz, Ullah), a perspective by the co-creator of the dominant ML force-field tool surveys ~50 agentic computational-chemistry systems as of Aug 2026 and names two facts: the specialized-agent space is being commoditized by generalist agents (adding a capability is becoming 'ask the model'), and adoption beyond each system's own developers is very limited — the number of demos is not the user base; the authors close with 'we have no answer' on where established specialists should invest.",
     "paper", "adjacent", PB),
    ("ROCR AsyncEventsLoop busy-spin gfx1201",
     "ROCm/legacy-rocm-build #6634 (opened 2026-08-20) is a second, independent report of the same ROCR AsyncEventsLoop busy-spin bug class as TheRock #7051, this time on gfx1201 on kernel 7.0.0-29-generic (the exact kernel this machine runs) with PyTorch 2.12.0 / ROCm 7.14: after a Wan 2.2 workload completes, 99.79% of the spinning thread sits in rocr::core::Runtime::AsyncEventsLoop (libhsa-runtime64.so.1) and a 5s strace -e ioctl shows zero ioctl calls — pure userspace spin — which reframes it as a ROCR-runtime / wheel-channel bug rather than a gfx1151-only driver quirk; TheRock #7051 itself remained open and unaddressed (~3 weeks, triage, adityas-amd) as of 2026-08-24.",
     "breakage", "on", BR),
    ("Ollama 0.33.0-RC2 local model toggle",
     "Ollama v0.33.0-RC2 (pre-release, cut Aug 21) adds a menu-bar toggle for Claude's use of any local Ollama model and an Apps view, and fixes an agent-client hang where canceling a long prefill discarded restore points (a '46k-of-47k reprocess from zero' on recurrent-layer models); it is an RC, 3 days old, not a final.",
     "news", "on", MB),
    ("Iran sanctions announcement scheduled",
     "Treasury Secretary Bessent said on Aug 23 (2026) he would hold a Monday press conference — i.e. 2026-08-24 — to announce what he calls the 'toughest sanctions in history' on Iran, layered with the naval blockade ('a one-two punch'); Iran's armed-forces chief warned of a 'devastating' response, so a hard headline event on fuel, shipping, and Hormuz flow was expected that day (CNBC / CBS, Aug 23).",
     "news", "adjacent", MB),
    ("liaison-agentSystem stalled 7 days",
     "iconbaypark2900/liaison-agentSystem's last commit is 'WIP snapshot taken during migration to EVO-X2' at 2026-08-17 02:56 UTC — as of 2026-08-24 the repo has crossed into the stalled (7+ days) case, first flagged on 2026-08-20 as 'not yet stalled (3 days)', and the EVO-X2 migration thread inside it has been silent for a week.",
     "repo", "adjacent", RA),
    ("github mcp token broken",
     "The GitHub MCP gateway token that the repo-activity automation verified as reaching the 9 private iconbaypark2900 repos on 2026-08-20 failed on 2026-08-24 with 'Authentication Failed: Bad credentials', reverting 9 of 10 repos to UNKNOWN (HTTP 404) and the automation to its pre-2026-08-20 blind state; the shell path is unchanged (no GITHUB_TOKEN, no gh auth) — it is a token/credential fix, not a research problem.",
     "breakage", "on", RA),
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


model = TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')
embeds = list(model.embed([t[1] for t in ITEMS]))
assert all(len(v) == 384 for v in embeds), [len(v) for v in embeds]

results = {"stored": [], "skipped": []}
for (subject, info, kind, focus, src), vec in zip(ITEMS, embeds):
    hits = qdrant("POST", f"/collections/{COLL}/points/query", {
        "query": [float(x) for x in vec], "using": VEC_NAME, "limit": 3,
        "score_threshold": 0.72, "with_payload": True,
    })
    hits = hits if isinstance(hits, list) else (hits or {}).get("points", [])
    if hits:
        top = hits[0]
        p = top.get("payload", {})
        print(f"SKIP dup {subject!r} score={top['score']:.3f} src={p.get('metadata',{}).get('source') or p.get('source','?')}")
        results["skipped"].append({"subject": subject, "score": round(top["score"], 3),
                                   "stored_info": (p.get("document") or "")[:120]})
        continue
    qdrant("PUT", f"/collections/{COLL}/points", {"points": [{
        "id": str(uuid.uuid4()),
        "vector": {VEC_NAME: [float(x) for x in vec]},
        "payload": {
            # The key the READERS use. It is NOT the qdrant-store tool's parameter
            # name ("information") -- copying that name into a raw payload is what broke
            # every qdrant-find over this collection. Read the data, not the interface.
            "document": info,
            "metadata": {
                "job": J, "date": D, "cadence": "daily",
                "focus": focus, "kind": kind,
                "source": src,
            },
        },
    }]})
    results["stored"].append({"subject": subject, "focus": focus, "kind": kind})
    print(f"STORED {subject!r}")

cnt = qdrant("GET", f"/collections/{COLL}", {})
print("COLLECTION", json.dumps(cnt, ensure_ascii=False))
print("STORED", len(results["stored"]), "SKIPPED", len(results["skipped"]))
with open("/home/iconbaypark2900/openworker-tasks/__task__task-ecad448aa3/ingest_kb.json", "w") as f:
    json.dump({"results": results, "collection": cnt}, f, ensure_ascii=False, indent=2)
