# Cover Letter — Anthropic Fellows Program (January 2027 cohort)

**Applicant:** Jonathan Beale
**Contact:** jonaston015@gmail.com · (954) 494-0671 · linkedin.com/in/jonathan-beale · github.com/iconbaypark2900
**Workstream preference:** ML Systems & Performance Fellows (with AI Safety second)

---

To the Anthropic Fellows Program recruitment team,

I'm applying to the Anthropic Fellows Program because I want to move from building production ML systems to helping ensure the systems at the frontier are safe, reliable, and steerable — and the Fellows program is the most direct route I know from applied engineering to that kind of empirical research.

**Where I've been doing the work.** As ML Solutions Architect at Hewani I built an end-to-end recommendation system on Google Cloud Vertex AI — Feature Store, Matching Engine, full deployment lifecycle from provisioning and containerization to monitoring and retraining. Before that, at Mutual of Omaha I architected a RAG system for querying large insurance document corpora, and built an ETL pipeline in which small language models act as autonomous agents to deduplicate medical and insurance claim records. That work — models as components you must make reliable, observable, and recoverable inside a larger system — is what drew me to the ML Systems & Performance workstream.

**What I bring to empirical research.** My independent projects have taken the "build the system, then measure it" discipline further than my day job asks of me. On a hybrid quantum-classical pipeline over the Hetionet biomedical knowledge graph, I trained QSVC and VQC models, benchmarked them against logistic regression, SVM, and random forest baselines (PR-AUC ~0.73, with QSVC ahead of the classical baselines), and pushed experiments to IBM Quantum hardware backends via Qiskit Runtime. Separately I built a post-quantum RNG from quantum circuit primitives and validated its statistical randomness against the full NIST test suites — i.e., I'm comfortable with experiments where the claim is checkable and the failure mode is embarrassing if you fudge it. Neither project had a roadmap handed to me; both shipped as public, reproducible artifacts (hetqml-web.fly.dev, public GitHub repo).

**Honest gaps, and how I'd close them.** I have no publications and no prior formal safety research — I know exactly what I'd be leaning on the mentors for. What I bring instead is the unglamorous systems layer that underneath it all — containerization, inference APIs, monitoring, retraining pipelines, and an agentic ETL system where a bad model decision corrupts downstream data, which forces you to design the constraints and evaluations first. That's the operational side of keeping agentic systems trustworthy, and it's where I'd like the next four months to take me, with Anthropic researchers setting the research questions.

I'm fluent in Python, available full-time for the January 2027 cohort, and based near Florida — I'd note any start-date or workspace logistics needed at application time. I'd be honored to join the next cohort.

Thank you for considering my application.

— Jonathan Beale
