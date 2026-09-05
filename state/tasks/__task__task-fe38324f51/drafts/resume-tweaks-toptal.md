# Resume Tweaks — Toptal: AI/ML Engineer, AI-Driven E-Commerce

**Evaluation axes from the posting:** LLM/agent building, fine-tuning open models
(Llama/Mistral), GPU infra, full-stack (MongoDB/Express/React/Node), production
REST APIs, and (nice-to-have) Stripe/Twilio/OpenAI integrations.

## Reframe the headline
- First line under your name: **"AI/ML Engineer — LLM agents, RAG, and
  production ML systems (Python · PyTorch · cloud MLOps)".** The posting's title
  is "AI/ML Engineer"; your current title "ML Solutions Architect" undersells the
  agent focus. Put the *engineer* + *agents* framing up front.

## Lead with (reorder top of page)
1. **Mutual of Omaha — agentic ETL + RAG.** This is the strongest direct match to
   "architect and implement autonomous AI agents."
   - Rewrite the agent bullet to name the loop: "Designed and shipped an
     **autonomous-agent pipeline** (SLMs as agents) that deduplicates claim records
     end-to-end, with a **RAG** system for semantic document search."
   - Use the posting's words: **autonomous agents**, **conversational/search
     workflows**, **production**.
2. **Hewani — recsys + inference APIs.** This maps to "dynamic site
   customization" + "customer-facing commerce" + "production APIs."
   - Emphasize: "personalized product suggestions at scale" (their words: product
     suggestions / recommendations) and "RESTful inference APIs consumed across
     multiple app surfaces."
   - This is your closest thing to e-commerce personalization — surface it hard.

## Close the two real gaps (only if true — do NOT claim experience you don't have)
- **Fine-tuning open models:** The resume lists Unsloth, Ollama, DSPy in skills but
  no fine-tune project. If you *have* fine-tuned Llama/Mistral (even as a
  side project), add one project line with the model name + what you fine-tuned +
  eval. If you haven't, **do not claim it** — instead the cover letter already
  offers a hands-on task. Honesty wins the Toptal screen.
- **Full-stack:** You have TypeScript + Next.js + REST integration. Rephrase the
  Next.js/TypeScript skills and the "REST APIs across multiple app surfaces" line so
  the reader sees **frontend + backend + API** in one profile. If you've touched
  Mongo or React specifically, list them; if not, leave them out.

## Rephrase into posting vocabulary (no new claims needed)
- "personalized content and product suggestions" → **product recommendations /
  dynamic product surfaces**.
- "RESTful inference APIs" → **production REST API integrations**.
- "monitoring and retraining pipelines" → **deployment + MLOps lifecycle** (this is
  their "GPU infrastructure + deployment" axis — your strongest infra signal).
- "SLMs as autonomous agents" → **autonomous AI agents**.

## Cut / demote
- **NRG + Snap internships** — not relevant to this tech stack; drop to one line
  or remove to make room for the agent + recsys bullets to shine.
- **Quantum projects (Hetionet QML, QCrypt RNG)** — real differentiators but *not*
  this posting's focus. Keep to a compact one-liner each under Projects ("I do
  research-grade, high-stakes ML") and move them *below* the two LLM/production
  projects so the LLM-agent story leads. Do not cut them entirely — they're your
  strongest proof of depth, just reorder.
- **R (Beginner), Rust (Beginner)** — remove from the language line; they dilute
  the Python/TS signal for this role.

## One-sentence "why me" to prepend (optional but powerful)
"I've built autonomous LLM agents and owned them to production — exactly the
agent-led commerce work in this posting — and I can walk your team through a
hands-on fine-tune/build task against your stack before commit."
