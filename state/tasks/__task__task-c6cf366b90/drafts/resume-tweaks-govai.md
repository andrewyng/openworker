# Resume Tweaks — GovAI
## Role: Research Fellow 2026, AI Governance / Safety / Technical AI Governance (posted 2026-07-27, deadline **today — Aug 16, 2026**)

Target reader: a research program lead at a think tank, not an ML hiring manager. The posting asks for "significant research experience" and "a proven record of impactful research"; it does NOT ask for a degree. The keyword list on the fellowship page is:

> AI Governance; AI Safety; AI Risk Management; AI Policy; Technical AI Governance; Responsible AI; Machine Learning Governance; Machine Learning; Public Policy; Threat Modelling; Technology Regulation; Emerging Technologies; AI Economics; Geopolitics of AI.

## What to lead with (re-order top of resume)

1. **ADI Fellowship, Equitech Futures (Dec 2023)** — move this to the top of the whole resume for this application. The ADI Fellowship's framing — "Selective machine learning fellowship focused on applied data science, real-world ML pipelines, and responsible AI" — is a *fellowship* and it explicitly references responsible AI. That is a direct keyword match with the fellowship page ("Responsible AI").
   - Reframe the "under the supervision of the Founder & Chief Science Officer" line to lead with the *output* rather than the advisor, because GovAI is asking for "a proven record of impactful research."
   - If you have any specific deliverable — a paper, a codebase, a workshop, an internal report — name it. If not, don't pad it; the cover letter does that work.

2. **Mutual of Omaha (Jan 2024 – Feb 2025)** — second, because it's the strongest production evidence and it's *in a regulated, sensitive domain*.
   - Reframe the SLM-agent ETL bullet to "agent-based ETL over medical and insurance claim records" — the "regulated, sensitive, large corpus" framing is GovAI-adjacent, and "machine learning governance" is a named keyword on their page.
   - The RAG bullet — "semantic search over a large, sensitive corpus" — is exactly the class of system GovAI research covers under technical AI governance; keep it but reframe it to emphasize the *governance* surface (what you did about sensitivity, what you *didn't* do, what you learned) rather than only the performance outcome.

3. **Hewani (Apr 2025 – present)** — third, for production scale, because GovAI also wants "significant research experience" which I'd argue does include sustained production work.
   - Reframe the deployment bullet: "owns the full deployment lifecycle — provisioning, containerization, monitoring, retraining" — this is the "production ML infrastructure" surface that a governance researcher needs to be credible to engineers and to the ML-governance research community.
   - Reframe the RESTful API bullet to "cross-surface inference APIs consumed by multiple business applications" — the multi-app-surface line is the closest "governance" surface I have, because in practice one model serving many consumer apps is a governance problem. Name the trade-offs you worked through, if any.

4. **CSC-specific — AI Society, ShellHacks redaction app, E³ Scholar** — pull these up from *extracurricular* into a "Selected Research & Community Work" section for this application. GovAI's keyword list is heavy on responsible-AI framing, and these three items are *the* responsible-AI evidence.
   - AI Society: "facilitated AI ethics discussions and connected students with learning & career opportunities in AI" — reframe to "facilitated applied AI ethics discussions for a professional AI community" and, if you have a specific discussion or output (a whitepaper, a workshop, a blog post), name it. If not, keep the bullet short and honest.
   - ShellHacks: "AI-powered app to redact sensitive information from documents" is a *responsible-AI* application, which is what the Shellhacks app actually is. Keep it.
   - E³ Scholar (CodePath): "cybersecurity and Python programming" — keep one line; it's a positive signal but the cybersecurity angle is less relevant than the responsible-AI angle, and the CV reader will already see it.

5. **Hybrid QML + QCrypt** — keep at the bottom as "Selected Technical Work." For GovAI, these two projects are *not* the lead. QCrypt's "NIST statistical randomness benchmarks" line is the only line with a direct "reproducible, tested against a standard" signal, which maps to GovAI's "reliability" and "verification" themes. QCrypt stays; hybrid QML compresses to two lines.

## What to rephrase in the posting's language

- "RAG system for intelligent insurance document querying" → "Retrieval-Augmented Generation system over a large, sensitive insurance corpus, with the goal of reducing manual review time" — the "large, sensitive corpus" phrase is GovAI-adjacent, and "reducing manual review time" is the business impact.
- "ETL pipeline using small language models as autonomous agents" → "Agent-based ETL pipeline over regulated medical/insurance claim records" — the "regulated, sensitive domain" framing is the keyword match, and "agentic LLM system" is the technical name.
- "recommendation system" → "personalized ML system in production" — the word "recommendation" is fine at other employers but at GovAI the point is a full-lifecycle decision system, which is where a governance surface exists.
- "monitoring and retraining pipelines" → "production monitoring, retraining, and model-governance surfaces" — GovAI's "technical AI governance" is exactly what this line is, and it's a direct keyword match if we name it.
- "AI ethics discussions facilitated" → "applied AI ethics discussions in a professional community" — "AI ethics" is one word, "responsible AI" is the keyword on the fellowship page; the reframe puts the keyword on the resume.
- "cybersecurity and Python programming" (E³ Scholar) → "cybersecurity coursework, Python programming, and applied AI safety" — no, the "applied AI safety" addition is not on the resume and shouldn't be added. Keep "cybersecurity and Python programming" and let a different bullet carry the responsible-AI keyword instead.

## What to cut

- Snap EAT-vertical marketing-strategy intern — cut. It's the weakest signal and it has no GovAI keyword match.
- NRG energy policy-research intern — cut. Same reason.
- Year Up Java bootcamp — keep one line. The JD doesn't ask for it and it's not keyword-matched, but it's a positive signal (production-grade, OOP, REST) and one line is fine.
- "AI Society member" bullet — expand to lead with the "facilitated AI ethics discussions" line because that's the responsible-AI keyword match.

## What to add (only if true)

- One line on the QCrypt RNG project: "benchmarked against NIST statistical test suites" — the NIST line is the keyword match for "reliable / tested against a standard." If you ran any additional reproducibility or verification work (e.g., a public test suite), add one line. If you didn't, don't add it.
- One line on the hybrid QML project: "PR-AUC ~0.73 with classical baselines" is the honest-benchmark signal. If you ran a public evaluation (e.g., the live demo at hetqml-web.fly.dev/initialize), name the evaluation and the public repo. If not, don't add it.

## What I'm NOT going to do

- Add "PhD" or "research fellow" to the headline. The posting explicitly says no formal degree requirement. Adding a title I don't have is a disqualifier.
- Add "AI Safety" to the Skills block if I haven't done AI Safety work. The "AI Safety" line on the fellowship page is a *research area* that is expected of the applicant, not a skill I can add by listing it.
- Add a "publications" section. The resume has none. The cover letter has an honest line about that. Adding a fabricated publications section is a disqualifier.
- Add "AI Governance" to Skills. That is what the fellowship is for, not what I'm currently doing, and adding it to the Skills block without any production evidence would be a lie.
