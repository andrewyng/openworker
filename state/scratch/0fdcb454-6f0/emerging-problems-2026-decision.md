# Choosing a 2026 Problem: A Decision Memo (Beyond the Payment/Identity Cluster)

*Prepared 2026-08-18. Complements `metered-web-broker-build-plan.md`. This is the zoom-out: which distinct problem family to own.*

---

## The meta-pattern (the one sentence that matters)

> **The model is no longer the bottleneck. Everything *around* it is: context, trust, where the data lives, portability, and safe execution.**

Every 2026 signal below is a different slice of that. The durable engineering value — and the skill transfer for an engineer strong in cryptography, protocols, and distributed systems — lives in the "around," not the model.

---

## One: the five distinct problem families (with a live "taken vs. open" check)

| # | Problem family | Core challenge | Who's already on it | Status of the gap |
|---|---|---|---|---|
| **1** | **Context engineering** | Get the agent the *right slice* of knowledge, cheaply, and keep it fresh | **Modus** ($10M, Jul 2026) — "context warehouse," Context Miner + Composer | **Largely taken & funded.** "Maintain context over time" sub-niche is thin but Modus is moving there. |
| **2** | **Agent security & governance** | An agent takes a different path every run; *a valid credential only guarantees the door opens* | **Tigera Lynx** (GA, in prod at major banks) — in-path authz/audit, eBPF/LSM, Cedar; **Pylar** — data-access via SQL views | **Defensive layer is taken.** The *verification/audit-reconstruction* and *shadow-agent-discovery* edges are thinner. |
| **3** | **Data architecture fork** | Does the data stay *where it is* (Iceberg/composable) or does logic move *to* the data (Oracle) | **Oracle** 26ai (data gravity); **Google** Iceberg + WebMCP; **Databricks** acquired Neon | **Open, durable, 10-year.** "Reason over live data in place" portability tooling. |
| **4** | **The missing agent PaaS contract** | Same agent artifact (code + memory contract + tools + permissions + evals) must be **versionable, testable, movable across clouds** | AWS/Microsoft/Google all built it **inside a single cloud**; *"no open project answers all three [governance/packaging/state] today"* | **Open, highest-ceiling.** Explicitly called unsolved. But it's a standards/moat play, not a feature. |
| **5** | **WASM safe execution at the edge** | Run **untrusted** tool code / agent-generated code safely & fast, with hard capability boundaries | Partly (GKE Agent Sandbox, AWS microVMs) for *managed* code; **not** for arbitrary third-party *tools* | **Open, young, shippable.** "WASM + agent tooling" intersection is underbuilt. |

