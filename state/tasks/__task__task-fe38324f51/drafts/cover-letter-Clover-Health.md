# Cover Letter — Clover Health (Counterpart Health), Senior Machine Learning Engineer (Remote)

**For:** Senior ML Engineer — ML/NLP/LLM platform, insurance claims and chronic-care data (posted 2026-08-21)
**Applicant:** Jonathan Beale

---

Dear Clover Health team,

Counterpart Health uses machine learning and NLP to predict avoidable adverse events and get people targeted care. I've spent the last two of my career years doing exactly that kind of applied ML on insurance claims data, and I'd like to bring that experience to a team scaling it.

At Mutual of Omaha I built a RAG system for intelligent querying over large insurance document corpora — the same problem space you describe in the posting ("ML/NLP/LLM infrastructure is central to our central mission"). The system was live, reduced manual review time, and was designed to answer the question claims teams actually asked. In parallel I built an ETL pipeline using small language models as autonomous agents to deduplicate medical and insurance claim records — messy, high-volume, health-sector data where accuracy and throughput both matter, the same profile of work this role describes.

At Hewani I architect and own an end-to-end ML recommendation system on GCP Vertex AI, from feature store setup through inference APIs consumed across multiple app surfaces, through monitoring and retraining. I've also built the full deployment lifecycle: containerizing models, writing the inference APIs, monitoring them, and owning retrigger retraining when the model degrades. That's a "high-reliability, distributed platform for ML/NLP/LLMs" in practice — not a research prototype.

The posting asks for someone who can "work autonomously in ambiguous environments" and "translate insights into action at scale." I'd note the overlap with what I've actually shipped:

- RAG over insurance claims documents (Mutual of Omaha) — your core domain
- Agentic pipeline over claim records (Mutual of Omaha) — directly transferable
- Production ML infrastructure on GCP Vertex AI, owned end-to-end (Hewani)
- Evaluation-driven iteration: I've built comparison baselines, measured PR-AUC against them, and re-tuned — on a harder problem (quantum classifiers on a biomedical graph) where the evaluation is the point

Gaps I'll name plainly: the posting asks for five-plus years in ML Engineering roles; my timeline is a bit under that bar, and I don't have a traditional CS degree. What I do have is two years of production ML work where the output was a working system used by a business unit, and a research depth that extends outside the standard toolkit (quantum-classical ML on real hardware). I'd appreciate the chance to demonstrate both in an interview.

I'd love to talk about how an SLM-agentic deduplication pipeline and an end-to-end GCP recommendation system map onto Counterpart Health's ML platform work.

Best,
Jonathan Beale
