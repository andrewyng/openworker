# Resume Tweaks — Wand AI, Staff Software Engineer, AI (Org & Governance)

Goal: make the two resume projects (Hetionet KG link prediction; agent/RAG pipelines) read as the *core* of the application, not the decoration, and front-run the seniority objection with an explicit framing.

## Reorder / reframe
1. **Move the Hetionet QML/KG project to the top of the document** with a heading like "Flagship project: Knowledge-graph reasoning (public, live demo)". Expand to the bullets that matter to *this* role:
   - Built a link-prediction pipeline over Hetionet disease-gene/drug-target graph; RotatE embeddings + LDA dimensionality reduction; QSVC vs VQC vs classical baselines (Logistic Regression / SVM / Random Forest); PR-AUC ~0.73, QSVC best.
   - Graph stack: Neo4j, NetworkX, Qiskit Runtime (IBM Brisbane/Torino backends).
   - *Why it matters here:* "reasoning over structured organizational-style entity data and scoring candidate relationships" — this is the role's core.
2. **Reorder Mutual of Omaha bullets** — lead with the SLM-agent ETL (agent reasoning over messy records) before the RAG. Rephrase both bullets using the posting's vocabulary — "policy/risk" → use **your actual words** "reconciliation, review, deduplication." If you can truthfully say you added any verification/evaluation step to catch model errors, write it down (e.g., "added an evaluation pass on model output"); only if true.
3. **Hewani** — keep, but compress. The Vertex AI recsys + API lifecycle is good *engineering* evidence; it's not the core skill for this role. Two bullets max.
4. **Delete Snap & NRG internships** for this application (they dilute the technical signal).

## Rephrase into the posting's language
- "Agent-style ETL" → "Small-language-model agent pipeline for record-reconciliation; agent behavior verified against ground-truth claim pairs" *(only if verification is true)*.
- "Hybrid QML pipeline" → "Graph-ML pipeline over a 100k+-scale biomedical knowledge graph" *(only if you verified the scale; otherwise drop the scale claim)*.
- "Reasoning over unstructured or ambiguous data" (their words) → mirror it: "Semantic retrieval over large, unstructured insurance document corpora."
- Add a one-line **Highlights** block at top (only with facts you have):
  - `Two production ML systems shipped end-to-end over the past 18 months`
  - `Public, live knowledge-graph ML pipeline (github + demo)`
  - `NIST-validated cryptographic PRNG (Qiskit, PennyLane)`
- Add a single honest line: "Self-directed research track; see public repos for evidence" and link the GitHub profile.

## Cut
- "CodePath E³ Scholar," "ShellHacks participant," "AI Society Member" — drop all three for this application; the letter can mention one.
- Compress the Skills section to one line: `Python · PyTorch · HuggingFace · LangChain · DSPy · Qiskit · PennyLane · Docker · GCP · PostgreSQL · Neo4j · NetworkX · GitHub Actions`.
- Remove all "Beginner" and "Intermediate" labels — Wand's team is senior-heavy and the labels drag the whole CV down.

## Do NOT add
- No "team-lead" or "managed" phrasing unless you actually did.
- No "5+ years," no "staff-level," no "production at scale" beyond what the two role bullets support.
- Don't invent a governance/risk domain experience you don't have. The letter addresses seniority head-on; the resume should not try to outlie it.
