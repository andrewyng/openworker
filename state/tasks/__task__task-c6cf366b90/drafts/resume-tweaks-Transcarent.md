# Resume tweaks — Transcarent (Senior ML Engineer, agentic)

Goal: screens on **production agentic systems + RAG/retrieval + LLM eval + LangChain suite + high-stakes domain**. The posting explicitly accepts "or equivalent practical experience" for the degree and says they're not looking for someone who checks every box — spend the tailoring on evidence, not on padding.

## Lead with
1. **Mutual of Omaha — RAG + SLM-agent ETL** as the top bullets overall (before Hewani). This role is the literal description of that work, at consumer health scale.
2. A one-line framing under experience: "Production LLM/agent systems for insurance and medical decisions; retrieval, agent pipelines, baseline evaluation."
3. Keep the **Hewani** block strong but reframed: "recommendation system on Vertex AI (embeddings + matching engine)" → that IS the "embeddings, vector search, relevance tuning" line they want. Make embeddings/vector search explicit in the bullet if true.

## Rephrase in their language
- "RAG system for intelligent insurance document querying" → "RAG system over a large insurance-document corpus — retrieval and query rewriting for semantic search; reduced manual review time" (only add "query rewriting" if actually implemented).
- "ETL pipeline using small language models as autonomous agents" → "LLM-agent ETL pipeline for deduplicating medical and insurance claim records (autonomous agents, production use)" — keep "production" and "claim records" front and center.
- QML project: move the **evaluation** sentence to the first line of the project: "Built baseline eval set (Logistic Regression, SVM, Random Forest) and reported PR-AUC ~0.73 with QSVC outperforming classical baselines." That is the "own LLM evaluation" equivalent you actually have.
- Skills line: keep LangChain, Python, PyTorch, HuggingFace Transformers, Docker, GCP, PostgreSQL visible in the first two skill groups.

## Gaps to handle honestly (do NOT invent)
- **5+ years ML** vs. ~2.5 shown — the posting's own "we aren't looking for someone who checks each box" language is your opening; lead with scope and let the two domain-matched production systems do the work.
- **Multi-turn agents, tool/function calling, memory** — the resume shows autonomous-agent ETL (a form of agentic workflow) but not explicit "stateful graphs / tool use" wording. If LangGraph or explicit tool-calling was used in any of these projects, name it. If not, do not add it.
- **High-stakes domain guardrails** — insurance/medical domain exists; explicit input filtering/safety-checking does not appear. Do not claim a "guardrails layer" that isn't in the resume.
- **Agent observability/tracing** — not visible in the resume. If you used any logging/tracing on the Hewani or Mutual of Omaha systems, name it. If not, leave it.

## Cut / demote for this application
- Snap marketing, NRG research, Year Up, ShellHacks, CodePath → cut to a one-line "other" section or drop entirely.
- Quantum hardware detail in the QML project → one line; keep the **eval harness** and **baseline comparison** detail prominent.

## Do NOT
- Do not claim publications or peer-reviewed work.
- Do not add Kubernetes/OBS/tracing claims that aren't in the resume.
- Do not claim a degree. The "equivalent practical experience" clause is explicit — use the scope argument.
- Do not mention the interview's "no GenAI use" ask in the letter or resume — it's their internal rule, not a fit criterion.
