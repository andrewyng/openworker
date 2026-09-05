# Resume Tweaks — Clover Health (Counterpart Health), Senior ML Engineer

**Posting / JD keywords (from Dice):** "ML/NLP/LLM platform"; "high-reliability, distributed platforms for machine learning, natural language processing, and LLMs"; "deploying Python apps into production environments"; "production ML/NLP/LLM models"; "distributed systems"; "mentorship"; "healthcare preferred but not required".

## Re-order / lead with — the healthcare/claims domain is your wedge

1. **Mutual of Omaha — RAG for insurance documents** → *first bullet on the resume*. Clover says "healthcare experience preferred but not required" and "we use ML/NLP/LLM to leverage our data to help keep beneficiaries healthy and out of the hospital." Your work is *on insurance documents and medical/claim records* — that is the exact "healthcare data" they're asking for.
   - Rephrase: "RAG system for intelligent querying of insurance and medical documents; semantic search over large corpora; reduced manual review time."
2. **Mutual of Omaha — SLM-agentic claim dedup** → second bullet. Clover cares about "scaling the impact of other engineers and data scientists through mentorship, development of reusable libraries, and documentation." Emphasize the *pipeline, reusable, production* nature rather than the novelty.
3. **Hewani — Vertex AI** → production infra. Map each sub-item to Clover's ask: monitoring → "high-reliability platforms"; retraining → "robust production platform"; containerization + Docker → "deploying Python apps into production environments."
4. **Hybrid QML / QCrypt RNG** → move to research / differentiator. Not the lead for Clover.
   - Clover doesn't need the quantum angle; they need the reliability and the evaluation discipline. Lead with the "compared against classical baselines, measured PR-AUC, iterated" story.

## Cut or de-emphasize

- Snap Inc. marketing-strategy intern — cut for this one; off-domain.
- NRG energy-market research — de-emphasize to a one-liner if kept at all.
- E³ Scholar / AI Society / ShellHacks / Snap — fold into one "Community, coursework & hackathon" line or cut if space is tight.

## Skills section reshuffle (clover-relevant order)

- **Top line:** Python, PyTorch, LLMs, NLP, RAG, LangChain — Clover's explicit stack.
- **Second line:** TensorFlow (they list it), scikit-learn (they list it), Docker, GCP (they list "open source + cloud"), NumPy/Pandas (they list it under "data science"). If you've used any of these, put them on the top line; don't invent, but do surface anything legitimate.
- **Third line (differentiator):** ML pipelines, retraining, monitoring, Docker, GCP Vertex AI (Feature Store, Matching Engine), inference APIs.
- **Research / differentiator (third line):** Qiskit, PennyLane, quantum-classical ML, Neo4j, NetworkX.

## Gaps to preempt (don't invent)

- **5+ years requirement.** Clover's JD asks for 5+ years in ML Engineering roles; you're at ~2. Address in the cover letter (done — already named) and let the domain work + shipped platforms carry the argument.
- **Healthcare:** you *do* have it — insurance + medical + claim records. This is a strength; lead with it. Clover's "preferred but not required" line becomes "you have exactly what we want."
- **No CS degree:** not on the JD (confirmed). No need to flag as a gap; keep ADI Fellowship / Year Up visible as professional training.
- **Team/mentorship:** JD mentions "development of reusable libraries, and documentation" and "mentorship, professional development." You have no explicit line for *mentoring*. If you've done any internal tech talks, pair programming, code reviews, or documentation, add one bullet under Mutual of Omaha or Hewani that says it (only if true).

## One-paragraph "why Clover Health" to use in any intro screen

> I've spent my last two roles building and running production ML — a RAG system over insurance medical documents and an SLM-agentic dedup pipeline on claim records — and owning the full infrastructure lifecycle on GCP at my current role. The "healthcare experience preferred" line hits directly: my last major system was on insurance and medical records.
