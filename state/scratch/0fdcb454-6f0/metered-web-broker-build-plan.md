# Build Plan: A Neutral "One-Contract" Metered Web Broker for AI Agents

*Prepared 2026-08-18. Status: recommendation for building a flagship product in the agent-protocol space.*

---

## TL;DR

The agent infrastructure stack is converging on a stable set of patterns (well-known-URL discovery, typed schemas, signed mandates, standard event streams). Most *single-protocol* bridges are already built. The open, high-leverage gap is **the layer that sits above the protocols**: a neutral broker that lets an agent buy **metered web access** — and quietly absorbs the three rail problems underneath it (payment via 402/x402/Cloudflare, licensing via RSL, and identity/attestation via PACTs / Web Bot Auth), with budget guardrails and an audit trail.

You are well-positioned for it: it is infrastructure + signing + budget-enforcement work, deployable on Vercel, and it is the exact layer enterprises will need once the metered web becomes mandatory.

---

## 1. What the hyperscalers are converging on (the map)

Google's March 2026 developer guide is the cleanest mental model — six protocols, one boundary each:

| Protocol | Solves | Maturity |
|---|---|---|
| **MCP** | agent → tools/data | mature, large ecosystem |
| **A2A** | agent → agent (discovery via `/.well-known/agent-card.json`) | maturing, cross-vendor |
| **UCP** | commerce lifecycle (typed checkout, `/.well-known/ucp`) | new |
| **AP2** | payment authorization (typed mandates, guardrails, audit) | new, x402/stablecoin rails |
| **A2UI** | agent → UI (18 declarative component primitives) | new |
| **AG-UI** | agent → frontend streaming (typed SSE events) | new |

The platforms ship the *runtime* around it:

- **Google — Gemini Enterprise Agent Platform** (Apr 2026, evolved from Vertex AI): Agent Identity (cryptographic IDs), Agent Registry, Agent Gateway, Memory Bank, multi-day long-running agents, Agent Sandbox.
- **AWS Bedrock AgentCore** (Apr 2026): managed harness, per-session microVMs, filesystem persistence (suspend/resume mid-task), IaC deployment via CDK/Terraform.
- **GKE Agent Sandbox**: gVisor-isolated execution, ~300 sandboxes/sec at sub-second latency.

The through-line: **identity → discovery → trust → transaction → execution → observability**, moving from "you build it" to "the platform provides it, you plug in."

**Key takeaway: build against the *patterns*, not any one spec's current draft form.** UCP, AP2, A2UI, AG-UI are all new and churning; the patterns (well-known-URL discovery, typed schemas, signed mandates) are stable.

---

## 2. The metered web: the two rails that matter in 2026

From serp.fast (June 2026) and the x402/AP2/spec sources, the web now has **two rails** for agents:

- **Payment rail** — "what a fetch costs":
  - **HTTP 402** (revived by x402, Coinbase/LF, May 2025): server returns `402` + `PAYMENT-REQUIRED`; client signs `PAYMENT-SIGNATURE`; a facilitator `/verify`/`/settle` settles on-chain (USDC on Base/Ethereum/Solana; `exact` / `upto` / `batch-settlement` schemes). `batch-settlement` matters for high-volume crawls.
  - **Cloudflare Pay Per Crawl** (live since Jul 2025): CDN-level gate, Stripe-connected, Cloudflare as merchant-of-record; crawler advertises `crawler-max-price`.
  - **Self-hosted 402 gates** + a Stripe-issued access key.
