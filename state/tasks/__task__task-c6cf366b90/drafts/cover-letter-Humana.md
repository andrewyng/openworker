# Cover Letter — Humana, Senior AI Software Engineer (Remote — must live near a designated IT location incl. Tampa/Fort Lauderdale, FL)

**For:** Senior AI Software Engineer — building LLM-powered features, RAG, and prompt pipelines deployed in clinical settings
**Applicant:** Jonathan Beale

---

Dear Humana Senior AI Software Engineer team,

Humana uses AI to improve outcomes for the people it serves — and the role you describe is exactly the kind of work I've spent the last two years doing: putting LLMs, retrieval, and structured agents into real systems where correctness and safety actually matter. That stakes-stacking is the difference between an LLM demo and one that reaches a clinical patient, which is why the posting's emphasis on RAG, evaluation, and HIPAA-grade handling of protected health data caught me immediately.

At Mutual of Omaha, I built a RAG system for intelligent querying over large insurance document corpora — semantic search over the same class of sensitive health-and-insurance data Humana handles daily. The system was live and reduced manual review time. In parallel, I built an ETL pipeline that used small language models as autonomous agents to deduplicate medical and insurance claim records and to cleanse and optimize files for review — messy, high-volume health-sector data where accuracy and throughput both matter.

At Hewani I architect and own an end-to-end ML system on GCP Vertex AI, from feature-store setup through inference APIs consumed across multiple app surfaces, and I own the full deployment lifecycle — containerizing, monitoring, and owning retraining pipelines. That is the "infrastructure and MLOps" side of your role in practice, and it taught me to make decisions on technical approach autonomously in greenfield, ambiguous situations — precisely the operating mode you describe.

Where the posting asks for specifics I can point to real artifacts:

- RAG pipeline design and vector databases — built over insurance document corpora (Mutual of Omaha)
- Custom LLM and agent evaluation — I compared QSVC/VQC classifiers against classical baselines, measuring PR-AUC (~0.73) and iterating on the loser, because the eval was the point
- Production LLM work with orchestration (LangChain, HuggingFace Transformers, PyTorch) — deployed end-to-end at Hewani
- Healthcare-adjacent data and HIPAA-aware handling of protected health information — the entire Mutual of Omaha build

Gaps I'll name plainly: the role asks for 5+ years of software engineering with 2+ years of production AI. My production AI/ML work is roughly two years and I have no traditional CS degree — but the posting explicitly accepts "equivalent experience," and I'd rather let the shipped systems speak for the bar than hedge it. GraphQL and WebSockets aren't on my resume yet (I build on REST today), and I don't yet have a named tool like Claude Code or Cursor — both are quick to close and I have no doubt I can.

I'd welcome the chance to walk you through how a RAG-and-SLM-agents build over insurance data, plus an end-to-end GCP ML system, map onto Humana's clinical AI work.

Best regards,
Jonathan Beale
jonaston015@gmail.com · (954) 494-0671 · linkedin.com/in/jonathan-beale
