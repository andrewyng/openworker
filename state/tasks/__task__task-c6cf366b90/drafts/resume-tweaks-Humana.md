# Resume Tweaks — Humana, Senior AI Software Engineer

**Target role:** Senior AI Software Engineer — LLM-powered features, RAG, prompt pipelines in clinical settings; ~$106,900–$147,000/yr; must live near a designated IT location (Tampa, FL and Fort Lauderdale, FL are both listed).

## 1. Lead with the healthcare-insurance RAG + agent bullets — reorder experience
Right now the resume's most Humana-relevant work (insurance-document RAG, claims-dedup SLM agents) is buried under Hewani's generic "recommendation system." **Move the AI/ML Engineer role at Mutual of Omaha up or expand its bullets to lead**, because every one of the four headline requirements maps onto it:
- "RAG pipeline design and vector databases" → the RAG system over insurance document corpora
- "custom LLM and agent evals / agent trajectories" → the SLM-agentic ETL dedup pipeline
- "HIPAA-Compliant Development / protected health information" → medical + insurance claim records
- "structured prompt pipelines / workflow automation" → the document-transformation pipeline

## 2. Reframe Hewani into the "infrastructure + MLOps" story the posting wants
The posting's "Infrastructure and MLOps: AI infrastructure design, deployment automation, and operational tooling" is already your Hewani story. Tighten that bullet to name the pieces:
- "owned the full deployment lifecycle (infrastructure provisioning, containerization, monitoring, and retraining pipelines)" — add the word **MLOps**
- name the serving surface: "exposed RESTful inference APIs consumed across multiple app surfaces" — this is the "REST/GraphQL APIs that serve AI/ML outputs" requirement; if you have ANY GraphQL or WebSocket exposure, add it
- Add a quantified outcome (even rough) — "reduced manual review time" from the RAG bullet could carry a number (e.g., "% faster" or "hours/week saved")

## 3. Call out the eval work explicitly (this is a differentiator)
The posting heavily weights evaluation ("Implement and iterate on evaluation frameworks," "custom LLM and agent evals, including automated testing of model outputs, agent trajectories, and failure modes"). The resume currently only implies this via "PR-AUC ~0.73" on the QML project. **Add a short, explicit line on that project** like: "Built a model-evaluation strategy: compared QSVC/VQC classifiers against logistic-regression/SVM/random-forest baselines and iterated on the underperformer, measured by PR-AUC." This directly answers the "custom eval" requirement and shows eval discipline in the clinical-AI sense they want.

## 4. Add the tools they name (honestly, only if true)
- **scikit-learn**: you used classical baselines (Logistic Regression, SVM, Random Forest) — those are scikit-learn. Put `scikit-learn` on the ML-libraries line next to PyTorch/HuggingFace. This is a real, evidenced add.
- **Vector databases**: PostgreSQL + Neo4j + ElasticSearch are on the resume; name the vector side if you used pgvector/Pinecone/Weaviate (the posting lists those four). If your RAG used a plain Postgres/ES vector type, say so — it maps directly.
- **Next.js** is already listed — good, it matches the "TypeScript + Next.js for full-stack" preferred line.

## 5. What to cut / de-emphasize
- **Snap Inc. (digital storytelling)** and **NRG (market research)** are the weakest fits for this AI-software role. Keep them one line each (they show remote-internship breadth) but they do NOT belong at the top for Humana.
- **The quantum projects (QML, QCrypt RNG)**: keep them — they're evidence of rigorous evaluation and a non-generic toolkit — but relocate them lower than the production RAG/agent work for this application. They are a "why trust this builder" differentiator, not the lead.

## 6. The honest gap to pre-empt
The posting is 5+ yrs SE + 2+ yrs production AI, and accepts "equivalent experience" in lieu of a degree. Two things to handle:
- **No degree on the resume**: add a one-line "Relevant equivalent experience" note pointing to the 2 years of production RAG/agent/ML work, so the recruiter's eye lands on it before it lands on the missing line.
- **5+ years SE**: total professional span (internships 2024 → present) can be framed as cumulative professional experience; be honest in the interview but let the resume's timeline show breadth.

## 7. Location flag (verify before applying)
The posting requires living near a designated IT location and lists **Tampa, FL / Fort Lauderdale, FL**. Your 954 area code suggests Florida proximity — **confirm you actually satisfy this geographic requirement**; if not, this role is off the table regardless of the technical match.