- **Verification rail** — "are you allowed to fetch at all":
  - **RSL** (Really Simple Licensing, RSL Collective, Sep 2025): machine-readable license terms; supports pay-per-crawl *and* pay-per-inference.
  - **PACTs** (Private Access Control Tokens, Cloudflare + Chrome + Edge, Jun 2026): attested identity for sanctioned bots.
  - **Web Bot Auth / signed `/.well-known/http-message-signatures-directory`** (the IETF-draft identity work you've already been reading).

Pressure signal: automated traffic crossed **57.4% of web requests** (June 2026, Cloudflare measurement) — the first time machines outnumber humans. This is why the plumbing appeared.

---

## 3. What is already built (don't re-build)

- **AP2 ↔ x402 bridge**: `ap2-x402-bridge` (open-source, Bonanza Labs) — "the only open-source AP2↔x402 converter." Both directions, auto-detect. **Taken.**
- **AP2's own x402 rail**: AP2 ships a production `rail-adapter-x402` with `control-plane` → `rail-adapter-x402` → Coinbase x402 (escrow, liquidity, compliance), and flow types (`immediate_capture`, `escrow_release`, `milestone`, `usage_metered`). **Taken.**
- **A2UI/AG-UI renderers**, **identity/key-directory** services: partially building on the platforms.

So "connect two payment protocols" is a saturated, already-solved niche. **The open gap is above it.**

---

## 4. The flagship: a neutral "one-contract" Metered Web Broker

**One line:** an agent calls *one* endpoint and gets content + a license + a settlement receipt; the broker handles the 402s, the licenses, the identity, the budget, and the audit — "one contract instead of negotiating with every origin."

### Why this, and not a protocol bridge
- It is the exact thing the serp.fast write-up says builders want but nobody owns as a neutral layer.
- It is *your* lane: infra + signing + budget enforcement + observability, deployable on Vercel, enterprise-relevant (the enterprise money is in trust/governance, not in the raw protocol).
- It differentiates on **behavior**, not on re-plumbing a spec.

### Architecture sketch

```
        ┌───────────────────────────────────────────────────────────┐
        │                METERED WEB BROKER (neutral)               │
   ┌────┴────┐      ┌──────────────┐   ┌────────────────────────┐   │
   │  Agent  │──────▶│   Gateway    │──▶│   Budget Engine        │   │
   │(MCP/A2A)│  1 call│ (auth + route)│  │ (ceiling, alert, meter)│   │
   └────┬────┘      └──────┬───────┘   └───────────┬────────────┘   │
        │                  ▼                        │                │
   ┌────┴────┐      ┌──────────────┐   ┌────────────────────────┐   │
   │  Policy │◀─────│  Rail        │   │  Identity/Attestation  │   │
   │  (AP2   │      │  Adapter     │   │  (Web Bot Auth / PACTs)│   │
   │ mandates)│     │  ┌─────────┐ │   └────────────────────────┘   │
   └─────────┘      │  │ x402    │ │                                 │
                    │  │ CloudFLR│ │  ┌────────────────────────┐     │
                    │  │ Self402 │ │  │  License Engine (RSL)  │     │
                    │  └─────────┘ │  │  (terms → enforceable) │     │
                    └──────┬───────┘  └────────────────────────┘   │
                           ▼                                        │
                    ┌──────────────┐      ┌──────────────────────┐   │
                    │   Facilitator│      │  Audit / Reconcile   │   │
                    │ (settlement) │      │ (ledger, receipts,   │   │
                    └──────────────┘      │  block vs pay vs deny)│ │
                                         └──────────────────────┘   │
        └───────────────────────────────────────────────────────────┘
```

**Core components:**
1. **Gateway** — single MCP/A2A tool: `fetch(url, budget)`. Auth, routing, and protocol negotiation live here.
2. **Budget Engine** — the differentiator. Per-request ceiling + recurring line-item ceiling + alert (treat fetch cost like token cost). Distinguishes and reports `blocked` / `payment-required` / `token-rejected` *separately* (today they collapse into a generic failure and hide the cost decision).
3. **Rail Adapter** — one interface (`authorize/verify`, `pay`, `settle`, `receipt`) with pluggable backends: x402 facilitator, Cloudflare Pay Per Crawl, self-hosted 402. Swappable without touching the agent.
4. **Identity / Attestation** — signs requests (Web Bot Auth), presents PACT-style tokens where accepted, and keeps the `http-message-signatures-directory` + Ed25519 key lifecycle (issue, rotate, tight `expires`) as a first-class concern.
5. **License Engine** — reads RSL terms, enforces them post-fetch (attribution, no-store, pay-per-inference), and surfaces the obligation in the receipt.
6. **Audit / Reconcile** — the ledger: every fetch → `{status, price, rail, license, identity, settlement_ref}`. The enterprise surface.

### Why these six, and the build order that earns trust
Budget and Audit first (that's where the value is), Rail Adapter second (that's where the complexity lives), Identity third (that's where you differentiate from a dumb proxy), License last.

---

## 5. Phased roadmap

**Phase 0 — Conformance harness (1–2 weeks, ship first)**
A CI tool that validates an agent's A2A card, AP2 mandate shape, UCP checkout, and Web Bot Auth signature against *current drafts*, and flags when a draft bumps.
- *Why first:* low-risk, fast to ship, immediately useful to every team, distribution channel into the ecosystem, and it keeps *you* current on the churn.
- The Google guide's own advice is "check for an official SDK before building" — this harness *is* that check, productized.

**Phase 1 — Budget + Audit core (2–3 weeks)**
- Budget Engine with ceiling, metering, and the three distinct failure classes.
- Audit ledger schema + a small dashboard.
- *Goal:* an agent that can already see its metered-web spend and explain every denial. This is the moat.

**Phase 2 — Rail Adapter (2–3 weeks)**
- One clean interface, three backends (x402 facilitator, Cloudflare Pay Per Crawl, self-hosted 402).
- Use the existing `ap2-x402-bridge` patterns as reference; do **not** fork it, build the adapter around it.
- *Goal:* any 402/Cloudflare pay-per-crawl origin works behind one tool.

**Phase 3 — Identity / Attestation (2–3 weeks)**
- Ed25519 key lifecycle, http-message-signatures directory hosting, signing middleware, PACT presentation.
- *Goal:* the broker is a *sanctioned* bot, not an anonymous one.

**Phase 4 — License Engine (1–2 weeks)**
- RSL parsing + post-fetch enforcement + receipt surfacing.
- *Goal:* compliance, not just access.

**Phase 5 — Enterprise surface (ongoing)**
- Multi-tenant, policy templates, SSO, statement export (mirror AP2's `GET /rails/x402/statements`).
- *Goal:* the "govern" pillar Google/AWS are selling, delivered as one endpoint.

---

## 6. Honest caveats (draft-caveat discipline)

1. **Specs are moving.** x402 v2, AP2, UCP, A2UI, AG-UI are all maturing; PACTs and Cloudflare Pay Per Crawl are still settling (the serp.fast piece flags two open questions: which payment rail wins, and whether PACT-style attestation hardens into a gate). **Build against patterns, not a draft's exact field names.**
2. **Coincide, don't clone.** Cloudflare and the platforms are building parts of this. Your wedge is *neutrality + the budget/audit layer* — not competing with their rails, and not re-implementing AP2↔x402 (already done by `ap2-x402-bridge` and AP2's `rail-adapter-x402`).
3. **The bridge is taken.** If you were going to lead with "AP2↔x402 bridge," *don't.* It is an occupied niche; lead with the broker above it.
4. **Metered-web economics are unproven.** The 57.4% automation stat is the demand signal, but the per-crawl price floor and settlement finality (esp. `batch-settlement` reconciliation) are still being worked out. Phase 2 depends on rail stability — sequence it *after* Budget/Audit so the moat exists regardless of which rail wins.

---

## 7. How this shapes the engineer

The meta-skill is **protocol engineering**: designing typed, discoverable, cryptographically-signed interfaces between autonomous actors.
- **Cryptography in practice** — Ed25519, JWK thumbprints, message-signature bases, replay windows (already touching it).
- **Standards-track fluency** — reading IETF drafts (you have `draft-meunier-web-bot-auth-architecture-05` open), understanding structured-field vs. dictionary verification. Rare and valuable.
- **Trust & governance architecture** — mandates, guardrails, identity, audit trails. This is where the enterprise money is (Google's "Govern" pillar, AWS's "governance and audibility of IaC").

**Recommendation:** lead with the **Metered Web Broker** (Phases 1–4). Ship the **conformance harness** (Phase 0) first as the fast-win and distribution channel. Do **not** lead with an AP2↔x402 bridge — that niche is already filled.

---

## Sources
- https://developers.googleblog.com/developers-guide-to-ai-agent-protocols/
- https://cloud.google.com/blog/products/ai-machine-learning/introducing-gemini-enterprise-agent-platform
- https://aws.amazon.com/about-aws/whats-new/2026/04/agentcore-new-features-to-build-agents-faster/
- https://cloud.google.com/blog/products/containers-kubernetes/whats-new-in-gke-at-next26
- https://pratikdhanave.com/blog/posts/agentic-commerce-protocol-stack.html
- https://serp.fast/blog/pay-per-crawl-web-access-layer-2026
- https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md
- https://docs.x402.org/core-concepts/http-402
- https://github.com/google-agentic-commerce/AP2/
- https://agentpaymentsprotocol.info/docs/payments-rail/
- https://pypi.org/project/ap2-x402-bridge/