**Cross-check against my earlier recommendation:** the in-chat/first-memo instinct was the **payment/identity** cluster (a family #2-flavored broker). The cross-check says: the *defensive* parts of #2 are now **productized** (Lynx, Pylar) and #1 is *funded* (Modus). So chasing "agent identity + a payment rail" as the flagship risks entering a well-funded, already-shipping niche. The **more open, more durable** ground is the **portability + safe-execution** intersection — #4 fused with #5.

---

## Two: the DECISIVE top pick

### **A portable, verifiable agent runtime with a secure tool sandbox.**
*"The missing agent PaaS contract (#4) made concrete and shippable via safe execution (#5)."*

**What it is, in one line:** a neutral runtime that loads an agent as a **single movable artifact** — code, memory contract, tool dependencies, permissions, and eval suite travel together — and runs any **untrusted third-party tool code inside a capability-scoped WASM sandbox**, with **signed identity + scoped delegation + an auditable execution record**.

### Why this one, specifically (it wins on every stated criterion)

| Your criterion | How this pick satisfies it |
|---|---|
| **Durable, not a fad** | Portability and secure execution are 10‑year infrastructure themes. They outlive any single protocol (MCP/A2A/UCP) — the runtime *hosting* them is the durable layer. |
| **Transfers your skills** | **Cryptography** — signed agent artifacts, scoped delegation tokens, verification of tool provenance. **Protocols** — the agent *artifact contract* and the tool boundary are the standards. **Distributed systems** — capability boundaries, sandbox isolation, multi-cloud portability. |
| **Shippable / Vercel-able** | WASM + edge functions is *natively* serverless/edge. This is the only family you can genuinely run on an edge runtime today; the PaaS-contract (#4) and data-fork (#3) are too heavy. |
| **Differentiated (not taken)** | Lynx is an *in-path, single-K8s-cluster network control plane* (vendor, CISO-buyer). This is a **neutral, consumer-side packaging + runtime + sandbox** — "the same agent artifact moves and runs." Different surface, different buyer. |
| **Continuity with your work** | Your Web Bot Auth / signing work *is* the signed-identity layer; your metered-web broker becomes a **built-in metered-access tool** on top. You don't abandon prior work — you climb one level up the stack and it becomes a component. |

### Why not the others (decisive, not wishy-washy)
- **#1 Context — skip as a flagship.** Modus is funded and moving; "context maintenance" is a defensible sub-niche but you'd be #3 in a fast, capital-dense space. (Still worth *using* as a tool, not owning.)
- **#2 Security/governance — the defensive core is taken** (Lynx GA at banks; Pylar). Only the *verification/shadow-agent* edge is open, and it's a thinner, more adversarial build.
- **#3 Data fork — durable, but not your lane.** It's a heavy data-infra play (Iceberg, kernels, storage). Real, but it doesn't leverage your crypto/protocol/edge strengths and isn't edge-shippable.
- **#4 vs #5 on their own — the pick fuses them.** #4 alone is a standards body (slow to monetize, big moat — the "play it for the long game" option). #5 alone is a narrow sandbox product. Fused, you get #4's *ceiling* (the portability contract) delivered as #5's *concrete, shippable MVP* (the sandbox). That's the best risk-adjusted position.

### The MVP, in build order (this is the actual deliverable)
1. **The artifact format (the #4 contract):** a spec + a packager. One command bundles `{agent code, system prompt, tool manifest, memory contract, permission policy, eval suite}` into a single signed, versioned bundle (OCI image as the escape hatch, like the source article notes). **This is your standards contribution.**
2. **The sandbox (the #5 runtime):** load the bundle; run untrusted tool code in WASM with **capability scoping** (no arbitrary syscall/fs/network — exactly the "most dangerous security gap" the sources cite). WASM gives you the boundaries that a plain container/microVM doesn't cheaply, at the edge.
3. **The trust layer (your crypto):** signed artifacts + per-hop scoped delegation tokens (the Lynx/JWT-per-hop pattern, but consumer-side) + a verifiable execution audit record (OpenTelemetry, per the sources) — the thing an auditor or *you* can replay to prove the agent stayed within its authority.
4. **The portability proof (the moat):** the *same bundle* executes unchanged on two different edge/cloud runtimes. That single demo — "runs on X and Y without a rewrite" — is the entire thesis of #4, made concrete. **This is the line the source article says nobody has drawn yet.**
5. **Metered access as a first-class tool:** drop in your existing broker work as a built-in capability (pay-per-tool-call to a third party) — continuity + an immediate differentiator.

### The honest hedge (when this pick is *wrong*)
- If **your goal is fast revenue in 2026**, #4-pure (standards) is too slow and #1/#2 are crowded. For near-term cash, **the metered-web broker (first memo) is still the better short-horizon bet** — it's shippable in weeks and monetizable now.
- If **your goal is a durable, defensible, skill-building position** (which is what you asked for), **the portable verifiable runtime is better** — higher ceiling, transfers your exact skills, and the portability+safe-execution gap is genuinely open as of this research.
- **Decision rule: pick the runtime if you're willing to spend ~a quarter on the artifact spec + sandbox before it's "useful money"; pick the broker if you need shippable value this month.** Both keep your prior work as components.

---

## Three: the ranked shortlist (for the "what can I build in 2026" question)

1. **Portable, verifiable agent runtime + secure WASM tool sandbox** — *top pick* (durable, skill-transfer, shippable, open gap, fuses #4+#5).
2. **Metered-web broker** (first memo) — *best short-horizon / fastest-to-revenue*; the payment/identity/verify cluster, still defensible because the "neutral one-contract + budget/audit" layer isn't owned.
3. **WASM tool sandbox** as a standalone product — *safest, most concrete* if you want one clean shippable box rather than a platform.
4. **Neutral agent PaaS contract (standards body)** — *highest moat, slowest cash*; the long-game governance/play-the-wire move.
5. **Data portability ("reason over live Iceberg in place")** — *most durable of all, but least aligned to your crypto/protocol/edge strengths*; the right pick only if you want heavy data-infra.
6. **Context-maintenance sub-niche** — *open but funded/crowded* (Modus); use as a feature, don't own as a flagship.
7. **Offensive agent-verification / shadow-agent discovery** — *thin, open, adversarial*; a specialist wedge inside the (otherwise-taken) security family.

---

## Four: how this evolves you as an engineer

The trajectory, if you take the top pick: **protocol & cryptographic-interface engineer → platform/standards designer.**

- You go *one level up the stack*: from building a rail (a payment/identity endpoint) to building the **surface agents and tools run on**. That's a durable, defensible, and market-relevant elevation — the "owns the abstraction" move, not the "integrates one more API" move.
- The **three skill compounds** you already have (cryptography, standards-track fluency, distributed-systems boundaries) become the *core* of the product instead of supporting cast. Signing, scoped delegation, capability isolation, and portability are all "distributed systems + trust" problems — exactly your lane.
- **Standards-track ownership** (the #4 artifact contract) is the rare, high-leverage skill: it's the difference between a component and a platform. The source article's explicit thesis — *"Whoever ends up owning the agent lifecycle/contract… will set the negotiating position for the next decade"* — is your opening to be on the right side of that.

**Bottom line (decisive):** Given your skills (crypto, protocols, distributed systems) and your criteria (durable, not a fad, shippable, Vercel-edge-able), **the best 2026 bet is the portable, verifiable agent runtime with a secure WASM tool sandbox** — it is the open, highest-ceiling gap (the missing agent PaaS contract) delivered as a concrete, shippable, edge-deployable runtime, and it lets your existing signing/identity/protocol work climb from "a component" to "the platform." **If you need revenue this month instead, build the metered-web broker (first memo) — and fold the runtime in as the phase *after* you've proven demand.**

---

## Sources (cross-checked this session)
- https://thenewstack.io/modus-enterprise-context-warehouse/  (family #1 — taken)
- https://www.tigera.io/blog/why-we-built-lynx-bringing-control-to-the-age-of-ai-agents/  (family #2 — taken)
- https://www.pylar.ai/  (family #2 data-access — taken)
- https://thenewstack.io/amazon-microsoft-and-google-are-converging-on-the-same-enterprise-agent-architecture/  (families #4, #5 — "no open project answers all three today"; WASM at the edge)
- https://futurumgroup.com/research-reports/data-gravity-in-the-age-of-ai-engineering-the-mission-critical-engine-for-autonomous-workloads/  (family #3 — data gravity vs composable; WebMCP; Databricks/Neon)
