# Resume Tweaks — Dragos, Senior AI/ML Engineer (ML Application Engineer)

## Lead with (reorder so this is the first block a reader sees)
1. **Hewani, ML Solutions Architect** — but rephrase around *deployment + reliability*, which is the posting's actual job:
   - Current: "Architected and deployed an end-to-end ML recommendation system… delivering personalized content and product suggestions at scale."
   - Better: "**Deployed a production ML inference system end to end on Google Cloud Vertex AI (Feature Store, Matching Engine), engineering the deployment lifecycle from containerization to monitoring and retraining pipelines, and owning the REST inference surface that downstream teams consume.**"
   - Why: the Dragos posting is explicitly *not* training-new-models work; it is "putting existing model types to work inside product and data pipelines," with data contracts, observability, and sane failure modes. The monitoring/retraining bullets are the closest match on the resume — surface the language first.

2. **Mutual of Omaha, AI/ML Engineer, bullet 1** — the RAG system *is* the same pattern as applying established techniques to a document-heavy corpus and proving output usefulness ("significantly reducing manual review time" is a reliability/accuracy outcome, exactly what the posting asks for in the "trustworthy outputs" line).

3. **Projects → promoted to first project, and re-titled**: "Hybrid QML Biomedical Link Prediction (Graph ML on biomedical knowledge graph)" — the posting lists "graph-based representations of asset relationships" as a Nice-to-Have. This project (Hetionet, Neo4j, NetworkX, RotatE embeddings) is the only resume item that clears that bar. Name the graph technologies in line one, and the quantum parts after — for this role the graph is the asset, the quantum is the depth.

4. **Skills block** — the posting requires `SQL` and `scikit-learn` familiarity. The resume has neither named and is otherwise honest. Two allowed moves:
   - **SQL**: PostgreSQL is already listed — that *is* SQL. Promote the Skills line to "SQL (PostgreSQL)" so the keyword appears without any claim of years.
   - **scikit-learn**: the resume does not say we used it. Do **not** add it as a skill. Instead, in the cover letter, say the classical baselines for the QML project were "Logistic Regression, SVM, Random Forest" (already on the resume) — that is the scikit-learn-shaped work, framed as it actually is, and it lets a reader infer familiarity without an overstated skills line.

## Rephrase into the posting's language
- "deployment lifecycle" → the posting's "production context."
- "reducing manual review time" → keep, and optionally add "and providing an auditable accuracy signal over a large document corpus" only if you can show it in the interview; the resume's own claim is enough as written.
- "ETL pipeline using SLMs as autonomous agents to deduplicate" → the posting cares about *applying established techniques to dedup-like problems*; reframe the first verb: "**Built a deduplication + cleansing pipeline** over medical and insurance claim records using small language models as autonomous agents" — the outcome word first, the tool second.

## Cut / de-emphasize for this specific submission
- The Snapchat and NRG internships — keep in place (they fill the timeline and the NRG one shows policy-analytical reading), but no expansion; they are not evidence for this role and the reader should spend their attention on the last two jobs + two projects.
- The Gen Z marketing bullets (Snapchat) — no change needed beyond not leading them.
- Year Up Java academy — keep one line, it helps explain the fundamentals bar the posting sets ("comfortable reading and reasoning about data at scale" aside, the Java/REST fundamentals line is the strongest single proof of basic engineering hygiene).

## Honest gaps to prepare for (do not put on the resume)
- **5+ years experience bar**: the resume shows ~2.5 years in ML roles + ~1 year of adjacent work. The cover letter should argue by scope of ownership (end-to-end system, not component), not by years, and name the gap plainly if asked.
- **No ICS/OT/cyber domain**: the posting calls this "a meaningful plus, not a prerequisite." The closest adjacent evidence is the CodePath cybersecurity coursework (E³ Scholar 2024) — one sentence in the cover letter, no more.
- **Rust**: the listing says "Python or Rust"; the resume already lists Rust at beginner level — that is fine as-is, do not claim more.
