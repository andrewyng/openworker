# Resume Tweaks — CrowdStrike, AI Platform Engineer (Remote)

> Prerequisite: I could only verify this JD through aggregator snippets (the Workday page did not render during the search run). Read the live posting first; if the requirements stack matches what's summarized below, apply these tweaks.

## Lead with
1. **Hewani, ML Solutions Architect, bullet 2** — "RESTful inference APIs… full deployment lifecycle from infrastructure provisioning and containerization to monitoring and retraining pipelines." The posting asks for "containerization technologies (Docker, Kubernetes) and REST APIs" plus cloud prototyping → this bullet is the single strongest match on the resume. Promote it above bullet 1 in the order for this submission.
2. **Technical Skills**, reordered into the posting's own categories:
   - Languages: Python, TypeScript, Java, Rust.
   - AI/ML: PyTorch, HuggingFace Transformers, LangChain, DSPy, Ollama.
   - DevOps & Cloud: Docker, Google Cloud Platform (GCP), GitHub Actions.
   This grouping mirrors the JD's "Python/TypeScript + Docker + cloud + LLM tooling" structure and makes each required skill visually findable.
3. **Mutual of Omaha, bullet 2** (SLM-agent ETL/dedup) — the posting's context is enterprise AI agents moving "from experimentation to governed systems"; a pipeline of autonomous small models on real claim data is the closest resume evidence of operating LLM workloads inside a larger data system.

## Rephrase in the posting's language
- "deployment lifecycle" → "production deployment and operational lifecycle" (the JD talks about dependable, governed systems).
- "ETL pipeline using small language models as autonomous agents" → "production pipeline of small-language-model autonomous agents" (puts "production" in front, matching their experimentation→production framing).
- Add no claims about Kubernetes, Terraform, OIDC, or AWS — none are on the resume. GCP is named in the JD ("AWS or GCP"), so the GCP line is safe as-is.

## Cut / de-emphasize
- Quantum projects: to one line under Projects for this role. CrowdStrike is a platform/enterprise org; the relevance here is only "comfortable with unfamiliar stacks and rigorous baselining," not the domain itself.
- Snapchat + NRG internships: keep for timeline completeness, no expansion — same rule as the Dragos draft.

## Honest gaps (state in cover letter, don't put on resume)
- Kubernetes, Terraform, OAuth2/OIDC / non-human identities: required by the JD, not on the resume. Flag the K8s/Terraform/OIDC gap head-on in the letter (already done in the draft) rather than hoping it isn't checked.
- Seniority: "expert"-level JD language vs. ~2.5 years in ML — same strategy as Dragos: argue by scope of ownership (owns the full serving stack at Hewani), not years.

## Bottom line
Rank this last among the three. The requirements stack skews platform-ops (K8s, Terraform, OIDC, 3-trillion-events scale) further from the resume's actual evidence than the Dragos or Perplexity roles do, and the req appears to have been live for a while rather than fresh. Submit the other two first.
