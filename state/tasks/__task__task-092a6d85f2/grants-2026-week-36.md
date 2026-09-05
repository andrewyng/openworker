# Grant + Funding Digest — W36 (Aug 31, 2026)

**Profile (unchanged):** Jonathan Beale — ML/AI engineer (RAG, LLM agents, recommender systems on GCP, agentic ETL) with independent projects in hybrid quantum-classical ML (Hetionet biomedicine, Qiskit/PennyLane, IBM QPU backends) and post-quantum cryptography. No academic institution, no prior major awards, and **no indication of a registered company** — the single most important eligibility fact (see Bottom line).

**Continuity / how this run compares to prior digests.** This updates and supersedes `grants-2026-week-34.md` (Aug 20); the week-35 output is not present in the workspace, so those 14 days were not captured — this run carries the week-34 state forward and refreshes every hard date it can. Everything in week-34's rolling set is re-checked, not rediscovered. All hard dates below are read directly from the funder pages this run (except where noted).

### State changes flagged this run (worth quoting so they aren't lost)
- **[RE-OPENED] Corrigibility Research Fund** — week-34 closed it as "closed Aug 23." The fund's own site (corrigibilityresearch.org) and AISafety's tracker now show it **reopened and rolling** with an internal **close of Oct 31, 2026**, **retroactive awards for 2026 work, and NO application form** (apply by emailing grants@corrigibilityresearch.org). Treat as live, not dropped.
- **NIH SBIR — currently EMPTY on the portal.** seed.nih.gov returns *"No SBIR/STTR funding opportunities found."* After the May 2025–Apr 2026 authorization lapse, NIH's NOFOs are still being reissued, so there is **no open NIH R43 to apply to right now** — the Sep 5, 2026 receipt date is effectively in limbo. The next firm date is **Jan 5, 2027**.
- **2026 SBIR is in a compressed post-reauthorization cycle.** The Small Business Innovation and Economic Security Act reauthorized SBIR/STTR through 2031; agencies are front-loading solicitation windows before the Sep 30 fiscal-year close, and **every SBIR application now goes through mandatory foreign-risk screening** (ownership/patents/employee backgrounds/financial ties). DoD releases run on a tighter 4-week window this year (Release 5: opened Aug 26, closes Sep 23).
- **DARPA Release 5 / DV019** opens **Aug 26, 2026** and closes **Sep 23, 2026 12:00 PM ET** — 23 days out, the soonest hard deadline on the list.

---

## URGENT — due within 14 days (by ~Sep 14)
**None.** No open program with a fixed deadline falls inside the 14-day window. The soonest hard deadline is **DARPA "Semantically-Aware ISR" (DV019) on Sep 23** — listed in the next block because it is the next real gate, not because it is in-window. The rolling independent-researcher grants below have no deadline, so they are always "soon" if you want to submit.

---

## Open now — hard dates, soonest first

### [SBIR] DARPA Release 5 — "Semantically-Aware ISR" (DV019) — DUE SEP 23
- **Funder:** DARPA / DoW (Information Processing Techniques). Release 5 pre-released Aug 5, 2026, opened for submissions Aug 26, closes **Sep 23, 2026 12:00 PM ET**.
- **Award:** Phase I typically ~$200K–$250K over ~6 months.
- **Eligibility:** US small business (<500 employees, ≥50% US citizen-owned), SAM.gov enrollment, VCOC SBIR certification. Subject to 2026's mandatory foreign-risk screening. Topic: mission-aware semantic-comms capability that shrinks transmitted multimodal ISR data while preserving understanding — ML + large-model reasoning over comms data.
- **Link:** https://www.darpa.mil/work-with-us/communities/small-business/sbir-sttr-topics
- **Honest fit:** Strong *topical* match to your RAG/LLM-agent/structured-data strength, but **you cannot apply without an incorporated entity, filed well before Sep 23** — and the "well before" is real: SAM enrollment + VCOC certification + topic fit take time. Flag in week-33's terms: this is the recovery plan you should only act on if you file an LLC/SAM this week.

