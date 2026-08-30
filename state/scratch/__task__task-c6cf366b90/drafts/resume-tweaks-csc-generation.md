# Resume Tweaks — CSC Generation
## Role: Senior Machine Learning Engineer, Causal & Decision Systems (Remote US / Toronto, posted 2026-08-15)

Target reader: a founder-led ML hire at a company building "closed-loop decision systems" with "measurable economic lift in controlled experiments" as the north-star metric. Their JD keywords in priority order:

> Recommendation, advertising, pricing, marketplace, credit, or other decision systems; causal inference and experimentation; bandits / RL / optimization / active learning; uncertainty estimation; counterfactual and off-policy evaluation; champion/challenger; production ML infra; Python, SQL, large behavioral datasets.

## What to lead with (re-order top of resume)

1. **Hewani (Apr 2025 – present)** — lead with this one. It is the only "recommendation system" on the resume and it's in a consumer/personalization domain, which is the posting's closest domain match.
   - First bullet: "Recommendation system on Vertex AI (Feature Store, Matching Engine) serving *personalized content and product suggestions at scale*" — the phrase "product suggestions at scale" is the single keyword overlap with the posting, so keep it verbatim.
   - Second bullet: "RESTful inference APIs across multiple app surfaces, owning the full deployment lifecycle" — that's the "production ML infrastructure, monitoring, and automated deployment" match, which is listed as the last line in their "What You'll Work On" block.
   - Add one line if you can say what the online feedback signals were (click, conversion, watch time, add to cart, etc.). CSC Generation's whole model is "decisions made by the model influence the data the model sees next"; the closest analog you own is a personalization loop where clicks/conversions flow back into retraining. Name it.

2. **Mutual of Omaha (Jan 2024 – Feb 2025)** — second.
   - Reframe the RAG bullet to lead with the *decision*: "RAG system over insurance document corpora reducing manual review time" is a defensible, honest line; the framing I'd use at CSC is "large sensitive corpus, LLM in the loop, human reviewer in the chain of trust" — matches their "safely automate an increasing share of real commercial decisions" without claiming causality work I don't have.
   - Reframe the SLM-agent dedup bullet to lead with the *record linkage* rather than the "agents" language — CSC cares about the decision loop more than the framework.
3. **Hybrid QML (2024 – Present)** — third. Keep it public and visible; it's one of the few signals of self-directed, baseline-honest, reproducible ML work, which is exactly the kind of thing a founder-led team screens for.
4. **CSC-specific additions to make**

- **Add one line on offline-vs-online evaluation, if you actually did any.** If you ran A/B tests on the Vertex AI recommendation system at Hewani, name the mechanism (even "A/B tested feature store updates") — that's the closest analog to "champion/challenger systems" I have. If you didn't, don't put it on the resume; the cover letter handles the honest gap.
- **Add one line on "uncertainty" only if you actually computed or compared confidence scores, thresholds, or calibration curves** somewhere in the two production roles. If not, leave the word out — "uncertainty estimation and calibration" is a named JD keyword they'll likely probe.
- **Move to a separate "Selected Academic & Technical Work" section** the NRG energy-policy-research intern and the Snap EAT-vertical marketing-strategy intern. They don't map onto any line in this JD and they dilute an ML profile.

## What to rephrase in the posting's language

- "recommendation system" → "personalized decision system" is the safe rephrase: it's honest (it is a decision system, it's not causal) and it lands the "decision systems" keyword twice in the resume.
- "reducing manual review time" → "reducing manual review time in a regulated document workflow" — this is the closest analog to "safely automate an increasing share of real commercial decisions" without claiming a causality result I haven't produced.
- "ETL pipeline using small language models" → "agent-based ETL pipeline" — the word "agent" is used in the JD in one spot (the "agentic" context); keeping it on the resume is fine but the rephrase should not lean on the phrase "agents as a selling point." Frame it as a *decision loop* (claim record, transformation, downstream review) and let the agent framing sit in the second position.
- "AI ethics discussions facilitated" → cut for CSC. Not on their keyword list; save it for the GovAI version.

## What to cut

- NRG energy policy research intern.
- Snap EAT-vertical marketing intern.
- "Facilitated discussions on AI ethics" line — one line, only if it can be framed as *responsible AI in production ML*, otherwise remove it.
- The QCrypt RNG project — keep only if you have space. It's a strong signal of benchmark honesty (NIST-suites), which does map onto the JC's "measurable economic lift in controlled experiments"; compress to 2 lines and make sure the NIST-suites line is visible.

## What I'm NOT going to do

- Claim a bandits / RL / sequential-decision track record. The resume shows zero of it. The cover letter is honest about the gap. Add a "Causal & Decision Systems" line to the projects as a *learning-in-progress* item only if you've actually started reading or running the literature; otherwise don't add it.
- Add "5+ years" or "senior" language to the headline — the posting doesn't explicitly say 5+ years, but "exceptional technical ability and judgment" is in their "Why This Role Is Different" block and the applicant bar is clearly senior. I want to be judged on the work rather than on a title.
- List PyTorch, TensorFlow, JAX — the JD doesn't name a framework ("we care about selecting the right method, not using a particular framework"). The frameworks list on the current resume (PyTorch, LangChain, PyTorch, DSPy, Unsloth, Ollama, HuggingFace) is fine to keep, but don't add a framework you haven't used.
