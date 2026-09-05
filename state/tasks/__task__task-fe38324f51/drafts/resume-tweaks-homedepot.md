# Resume Tweaks — Home Depot, Data Scientist, Pricing (Optimization & Deep Learning)

Goal: read as an applied ML practitioner who has *shipped large-scale ranking/scoring systems and hardened data pipelines* — the substance of a pricing data-science role — and be explicit about the pricing-domain gap before they find it.

## Reorder / reframe
1. **Hewani first, and expanded.** This is the closest analogy to a pricing/ranking job. Reorder bullets to lead with candidate generation + scoring + serving at scale, and add the business outcome language a retail reader wants: "ranking/personalization at scale," "multiple app surfaces," "monitoring and retraining."
2. **Mutual of Omaha — reframe around data quality, not insurance.** The claim-dedup pipeline is genuinely relevant (dedup, record linkage, data cleaning is core pricing-data work), but strip the insurance flavor. New lead bullet: "Built an ETL pipeline using small language models (SLMs) as autonomous agents to deduplicate and reconcile large-scale claim records; added a document-transformation pipeline to cleanse and structure files for downstream review." The RAG system stays as second bullet, framed as "semantic search over large document corpora; reduced manual review time."
3. **Projects: keep, but demote.** One line each for the Hetionet knowledge-graph project (shows modeling + baselines, which DS interviewers value) and QCrypt RNG (shows benchmarking discipline: "validated with NIST statistical test suites"). Put both under a two-line "Selected Projects" after experience.

## Rephrase into the posting's language
- Replace "personalized content and product suggestions" → "customer-facing product recommendation and ranking at scale" (posting-adjacent vocabulary).
- Replace "owning the full deployment lifecycle" → "owned model deployment lifecycle: containerization (Docker), CI (GitHub Actions), latency-aware API serving, monitoring, scheduled retraining" — concrete nouns a pricing DS panel will recognize.
- Add a single honest skills line: "Model evaluation: A/B and baseline comparisons (PR-AUC, NIST randomness suites)." Only true because of the projects — keep numbers you actually have (e.g., PR-AUC ~0.73 with QSVC).
- Add: "Familiar with: recommendation/ranking (matching engines, feature stores) — looking to apply to pricing & optimization" as an optional "Objective" one-liner.

## Cut
- Snap and NRG internships: compress to a single combined line ("Marketing & market-research internships, Snap Inc. and NRG Energy, 2024") or drop entirely.
- Remove "Java Focus" bootcamp wording; keep only "Software engineering fundamentals (OOP, REST APIs)."
- Drop R (Beginner) and TypeScript (Intermediate) from the primary skills band; keep Java (Intermediate) since JDs often want SQL + Java-adjacent comfort.

## Do NOT add
- No pricing, OR-tools, LP/ILP, or "retail" claims — you don't have them. The letter handles the gap explicitly; don't try to paper over it in the resume.
- No "5+ years" or seniority inflation; the resume is a ~2.5-year track — let the shipped-system evidence carry it.
