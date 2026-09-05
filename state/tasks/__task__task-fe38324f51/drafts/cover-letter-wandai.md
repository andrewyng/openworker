# Cover Letter — Wand AI, Staff Software Engineer, AI (Org & Governance)

Jonathan Beale
jonaston015@gmail.com · (954) 494-0671 · linkedin.com/in/jonathan-beale · github.com/iconbaypark2900

2026-08-19

**Re: Staff Software Engineer, AI (Org & Governance)**

Dear Wand team,

Your posting describes building the AI and agentic engines behind governance — systems that reason over messy, non-deterministic organizational data to catch policy violations, flag risk, and surface what humans need to see. That is, in essence, the two systems I have most recently built and operated in production.

At Mutual of Omaha I did exactly this kind of work on a smaller stage. I architected a RAG system for semantic query over large insurance document corpora — reasoning over unstructured, ambiguous text to surface what a reviewer needs. I also built an ETL pipeline in which small language models acted as autonomous agents to deduplicate medical and insurance claim records, with a downstream transformation pipeline cleansing and structuring files for medical review. Both systems were built by taking non-deterministic model output and engineering around it: verification, evaluation, and operational hardening, not prompting it into reliability.

The knowledge-graph work on my resume is a direct match for your stack. My hybrid quantum-classical project (public at github.com/iconbaypark2900/hybrid-qml-kg-poc, with a live demo at hetqml-web.fly.dev) is a link-prediction system over the Hetionet biomedical graph: RotatE-based embeddings, dimensionality reduction, QSVC and VQC classification, benchmarked against classical baselines and reaching PR-AUC ~0.73 — and it runs on graph infrastructure I worked with across Neo4j and NetworkX. Reasoning over a structured, entity-relationship view of the world, and scoring candidate relationships, is the core skill this role needs.

On the "track record of building AI systems as a creator" requirement, my honest answer: I have shipped two production systems end-to-end in under two years — a Vertex AI recommendation system (matching engine, feature store, REST inference APIs, containerized deployment, monitoring, retraining) and the agent-style ETL/RAG stack at Mutual of Omaha — plus public, self-directed research in quantum ML and cryptographic randomness (NIST-validated). I know where I sit relative to "Staff"; I'm listing this deliberately because the specific skill set this team is assembling — knowledge graphs plus agents reasoning over ambiguous data — is where I already live, and I'd rather be evaluated on that fit than on a title.

I'd welcome the chance to walk through my Hetionet pipeline and my RAG agent architecture, and to hear what your governance engine looks like.

Thank you,
Jonathan Beale
