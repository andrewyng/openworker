# Cover Letter — Perplexity, Research Residency

[Your address line]
20 August 2026

Hiring Team, Perplexity Research
San Francisco, CA / New York, NY

**Re: Research Resident, Perplexity Research Residency — 2026**

Dear Hiring Team,

I am applying to the Perplexity Research Residency because it is one of the very few programs in AI that explicitly recruits engineers with deep, unusual expertise in a single area and asks them to bring it into frontier AI work — and that description matches how I have built my career over the last two years.

In production, I work where applied LLM systems meet messy, high-stakes data. At Mutual of Omaha I architected a RAG system for semantic querying over large insurance-document corpora, and I built an ETL pipeline in which small language models acted as autonomous agents to deduplicate medical and insurance claim records. In that environment the research question is never "can the model do X" but "when can I trust the model on X, and how do I make its failures visible before a claims reviewer hits them?" That is the same question your Research Residency framing — humans actually working, thinking, and seeking information — puts at its center.

What I hope to contribute to Perplexity is a second depth that most applicants to an AI residency will not have: applied quantum-classical machine learning. I designed and built a hybrid QML pipeline on the Hetionet biomedical knowledge graph that predicts disease–gene and drug–target relationships, comparing quantum-support-vector and variational-circuit models (Qiskit, targeted at IBM quantum hardware backends) against classical baselines, achieving a PR-AUC of ~0.73 with the quantum model outperforming them. I also built a post-quantum random-number generator on Qiskit and PennyLane and validated its output against NIST randomness test suites. I have used quantum computing against real data, on real (noisy) hardware, with classical ablations. Whatever Perplexity's next hard problem is — inference under novel constraints, new randomness in a deterministic stack, or a fresh view on an old optimization — a person who can reason across that boundary is a useful add.

Beyond the narrow area, I have the applied AI tooling the residency expects: PyTorch, HuggingFace Transformers, LangChain, DSPy, REST API design, containerization with Docker, and full deployment on Google Cloud Vertex AI (Feature Store, Matching Engine, retraining pipelines) that I engineered end to end at Hewani, where I also own the inference-surface APIs other teams consume.

I would welcome the chance to start this residency within your eight-week onboarding window and to bring my QML and applied-LLM experience into Perplexity's research teams. I have attached my resume, my live demo of the QML link-prediction system (hetqml-web.fly.dev/initialize), and the source repository.

Thank you for your consideration.

Jonathan Beale
jonaston015@gmail.com | (954) 494-0671
linkedin.com/in/jonathan-beale | github.com/iconbaypark2900

---
*Evidence cross-check (do not include in submission):*
- RAG insurance system → resume, Mutual of Omaha bullet 1.
- SLM-agent ETL + dedup → resume, Mutual of Omaha bullet 2.
- QML PR-AUC ~0.73, Qiskit, Hetionet → resume, Projects, Hybrid QML Biomedical Link Prediction.
- NIST suites → resume, Projects, QCrypt RNG.
- PyTorch/HF/LangChain/DSPy → resume, Technical Skills.
- Vertex AI, Docker, REST APIs → resume, Hewani bullets + Technical Skills.
- Live demo URL + repo → resume, Projects. Nothing added that is not on the resume.
