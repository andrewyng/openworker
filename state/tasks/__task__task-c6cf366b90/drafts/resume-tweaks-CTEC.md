# Resume tweaks — CTEC (AI / Machine Learning Engineer, OPM)

Goal: this posting screens on **RAG + agentic frameworks + Python/PyTorch + claims/insurance domain + evaluation rigor**, and explicitly states "Equivalent professional experience will be considered in lieu of a degree." Lead with the overlap.

## Lead with
1. **Mutual of Omaha** block — move it above Hewani for this application. Its RAG + SLM-agent ETL bullets ARE the posting (claims domain, RAG, agents, dense-document parsing).
2. A one-line domain anchor under the experience header: "Production ML for insurance and medical-claims documents — RAG, agent-based dedup, evaluation against classical baselines."
3. The **evaluation** evidence — CTEC names RAGAS / LLM-as-a-judge. The QML project's baselines + PR-AUC are real evaluation evidence; promote it to a standalone bullet: "Evaluated quantum models against three classical baselines (Logistic Regression, SVM, Random Forest); reported PR-AUC ~0.73."

## Rephrase in the posting's language
- "RAG system for intelligent insurance document querying" → "RAG system for **semantic querying and dedup of dense insurance documents and claims records**" (keep to what the resume actually supports; only add "layout-aware extraction" if that is genuinely true).
- "ETL pipeline using small language models as autonomous agents" — keep verbatim; it already reads like their "agentic system architecture" language.
- Skills line: ensure LangChain, Python, PyTorch are visible up top (all already listed — reorder only, no additions).

## Gaps to address deliberately (don't invent, bridge with what's real)
- **5+ years ML / 2+ years LLM orchestration** vs. ~2.5 yrs shown. Do not pad years. Bridge via the "equivalent experience" clause + scope: full-lifecycle ownership at Hewani, two domain-matched production systems at Mutual of Omaha.
- **SQL + Spark/PySpark "strong"** — resume shows PostgreSQL/ElasticSearch, not Spark. If Spark is not a real skill, do not add it; lead with what exists (PostgreSQL, data pipelines at Mutual of Omaha).
- **Azure** — resume is GCP-shaped. Do not add Azure claims; GCP + Docker + GitHub Actions is the cloud evidence that exists.
- **US citizenship / OPM Public Trust clearance** — not in the resume. Confirm real status before applying at all; this is a hard gate. (The cover letter has a bracketed note where this line goes if eligible.)

## Cut / demote for this application
- Snap Gen Z marketing, Year Up Java focus, ShellHacks detail → one line each or drop.
- Keep the QML biomedical project (hybrid/rule-based differentiator + niche) but trim quantum-hardware detail to one line: CTEC cares about evaluation and hybrid modeling, not the hardware target.

## Do NOT
- Do not claim a CS degree. The posting allows equivalent experience — use that.
- Do not add Spark/PySpark or Azure fluency claims unless real.
- Do not state citizenship in the letter without confirming it.
