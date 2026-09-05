# Resume Tweaks — Doma (Staff ML Engineer, Title Insurance)

Goal: shift the resume from "two-year ML engineer who likes projects" toward "full-lifecycle MLOps owner in regulated data domains who benchmarks and defends numbers."

## Lead with these bullets (reorder Mutual of Omaha + Hewani to the top, in this order)

1. **Insurance-domain RAG + claim-record dedup** (Mutual of Omaha). This is the single most on-target bullet for Doma ("title risk and underwriting," "reduce model losses"). Rewrite:
   - "Architected and deployed a RAG system for intelligent insurance document querying, enabling semantic search over large document corpora and significantly reducing manual review time."
   → "Built a production RAG system querying insurance document corpora (embedding, retrieval, generation, eval), cutting manual document-review time for underwriting-adjacent tasks."
   Only claim a number if there is a real one to back it. If "significantly" is the only claim, keep "significantly" — don't invent a %; the JD doesn't require a number.
2. **Full Vertex AI lifecycle at Hewani** — this is the "data ingestion → training → deployment → monitoring → retraining" bullet they list first. Rewrite:
   - current: "...engineering algorithms in Python that deliver personalized content and product suggestions at scale."
   → "Owned the full ML lifecycle on GCP Vertex AI (Feature Store, Matching Engine) — training, REST inference serving across multiple app surfaces, containerized deployment, monitoring, and scheduled retraining — serving personalized suggestions in production."
3. **SMOOTHLY cut**: the Snap and NRG internships. They don't move the needle for a Staff ML seat in a regulated domain and they anchor the reader to a junior frame. Keep them only if a page-2 overflow is needed; otherwise drop.

## Rephrase in Doma's language

| Their phrase (JD) | Your current phrasing | New phrasing |
|---|---|---|
| "full model lifecycle — from data ingestion and training to deployment, monitoring, and retraining" | "deployment lifecycle from infrastructure provisioning and containerization to monitoring and retraining pipelines" | "full model lifecycle (ingestion, training, inference serving, monitoring, retraining)" |
| "Strong applied statistics" | (not currently stated) | Add a single-line: "Applied statistics: feature engineering, class-imbalance handling, model-error decomposition" — only if you can actually name 1–2 examples from the projects. The hybrid QML comparison vs. classical baselines is the honest place this lives. |
| "Tracking degradation, drift, and error rates..." | "monitoring and retraining pipelines" | "Production monitoring with drift and error-rate tracking feeding scheduled retraining" |
| "Fluent with current AI tooling" | (not stated) | Only add if true — e.g., "Daily use of LLM-based IDE/analysis tools" — but do not fabricate fluency |

## Cut / reposition

- **Hybrid QML** and **QCrypt RNG**: move below the work items, keep them, but shorten to one bullet each. They matter at Doma only as evidence that you benchmark against strong baselines and publish open repos. The quantum angle is interesting signal, not a requirement — don't let it read as a research profile when Doma is buying applied production skill.
- **Year Up** and **ADI Fellowship**: keep both — the ADI Fellowship (responsible-AI focus) is relevant to a regulated domain; Year Up is relevant to the Java/production-grade Java mention Doma values.
- **Snap / NRG**: drop (see above).

## One new line to add (only if true)

"Experience working with business/domain stakeholders to translate policy constraints into model behavior" — only if you can point to a real moment where an underwriting or product-operations constraint shaped the model. If not, leave it out. Doma will test this in the interview.

## Do not do

- Don't invent a tenure beyond what the resume says. "Two years production ML" is the honest ceiling.
- Don't add "6+ years" in any form.
- Don't claim causal-inference or bandit expertise at Doma — that's CSC Gen's lane, not Doma's.
