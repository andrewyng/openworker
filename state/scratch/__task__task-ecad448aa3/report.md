# KB Ingest Run — 2026-08-31

**Job:** Knowledge base — ingest yesterday's findings
**Status:** Completed. 10 new points stored; collection now at **41** (was 31). 0 left behind budget.

## Context (first run since 08-28)
Read 7 fresh automation outputs (all dated 2026-08-31), all carried forward from the 08-28 ledger:
- `papers-2026-08-31.md` — 3 ON-FOCUS papers + 2 ADJACENT
- `breakage-2026-08-31.md` — ollama v0.33.2 + legacy-rocm-build #6522
- `jobs-2026-08-31.md` — MATS Residency; Horizon 2027 CLOSED
- `morning-briefing-2026-08-31.md` — Nepal flood + Egypt bank action
- `corpus-2026-08-31.md` — arXiv weekly job
- `repo-activity-2026-08-31.md` — 0 new (GitHub gateway down, 4th run)
- `grants-2026-week-36.md` — Corrigibility Research Fund re-open

## ⚠️ Important: the corpus job already stored 10 points today
The **arXiv weekly corpus job** (task-0db7112de6, persona carries only github+filesystem MCP)
ran earlier today and **stored 10 papers to Qdrant itself** via HTTP, growing the collection
**21 → 31**. Its IDs include **2608.28444** (Sliding-window attention), which is ALSO listed in
the 08-31 `papers` file — a cross-file duplicate. The mandated find-first + identifier-aware
dedup correctly caught it: **2608.28444 was NOT re-stored** by this run. The `papers` ADJACENT
item 2608.28490 (security survey) was *not* in the corpus set, so it was stored here.

## Stores this run (10; collection 31 → 41)

ON-FOCUS (7):
- **2608.28571** Learning to Decode Concatenated Quantum Codes w/ Hierarchical Message Passing —
  neural QEC decoder doubles the [[15,7,3]] Hamming pseudo-threshold 6.5%→12.3%. The load-bearing
  *cost* half of the OpenEvolve Phase-3 QM-budget question, now with a concrete ~12% threshold.
- **2608.28576** Size-Weight Frontier for Synthetic-Augmented Inference — coverage-guaranteed
  weighting of synthetic data ("weight it, don't pile it on").
- **2608.28541** Code World Models: Enclosed Mode Is a Gauge Choice — a model accepted by a
  sampling gate can be exactly right on the reachable query set and arbitrary beyond it. Strongest
  formalization yet of the OpenWorker FrontierChallenge/Phantom-Gains trust gap.
- **ollama v0.33.2** — macOS/Claude-proxy housekeeping release, first since v0.33.1; no ROCm/Linux
  change, not a new break vector on the gfx1151 box.
- **legacy-rocm-build #6522** — 2nd independent AMD-triage report of gfx1151 AsyncEventsLoop
  100%-CPU-spin, same class as TheRock #7051 (corroborates).
- **MATS Residency Winter 2027** — only genuinely fresh job find: no-PhD AI-safety residency,
  ~$6.4k/mo + up to $16k/mo compute, window Aug15–Oct31 2026 AoE; real gate is the 12-week physical
  Berkeley/London cohort.
- **Corrigibility Research Fund** — re-opened (since a prior digest closed it Aug 23), rolling,
  no form, email apply, closes Oct 31 2026.

ADJACENT (cap 3):
- **2608.28490** LLM-Based Agents for Software & Systems Security — systematic review; the "auditable
  authority" gap is exactly the trust-and-governance hinge the RSI thread has been converging on.
- **Nepal glacier-triggered flash-flood disaster** — ~800 dead, ~85 US citizens missing.
- **US restricts UAE ops of Egypt's Banque Misr** over Iran facilitation — threads into the carried
  "Operation Economic Outcast" Iran thread (not a new escalation).

ADJACENT items left out (over cap of 3): India–Russia gasoline flow (India shipping ~1M barrels);
grants-ADJACENT items BlueDot Impact, Iliad RFP, IBM Quantum Credits.

## State changes carried forward (the ones that moved)
- **Horizon 2027 Fellowship** — FIRST FLAGGED 2026-08-26/27 as "OPEN, ~3 days out"; **now CLOSED**
  (deadline Aug 30 2026 11:59pm AoE has passed — today is the 31st). Drafts were carried 3 runs but
  never submitted, so the 2027 window is **lost** for the applicant. Watch the 2028 cohort.
- **repo-activity GitHub gateway** — **unreachable 4th consecutive run** (identical `fetch failed`;
  08-24 was a different "Bad credentials" error). 0 of 10 repos.
- **Tavily** — search backend down for a **5th consecutive run** (EAI_AGAIN); **Context7** also
  started failing this run (`TypeError: fetch failed`). Breakage + corpus jobs ran via web_search +
  direct GitHub/arXiv REST instead.
- **dcode-stack** — recorded last activity 2026-08-27 (4 days quiet, not yet 7+); FOCUS moved it
  from "went quiet" back to **Active** this week. **workstation-stack** — last activity 2026-08-29.

## Running total
`curl -s http://127.0.0.1:6333/collections/default | head -c 300` → `points_count: 41` (was 31, +10).
Collection `default`, named vector `fast-all-minilm-l6-v2`, 384-dim Cosine, `indexed_vectors_count: 0`
(HNSW still lazy — retrieval verification done via scroll, not `/points/query`, which still rejects
every vector shape).

## Sources / non-findings
- `repo-activity-2026-08-31.md` → 0 (GitHub gateway down, 4th consecutive run).
- 2608.28444 (Sliding-window attention) → already stored by the corpus job today (not re-stored).
- UnitedHealthcare / Laurel / CHEQ job matches — unchanged 08-28 carryover drafts, no store.
- transformers v5.16.1 / llama.cpp v0.3.0 / OpenWorker v0.2.1 / unsloth v0.1.804-beta — unchanged.
- TheRock #7051 — zero movement since 08-18, not re-stored.

## Deliverables
- **Ledger:** `/home/iconbaypark2900/OpenWorker/knowledge/ingest/2026-08-31.md`
- **Run data / script:** `ingest_kb_2026-08-31.json`, `store_2026-08-31.py` in the task workspace.
- **Threads updated:** quantum-computing-ml-rsi-paper-watch (3 papers), automation-fleet (count
  31→41), job-search (Horizon CLOSED + MATS), local-stack-breakage (#6522 + ollama v0.33.2).
