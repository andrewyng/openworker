# Cover Letter — CrowdStrike, AI Platform Engineer (Remote)

[Your address line]
20 August 2026

Hiring Team, CrowdStrike
AI Platform Engineering

**Re: AI Platform Engineer (Remote)**

Dear Hiring Team,

I am writing to apply for the AI Platform Engineer role. You are looking for an engineer who is comfortable at the intersection of applied LLM work, containerized deployment, and the operational discipline required to serve AI at CrowdStrike's scale (~3 trillion events/day). I have held that position in production twice in the last two years, on systems whose failure modes are measured in real business outcomes rather than benchmarks.

At Hewani I own the full deployment lifecycle of a production ML recommendation system on Google Cloud Vertex AI: infrastructure provisioning, containerization with Docker, the REST inference APIs that other product surfaces consume, and the monitoring and retraining pipelines that keep it behaving. At Mutual of Omaha I designed and deployed a RAG system for querying large insurance-document corpora, and an ETL pipeline using small language models as autonomous agents to deduplicate medical and insurance claim records. Both were cases where the interesting engineering was not the model itself but the platform around it: reliability of serving, failure modes of the pipeline, and how to make the system's output trustworthy enough to put in front of end users and downstream systems.

The posting's requirements map onto what I already do: Python and TypeScript as working languages; REST API design as a core deliverable; Docker for deployment; Google Cloud Platform as a primary target; and HuggingFace Transformers, LangChain, and PyTorch as the LLM tooling. One area I want to be candid about rather than gloss over: the JD lists Kubernetes, Terraform, and OAuth2/OIDC as requirements, and I have direct production use of Docker and REST security patterns but less depth in K8s/Terraform and non-human-identity systems than a longer-tenured platform engineer. Given the platform scale CrowdStrike operates at, I would want to confirm fit on those three areas early — I have spent the last year specifically building on managed cloud AI infrastructure (Vertex AI) rather than self-hosted Kubernetes, and I think an early technical conversation would let both sides see how much headroom I have on that specific axis.

What I'd bring to the platform team that is less common: production experience with LLM-agent pipelines at the data layer, and (from a separate research track) a working command of Qiskit, PennyLane, and quantum-classical ML, which I would be glad to use for anything in the platform's research-adjacent workload where a fresh angle on inference or optimization helps.

I would welcome the chance to walk through the RAG/ETL and Vertex AI deployment work in detail at a technical screen.

Jonathan Beale
jonaston015@gmail.com | (954) 494-0671
linkedin.com/in/jonathan-beale | github.com/iconbaypark2900

---
*Evidence cross-check (do not include in submission):*
- Vertex AI, Docker, REST, monitoring/retraining → resume, Hewani + Technical Skills.
- RAG, SLM-agent ETL, Mutual of Omaha → resume.
- Python/TypeScript/PyTorch/HF/LangChain → resume, Technical Skills.
- K8s/Terraform/OIDC gap — stated honestly, not claimed.
- Nothing on this letter claims Kubernetes, Terraform, or OIDC experience as held.

## Note for the user
This role is ranked below Dragos and Perplexity in the shortlist, in part because I could only read the JD via aggregator snippets (Workday page didn't render in this run), and because K8s/Terraform/OIDC are in the requirements stack while the resume's evidence is Docker/GCP/REST. Apply to this one after (or instead of, if you want more volume) the top two, and read the full JD on the live posting before sending.
