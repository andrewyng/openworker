# Resume Tweaks — ERA:AI Fellowship 2027

Applicant: Jonathan Beale · Target: Cambridge ERA:AI Fellowship — Winter 2027 · **Deadline: 13 September 2026, 11:59pm AoE (~12 days)**

## Why these tweaks exist
The ERA:AI application is essay-based (short essay questions, ~2 hours) plus a reference check and interviews. The resume doesn't need heavy reformatting — it needs to *supply evidence* for the essay prompts, which will ask about (a) your research/technical ability, (b) why AI safety/governance, (c) a research project idea. The file below is your evidence map for those essays, not just cover-letter dressing.

## What to LEAD with in the essays

- **Your QML project is your strongest evidence for "research ability."** Do not bury it. Lead with the *evaluating* framing, not the quantum novelty:
  - "Built a hybrid quantum-classical pipeline on the Hetionet biomedical knowledge graph, comparing QSVC and VQC models against logistic regression, SVM, and random forest — **selected QSVC on PR-AUC (~0.73).**"
  - "Optimized quantum embeddings (RotatE + LDA dimensionality reduction) and ran circuits against **real IBM Quantum hardware (Brisbane, Torino) via Qiskit.**"
  - "For a post-quantum RNG, **validated statistical randomness with the NIST suites.**"
  These three facts answer "can you run experiments, implement papers, and decide which model actually won" — the fellowship's core ask.
- **The agentic + RAG work answers "agentic oversight" and "evaluation."**
  - Mutual of Omaha RAG = "evaluation, retrieval." Agentic SLM ETL = "agentic oversight."
  - Hewani full-lifecycle = "I build responsibly" (matches the "responsible AI" strand).
- **No degree is a strength, not a liability, here.** The programme explicitly values "distinctive domain expertise" and "talent-first." Frame your self-taught path as evidence of the autonomy a fellow needs.

## What to REPHRASE
- "ML recommendation system" → **"retrieval-augmented / agentic / evaluation" framing.** The essays reward *research* language over *product* language. Reframe: "built, evaluated, and deployed" rather than just "built."
- The QCrypt RNG → **"rigorous empirical validation"** evidence, not just a cool project. The fellowship wants empirically-grounded work.
- The **ADI Fellowship** line → foreground "responsible AI" + "applied ML pipelines under the supervision of a Chief Science Officer."

## What to add (for the essay project-idea stage)
- Prepare a **concrete research project sketch** — the fellowship will ask for one. Honest options rooted in your actual skills:
  - *Evaluating dangerous capability of agentic systems* — you've watched autonomous SLM agents act on messy claims data; a project measuring when they deviate from operator intent is directly on-strategy ("agentic oversight").
  - *Verification/monitoring of frontier models* — the NIST-validation discipline from QCrypt transfers to "how do regulators independently verify a system's claims" (Technical AI Governance stream).
- Flag that you can **ship the evaluation tooling yourself** (Python, PyTorch, deployment) — a rare combination for a fellowship applicant that makes you an independent contributor.

## What to CUT or shorten (only space matters for essays)
- **Snap (marketing)** and **NRG (energy policy)** — drop to one line or omit; they add nothing to an AI-safety research application.
- **E³ / AI Society / ShellHacks** — keep one line of involvement if at all; the AI Society ethics discussion is arguably relevant to a governance essay (optional).

## Honest gaps to narrate (not hide)
- **No formal AI-safety work / no publications** — but the fellowship says this isn't required and values "people seeking to deepen or redirect an existing research agenda toward frontier AI risks." Route interest through your *evaluation discipline* + *agentic build*, not fabricated alignment research.
- **UK logistics** — the fellowship is in-person (Cambridge, Jan–Mar 2027). Be ready to state (and confirm) you can relocate for 10 weeks; visa support is provided.
- **Required output (paper/benchmark/dataset)** — you can produce a benchmark or evaluation harness; frame yourself as someone who *builds the thing that measures the thing.*

## One-line thesis for the application
You apply because you want to point your "build, evaluate, deploy, and verify" engineering discipline at measuring and constraining frontier-AI risk — the fellowship is the place to turn a builder into a safety researcher.
