# Resume tweaks — Vytalize Health AI Engineer

JD focus from the live listing: **agentic systems, LLM-powered applications, automating healthcare workflows, data-driven clinical solutions; CrewAI & LangChain; US remote.** The company works with independent PCPs, group practices, community health centers, and ACOs on value-based care.

## Lead with
1. **Mutual of Omaha — rewrite as the headline role.** Two bullets, both healthcare-flavored:
   - "Built a production RAG system over insurance/clinical document corpora for semantic search; measurably reduced manual document-review time for review teams."
   - "Built an LLM-agent ETL pipeline (small language models as autonomous agents) deduplicating medical and insurance claim records, plus a document-transformation pipeline that cleanses and optimizes claim files for medical review."
   This is your differentiator vs. every other applicant for a healthcare AI role — say "clinical" and "claims" explicitly, which the resume already supports.
2. **Hewani — keep as current-role, but lead with the platforms-and-APIs bullet** (inference APIs consumed across apps = the "automation of workflows" language the JD uses).

## Rephrase in their language
- The JD says **agents**; the resume says "SLMs as autonomous agents" once. Repeat the word "agents" in 2–3 places (it's all true).
- JD says "automate complex workflows" — your Mutual of Omaha ETL bullet *is* workflow automation. Use that exact phrase: "automated claim-record deduplication workflow."
- JD says "data-driven clinical solutions" — your AI-ethics/PII-redaction hackathon project shows sensitivity to handling sensitive records. Promote the ShellHacks bullet: "Built an AI-powered PII-redaction application (hackathon project) — experience with sensitive data handling in document pipelines."
- "ElasticSearch" sits at the back of your DevOps line; move **LangChain, DSPy, HuggingFace, PyTorch, PostgreSQL, Neo4j** to the front of the skills block to match the stack scan.

## Cut / demote
- Snap Inc. and NRG internship blocks → collapse to one line each under "Earlier experience." Neither is relevant to agentic healthcare work; keeping them full-length dilutes the Mutual of Omaha story.
- Quantum projects → keep both but compress each to 2 bullets and put under "Independent Research Projects" *after* the experience section, with the live demo + GitHub URL kept.
- Rust "Beginner" and R "Beginner" — drop the line; beginners list reads as a placeholder to a US-remote healthcare tech team.

## Add (only if true)
- A one-line summary above experience: "AI/ML engineer, 2+ yrs production — RAG, LLM agents, and MLOps for regulated, document-heavy domains (insurance, clinical documents, content platforms)." Accurate from the resume, and it pre-answers the "are you senior enough to own a healthcare agent workflow" question.
- If you have a public GitHub README for the RAG or ETL work: link it next to the Mutual of Omaha entry — agentic-healthcare teams love reading the actual architecture.
