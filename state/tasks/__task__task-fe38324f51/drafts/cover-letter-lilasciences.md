# Cover Letter / Research Proposal — Lila Sciences, AI Residency (2026 Cohort)

> **Note:** Lila requires your ≤3-page research proposal to be submitted **as**
> the cover letter on their Greenhouse form. This draft is structured that way:
> a proposal up front, motivation at the end. Trim to 3 pages before submitting.

---

Dear Lila Sciences team,

I am applying to the AI Residency Program, 2026 cohort. My proposal:
*"Agent-driven hypothesis triage over graph-structured scientific corpora"* — a
contribution to Lila's **agentic science** and **ML-driven automation** research
areas.

## Project proposal (summary)

Scientific discovery increasingly lives in graph-structured corpora: gene–disease
relations, drug–target interactions, reaction networks. I propose a residency
project to build an **autonomous triage agent** that walks a scientific knowledge
graph, forms candidate hypotheses, scores them with a learned link-prediction
model, and proposes the most promising next experiments for a scientist to run.
Concretely:

1. **Learned scorer.** A hybrid quantum-classical link-prediction model (QSVC /
   VQC encoders over RotatE graph embeddings) on Hetionet-scale corpora, benchmarked
   against classical baselines. I have built and deployed this end-to-end on Hetionet
   (disease–gene, drug–target prediction; PR-AUC ≈ 0.73 with the quantum variant
   ahead of logistic-regression/SVM/random-forest baselines; live demo at
   hetqml-web.fly.dev).
2. **Agent loop.** The rescoring model becomes the scoring function in an
   agent that can query the graph, read node annotations, propose edges, and
   revise. I have direct prior experience with this pattern: at Mutual of Omaha I
   built an ETL pipeline where small language models acted as autonomous agents
   to deduplicate medical and insurance claim records — the same "LLM proposes,
   model disposes" structure, at a scale where a wrong proposal has cost.
3. **Evaluation.** Held-out edge sets, counterfactual ablations (agent vs.
   random walks vs. classical scorers), and a small case study of the agent
   surfacing plausible-but-non-adjacent pairings a human reviewer would have
   missed.

**Relevance to Lila's agenda:** this sits squarely at the intersection of your
listed "agentic science" and "ML-driven automation" areas. The same triage-agent
pattern transfers to materials knowledge graphs (phase–property,
composition–processing relations), where a scientist's bottleneck is not
generating candidates but *triaging* them.

## Why me

- **Python + PyTorch, production-grade.** RAG architecture for insurance
  document semantic search (Mutual of Omaha); end-to-end ML recsys on GCP Vertex
  AI owning the full deployment lifecycle — Feature Store, inference APIs,
  monitoring, retraining (Hewani).
- **Self-directed research with shipped artifacts.** Two concurrent open projects
  with live demos and open repos (Hetionet QML pipeline; a post-quantum RNG
  validated against NIST test suites). I ship research, not just notebooks.
- **Agentic systems in a regulated domain.** The Mutual of Omaha agent-based ETL
  work is the closest real-world precedent for "agentic science": models acting
  with authority over data where errors are expensive, under monitoring.
- **Graphs.** Neo4j, NetworkX, RotatE embeddings, LDA-reduced quantum encoders —
  I work in graph space fluently on both the classical and quantum sides.

## Why Lila, why now

Lila is the rare lab that has both the compute substrate and the willingness to
fund *research* rather than only engineering: a residency that pairs you with
scientists on open-science problems, with publish-encouraged norms, is exactly
the environment where an agent-triage system of the kind I've been prototyping
solo can be evaluated against real discovery workflows. I want to do this next
year rather than the year after.

## Gaps I want to name up front

I do not have materials-science domain training, and I have no formal
publications. The first is addressed by the proposal being *method-first* — the
agent-and-scorer pattern is domain-agnostic and transfers to materials corpora
once embedded in Lila's domain. The second is addressed by the shipped artifacts
above; I would be glad to talk through the code and demos in an interview.

I am based in the US (Florida) and can relocate to Cambridge for the residency.

With thanks,
Jonathan Beale
jonaston015@gmail.com · github.com/iconbaypark2900
