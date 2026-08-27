# Cover Letter — Grid Dynamics, Senior ML Engineer (Remote)

**For:** Senior ML Engineer — LLMs, RAG architectures, agents, evaluation (posted 2026-08-21)
**Applicant:** Jonathan Beale

---

Dear Grid Dynamics team,

You're looking for an ML engineer who has shipped RAG applications, worked with agentic systems beyond API integration, and built evaluation infrastructure from scratch. That's been the shape of my work since 2024.

At Mutual of Omaha I built a RAG system for intelligent querying of insurance document corpora — the same class of problem your JD describes when it names "RAG applications, agents, safety systems, and end-to-end AI products." The system's purpose was to cut manual review time on large claim documents, so my work was measured against business outcomes, not model metrics alone. In parallel I built an ETL pipeline that uses small language models as autonomous agents to deduplicate medical and insurance claim records — practical, production-facing agentic work with data-quality stakes, not a notebook demo. At Hewani I architect and own an end-to-end ML recommendation system on GCP Vertex AI — Feature Store, Matching Engine, inference APIs served across multiple app surfaces, with me responsible for the full lifecycle from containerization to monitoring and retraining.

Two threads in your posting match specific things I've shipped:

- **"Experience working with LLMs beyond simple API integration."** My skills are PyTorch, HuggingFace Transformers, LangChain, DSPy, Unsloth, GEVA, and Ollama; I've worked with models across sizes (SLM agents to full LLMs) and with the tooling that adapts and steers them.
- **"Create datasets, benchmarks, and metrics to measure model and product performance."** My hybrid quantum-classical project is a compact but complete evaluation exercise: I built a link-prediction pipeline on the Hetionet biomedical graph, compared QSVC/VQC against three classical baselines, and measured the gap (PR-AUC ~0.73 with the quantum model winning). I optimized embeddings with RotatE representations and LDA, and I've kept the whole thing running on IBM Quantum hardware via Qiskit Runtime. The evaluation rigor is the point.

What I'd bring to a consulting practice: the habit of defining the measurable objective first, iterating fast, and documenting the trade-offs — the posture your JD describes for working in "ambiguous problem spaces." I'd also bring unusual ground in quantum-classical ML that, even where your clients aren't near-term quantum customers, is proof of comfort with unsolved, first-principles problems.

**Honest gap I'll name here:** five-plus years and a bachelor's degree are stated requirements. My production ML timeline is longer than that but my degree isn't a traditional CS one — I came through intensive applied programs (Year Up/Pluralsight, ADI Fellowship under a Chief Science Officer) and shipped the systems above. I'd be glad to demonstrate depth in an interview or work sample where the work itself gets judged.

I'd love a conversation about how my RAG and evaluation track record maps to your clients' AI roadmaps.

Best,
Jonathan Beale