### [SBIR] NSF 26-510 / 26-511 — SBIR/STTR "Deep Technologies" — full proposals due NOV 4, 2026
- **Funder:** NSF (America's Seed Fund).
- **Award:** Phase I up to ~$275K–$305K (12 mo); Phase II up to ~$1M; Fast-Track combines them. 26-511 carries a ~$40M scientific-instrumentation pilot emphasis.
- **Deadline:** The **full Phase I / Fast-Track proposal gate is Nov 4, 2026** (verified on seedfund.nsf.gov this run: applicants with a *Project-Pitch invitation* issued ≥2025 may submit by Nov 4, 2026). You need a **Project-Pitch invitation**, which is issued on a rolling basis from an early pitch — the realistic pitch window to make the Nov 4 deadline is **through roughly early September**.
- **Eligibility:** US-registered for-profit <500 employees, majority US-owned, majority US control; project in a funded deep-tech topic (AI/ML and **quantum technology both explicitly in scope**); first-time applicants explicitly welcomed; mandatory foreign-risk screening.
- **Link:** https://seedfund.nsf.gov/solicitations/ · full solicitation: https://www.nsf.gov/funding/opportunities/small-business-innovation-research-small-business-technology/nsf26-510/solicitation
- **Honest fit:** **Best SBIR on-ramp for you** — the Project-Pitch is a short, low-commitment document, quantum-ML/PQC is squarely in scope, and your live `hetqml-kg-poc` repo is real evidence. **Still requires a US entity.** If you incorporate, NSF (Nov 4) + DARPA (Sep 23) are two independent shots at the same project.

### [SBIR] NIH SBIR (R43) — next firm date JAN 5, 2027
- **Funder:** NIH/HHS.
- **Award:** Phase I (R43) up to ~$295K; Phase II (R44) up to ~$2M; Fast-Track available.
- **Deadline:** **Sep 5, 2026 is currently not active** (NOFOs are mid-reissue — see state change above). **Next firm date is Jan 5, 2027.**
- **Eligibility:** US for-profit small business; project must fit an NIH institute mission (biomedical/health, commercial translation).
- **Link:** https://www.seed.nih.gov/ (currently shows no open opportunities — verify before the Jan 5 date)
- **Honest fit:** Your Hetionet disease-gene / drug-target link prediction is the strongest *topical* biomedical asset on this whole list, but NIH SBIR reviewers expect **experimental validation and an institutional collaborator** for drug-discovery-adjacent work. **"Possible with a partner, not a solo play"** — and only worth it if you incorporate.

### [SBIR] DoD Open Topics / CSOs — rolling, apply any time
- **Funder:** DoD components (Army/Navy/Air Force/Space Force/DARPA/MDA) via Defense SBIR/STTR.
- **Award:** Phase I $50K–$275K; Phase II $750K–$1.8M.
- **Deadline:** **Rolling — Open Topics and Commercial Solutions Openings never close** (separate topic BAAs like Release 5 have their own windows).
- **Eligibility:** US for-profit small business. AI/autonomy, quantum information science, and cybersecurity are all named 2026 focus areas.
- **Link:** https://defensesbirsttr.mil/SBIR-STTR/Opportunities/ · https://defensesbirsttr.org
- **Honest fit:** Reachable only with an entity; your PQC/quantum-RNG and agent work are plausible Open Topic fits. Rolling window = no timing pressure, so low priority unless you're incorporating anyway.

### [SBIR] DOE SBIR/STTR — next window unconfirmed
- **Funder:** DOE (EERE, Office of Science, ARPA-E, etc.).
- **Award:** Phase I $200K–$250K; Phase II $1M–$1.6M.
- **Deadline:** No confirmed open window; the compressed 2026 cycle keeps shifting. **Verify the next FOA + LOI date on SAM.gov before end of September.**
- **Link:** https://www.energy.gov/small-business · https://www.sam.gov
- **Honest fit:** **Weakest of the big three SBIR agencies for you** — DOE funds clean energy, grid, nuclear, and lab instrumentation more than QML/PQC. Keep on the watch list, don't spend effort now.

---

## Rolling — no fixed deadline (the set you can act on today, no entity needed)

> Tagged against FOCUS. **ON-FOCUS** = bears directly on `agpack` (agent-verification harness), `OpenWorker` (unattended agent fleet / MCP), `dcode-stack` (LLM serving/inference/compute), or the funding-pipeline open question; **ADJACENT** = standalone, capped at three, last.

### ON-FOCUS

- **[FELLOWSHIP] Leo Gao's Experimental Microgranting** (~$10K, minimal application, open to anyone) — the *cleanest frictionless apply* on this list. Framing that ties to your work: an **agent-reliability / evaluation study of your SLM-agent ETL pipeline** (dcode-stack/OpenWorker domain). → https://aisafety.com/funding
- **[RESEARCH] Corrigibility Research Fund** (**state changed — re-opened, rolling, closes Oct 31**) — grants + prizes ($5K–$35K range seen last round), **no application form** (email grants@corrigibilityresearch.org). Framing: **keeping agent systems under human control / informed** — a direct fit for `agpack`'s verifiable-agents / conformance objective. Retrospective 2026 work is eligible. → https://corrigibilityresearch.org/
- **[FELLOWSHIP] Institute for Progress (IFP) — "The Launch Sequence"** (**NEW this run**) — **$10K honorarium + funder matchmaking** for a short **200–400 word pitch** on a project "preparing the world for advanced AI" (alignment, control, **evals**, AI security). **Individuals** may apply; rolling. The conformance/spec-check work in `agpack` is eval-adjacent and fits the "evals science" lane. → https://ifp.org/the-launch-sequence/
- **[FELLOWSHIP] AIAF (AI Alignment Foundation)** — grants + fiscal sponsorship for independent researchers with ideas their current funding can't cover. You're in their exact target demographic (no institution, no company); field content is mech-interpretability, so a pitch about applying your **pipeline-building / verification skills to interp tooling** is your angle. Rolling. → https://aisafety.com/funding
- **[RESEARCH] fal.ai Research Grants** — free GPU/compute credits for open-source AI projects; **no entity required**. Directly relevant to `dcode-stack`/benchmarking: run and compare serving pipelines. Your public HetQML demo + repo make this a reasonable low-effort apply. → https://fal.ai/grants
- **[RESEARCH] AIComputeFund** — compute grants "open to researchers, SMEs, workers, nonprofits, students worldwide"; **no entity required** per their site. Plausible fit for dcode-stack compute budget, but the page is thin — confirm current cycle terms before applying. → https://www.aicomputefund.com/
- **[RESEARCH] Coefficient Giving — "Navigating Transformative AI" (career-development + project fund)** — funds career transition/exploration and project work toward TAI-relevant goals; **individuals at any career stage, no institution or company required, no PhD**. Fit is borderline (it leans field-building/policy), but the **career-transition lane** is genuinely your situation (ML engineer funding your own work). The sibling "Global Health" RFP **closed Aug 21, 2026**; the "Navigating" fund runs rolling grant-by-grant. → https://coefficientgiving.org/funds/navigating-transformative-ai/
- **[RESEARCH] Lightcone Commons** — large-scale AI-safety philanthropy, quarterly rounds, **simple application, rolling**. Stretch framing-wise (it targets "reduce global catastrophic risk"), but the bare "ambitious, well-scoped project" round is low-cost to submit and you've been carrying it since week-33. → https://aisafety.com/funding

### ADJACENT (capped at 3)
- **[FELLOWSHIP] BlueDot Impact — Rapid Grants** (up to $10K) — low-effort longshot; same framing as #1. → https://aisafety.com/funding
- **[FELLOWSHIP] Iliad RFP** — theory-driven **ReLU-network** research / high-epistemic-bar AI-safety science; "mathematicians especially welcome." Your QML work is empirical, not formal-math, so **fit is weak**, but it's a rolling independent-researcher fund. → https://www.iliad.ac/opportunities-2/2026-request-for-proposals
- **[RESEARCH] IBM Quantum Credits** — quantum hardware/software credits; **your project already targets IBM backends (Brisbane/Torino)**, so it's your strongest quantum fit and **requires no entity**. Nuance to flag: the aggregator (GrantedAI) describes recent recipients as PIs at **universities/national labs**, and IBM's own language is "novel, utility-scale research proposals" — so the honest read is **worth trying, but the institutional bias is newer than week-34's "apply first" call**. Re-confirm on IBM's page before you invest. → https://www.ibm.com/quantum/blog/quantum-credits

### Also open with an entity (bigger checks, same gate)
- **[STARTUP] Transformative AI Fund (TAIF / EA Funds)** — $10K–$150K, accepts **individuals**, needs the "transformative AI" framing. Rolling. → https://aisafety.com/funding
- **[STARTUP] Halcyon Futures** — up to $1M for a *new technical AI-safety research org* (nonprofit or for-profit). Biggest check, biggest commitment. Rolling. → https://aisafety.com/funding

---

## Explicitly dropped / closed this run (so they aren't re-reported)
- **DARPA FALCON (DPA26BZ04-DV016)** — closed Aug 19, 2026 (still closed).
- **Lightcone Commons round 1 / Corrigibility (old round)** — the *old* 2026 round closed Aug 23; **the fund has since re-opened** — see the state-change note, not dropped.
- **Foresight Institute "AI for Science & Safety Nodes"** — accepting: **No** (per AISafety tracker).
- **Schmidt Sciences "Multi-Agent Safety"** — **No** applications; ~$1M joint with DeepMind/ARIA/CAIF, institutionally heavy.
- **Cooperative AI Foundation "AI Safety Grants"** — **No**; targets universities/research institutions.
- **AIForge (DARPA + NSF)** — **No**; US **university-led** research.
- **Open Philanthropy "AI Safety Research"** — **No** current open call (advisory-only right now).
- **Coefficient Giving "Global Health & Wellbeing…Transformative AI" RFP** — **closed Aug 21, 2026**.
- **AI2050 / Schmidt Sciences** — **No** open programmatic call.
- **DOE Office of Science quantum (Genesis MIQ / HEP QuantISED)** — no open 2026 window; funds flow through institutional PIs.
- **Florida standalone cash grants** (Enterprise Florida, FL High Tech Corridor) — still only tax incentives (QTI/HIPI, pay-after-growth) plus the **monthly free SBIR/STTR clinic (3rd Thursday)** — a genuinely useful free resource if you go the SBIR route.

---

## Bottom line
1. **The single gate is still incorporation.** If you file a US company + SAM.gov: the real money opens — **DARPA (Sep 23), NSF (pitch by ~early Sept → full proposal Nov 4), NIH (Jan 5, 2027, with a partner), DoD Open Topics (rolling)**, all now subject to mandatory 2026 foreign-risk screening. If you *won't*, the realistic set is the rolling, no-entity grants below.
2. **Do this week, no entity needed (four applies, ~2 hours):** Leo Gao Microgrant ($10k, minimal), Corrigibility Fund (**re-opened, no form — email it, closes Oct 31**), Institute for Progress Launch Sequence ($10k, 400-word pitch), fal.ai compute credits. These tie most directly to your `agpack`/`dcode-stack`/agent work.
3. **Deadlines to calendar: Sep 23 DARPA DV019** (only if you incorporate by ~mid-Sep), **early-Sept NSF Project Pitch** (to hit the Nov 4 full-proposal gate), **Jan 5, 2027 NIH**, and re-check **NIH seed portal before Jan** (it's currently empty).
4. **Carry-forward reminder:** the week-33 digest's Anthropic-Fellows recommendation stands — it's a **salaried, organization-tied research placement, so it belongs to the jobs track, not this digest**. Treat its Jan-2027 cohort as the jobs-pipeline priority, not here.

*Verified directly this run: DARPA topics page (Release 5 / DV019 + Sep 23 date), seedfund.nsf.gov (Nov 4, 2026 full-proposal gate + solicitations), corrigibilityresearch.org (re-open, rolling, no-form), aisafety.com/funding tracker (state of Lightcone/Corrigibility/IFP/Iliad/IBM/etc.), seed.nih.gov (currently empty), grantedai.com 2026 SBIR calendar (aggregator — used for agency schedule dates, not as primary). Coefficient Giving pages checked via search snippets. Rolled-forward amounts (TAIF, AIAF, IBM, DoD/NIH award ceilings) should be re-confirmed on the funders' own application pages before submitting, per week-33's own caution.*
