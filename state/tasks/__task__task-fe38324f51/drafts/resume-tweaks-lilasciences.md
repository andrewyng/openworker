# Resume Tweaks — Lila Sciences AI Residency

**Audience:** research committee; the "what you'll need to succeed" box on the Greenhouse
posting is your evaluation checklist. Match their language, lead with their priorities.

## Lead with (move to top of Experience, or add a "Selected Shipped Research" block)

1. **Hetionet QML pipeline** — this is your strongest evidence against
   "strong research background demonstrated through publications, thesis work,
   or open-source projects."
   - Rephrase: "Built a hybrid quantum-classical ML pipeline over a biomedical
     knowledge graph (Hetionet) for link prediction; **open-source, live demo,
     benchmarked** against classical baselines (logreg, SVM, RF); PR-AUC ≈ 0.73
     with quantum encoder ahead of all classical variants."
   - **Add:** the URL `hetqml-web.fly.dev` and the GitHub repo — Lila
     explicitly values "open-source contributions."
   - **Add one sentence of method** (currently missing): "RotatE embedding
     + LDA dimensionality reduction → QSVC/VQC classical-quantum encoder fusion."
2. **Mutual of Omaha agent-based ETL** — rephrase in Lila's words:
   - From "ETL pipeline using SLMs as autonomous agents" →
     "agentic pipeline: small language models as autonomous agents that
     deduplicate claim records, with evaluation over a held-out corpus"
   - The word "autonomous agents" is already there — **lead with it in the
     one-line summary**, not buried in the second bullet.

## Rephrase in Lila's posting language

- "RAG system for intelligent insurance document querying" →
  "retrieval-augmented generation over a large, noisy document corpus with
  semantic search — reducing manual review time" (keep, but name the scale:
  how many documents? if you can put a defensible number, do).
- Vertex AI recsys → "personalization at scale, own the deployment lifecycle:
  infra, containers, monitoring, retraining." Lila wants ML-driven automation;
  the deployment-lifecycle bullet is your best "I ship" signal.

## Cut / demote

- **NRG market-research intern** — drop if it doesn't fit in the 2-page limit.
  Not aligned to any Lila research area.
- **Snap A.R. intern** — one line max; "Gen Z marketing" noise to a research
  committee.
- **Year Up / E³ Scholar / ShellHacks / AI Society** — collapse to a single
  "Training & community" line at the bottom or cut entirely. Lila's rubric
  doesn't include these.
- **Java** — demote to the language list and don't claim as "Intermediate"
  if it's not your primary; Lila wants Python/PyTorch depth, not breadth.

## Add (only if true before you claim it)

- A **one-line "Why this project"** note for each of your two open-source
  projects — the motivation is the research-taste signal Lila's rubric
  explicitly scores ("demonstrated through... open-source projects").
- If you have any write-ups, blog posts, or READMEs that explain *why* you
  chose the QML approach, link them. This is the closest thing to a
  publication you have — make sure it's findable.
