# Outside View — argue against the current approach
**Date:** 2026-08-26 · **Run:** mid-week, time to change course still available

## Target (one sentence)
I am arguing against the core move of the OpenScienceLab/openEvolve drug front — **letting an LLM freely mutate SMILES strings as the evolutionary operator, with the search steered by a tiered scoring ladder whose workhorse fitness signal is the AutoDock Vina docking score** — as the way to find HCV NS3/4A protease candidates (Phase 3 closed 08-25 at commit 3ef8030; Phase 4 scope per the closure record).

*Correction to FOCUS.md, one line:* FOCUS.md (written Monday) still lists Phase 3 item 1 as the open question; per the durable record Phase 3 **closed** 2026-08-25 with sign-off, and the approach under fire is what Phase 4 runs on.

---

## 1. ALTERNATIVE APPROACHES (not the one in use)

**Alternative A — Chemically-grounded operators + a cheap ML surrogate, no LLM in the loop.**
Classical graph-based evolutionary operators (the Graph-GA lineage) do the mutation, and fitness comes from fast property predictors (QED/SA/logP-class models) rather than per-candidate docking. What it buys: zero token cost per generation, 100% validity by construction (operators act on graphs, not strings), and the scoring tier can be reserved for a shortlist. What it costs: no "chemical intuition" in the proposals — the 2026 literature is genuinely split on whether the LLM operator is worth it: ToolMol (arXiv:2605.12784, abstract read) reports its agentic-operator framework beats prior methods by >10% predicted affinity and >35% on ABFE, *and* reports that in their pipeline LLM direct-mutation variants did not improve over baselines once invalids were filtered — i.e., the win came from **tool-structured editing, not free LLM text edits**.

**Alternative B — Cost-allocated Bayesian optimization instead of an evolutionary budget ledger.**
Cost-aware BO (CArBO, Lee et al., arXiv:2003.10870) solves exactly "expensive black-box, heterogeneous costs, fixed budget": cost-effective initial design + cost-cooling that schedules cheap evaluations before expensive ones, and outperforms both vanilla EI and EI-per-unit-cost "on a set of 20 black-box problems" (abstract read). Multi-fidelity BO (the 2026 TS-MFBO paper line, ScienceDirect S1568494626009385, snippet only — I did not read the full page) does the same with low/high-fidelity surrogates. What it buys: a *provably-motivated* rule for which candidates get the expensive tier (DFT/CHGNet) at budget B, instead of plateau-patience heuristics. What it costs: a surrogate model over the candidate space you don't currently have, and it assumes you can sample quasi-continuously over a numerical domain — awkward for SMILES directly; it fits the descriptor/property space better than structure space.

**The honest synthesis:** the current architecture (budget ledger + tier ceilings + evolve()) is a *billing* system for an expensive oracle, but the *allocation* decision — which candidates promote to which tier — is exactly the decision CArBO/PBGI formalize. The ledger tells you what it cost; it never tells you what was worth it.

---

## 2. PRIOR ART (who already built this?)

- **ToolMol** (Stanford/UCSD, arXiv:2605.12784, v2, 2026-05; abstract + HTML read) is the closest published system to what I am building: GA + LLM operator + RDKit-backed scoring, multi-objective. Their key design decision — *the LLM never edits the SMILES string directly; it calls 7 deterministic RDKit tools with structural parameters* — is a direct answer to the exact failure mode in my Phase 2 record where "the default _child mutates SMILES/sequence so every material descendant was a clone" (PHASE2.md, per recorded history). They also ablate: fixing MOLLEO's invalid-generation rate to ~0% *degraded* performance, isolating the edit mechanism itself, not validity filtering. **If I adopt nothing else, adopt the tool-edits-not-strings pattern.**
- **MOLLEO** (arXiv:2406.16976; project page + alphaXiv abstract read by snippet) — LLMs as crossover/mutation operators in EAs over chemical space, claiming wins over Graph-GA on PMO tasks and structure-based docking (DRD3, EGFR). It is the published proof the *general* idea works; it is not the same system, and its best variant used GPT-4, with open-source (BioT5) degrading on multi-objective tasks — a warning for running this on a local 27B.
- **AlphaEvolve itself** (arXiv:2506.13131 read by snippet; DeepMind impact blog read by snippet) explicitly scopes to problems whose candidates "can be automatically evaluated" and stresses *fast, automatic evaluators* (FunSearch: ≤20 min on 1 CPU). My tier-2/3 evaluators (PySCF DFT, CHGNet) sit on the wrong side of that cost envelope for the number of generations an island model runs — the architecture tolerates slow evaluators only via the budget ledger, which (per §1) never optimizes allocation.
- **CArBO** (arXiv:2003.10870, abstract read) — the cost allocation problem I am solving ad hoc has a 6-year-old published solution.

**Verdict:** nothing here says "don't build the loop." They say the loop exists, is published, and its one non-trivial design decision (LLM edit mechanism) has a known-better answer I am not using.

---

## 3. THE CASE AGAINST (explicit criticism)

1. **The fitness signal itself is weak where I need it.** A 2026 audit (clawRxiv:2604.01170, read in full — note: preprint on a non-standard archive, so I weight it as corroboration, not authority) benchmarks Vina, Glide SP, GOLD ChemScore, RF-Score on 5,316 PDBbind v2020 complexes: best single function **R² = 0.31** (RF-Score), consensus **0.38**; **GPCRs collapse to R² = 0.12**; flexible ligands (>500 Da) are *systematically over-scored* (+1.8 kcal/mol MAE); and in their subsidiary redocking run, correlation **dropped to R² = 0.14** — pose error compounds scoring inaccuracy by ~40%. Independent of that preprint, the Liganx 2026 scoring comparison (read in full) concedes r = 0.5–0.7 (R² 0.25–0.5) even for the best workhorses, that Vina over-scores charged/large molecules and ignores bridging waters, and cites PoseBusters (Buttenschoen et al. 2024, *Chem. Sci.* — "AI-based docking methods fail to generate physically valid poses") as the standard pre-report check. **Implication:** an evolutionary population is precisely the setting where a noisy ranker is most dangerous — selection pressure amplifies the scorer's biases (Vina's burial preference and flexible-ligand over-scoring) into the population. My KILL-1 stamp-honesty tests verify that a score is *labeled* honestly, not that the score *predicts* binding.
2. **Free-text LLM mutation is the documented weak link.** Practical Cheminformatics (2024, read in full): Claude-generated analogs are "string manipulation masquerading as chemistry" — terminal-token substitution, parenthesized-methyl swaps, progressive alkyl-chain extension ("methyl, ethyl, futile"), ~20% of the sample unparseable. EmergentMind's LLM-genetic-search topic page (read in full) notes LLM-generated mutations "tend to collapse diversity compared to random or traditional syntactic mutations" (Brownlee et al. 2023; Dat et al. 2024) — diversity collapse is a known GA death mode, and my Phase 2 history already exhibited it (all 36 compositions collapsed to one seed).
3. **The AlphaEvolve framing overstates transfer.** The AlphaEvolve white paper's own scope note: it "puts tasks that require manual experimentation out of our scope" because candidates must be auto-evaluated, and its FunSearch ancestor required ≤20 min/1 CPU evaluation to stay viable. A pipeline whose expensive tiers are DFT/CHGNet runs *per generation* is closer to hyperparameter tuning than to algorithm discovery — the wrong side of that cost line.

Hedged where the evidence is thin: items 1's preprint is unverified (single archive, no peer review, no replication I found); the PoseBusters/CASF line it draws is, however, consistent with the mainstream record I did find.

---

## 4. THE ADJACENT FIELD (one field from outside that met this shape)

**AutoML / hyperparameter tuning.** The problem shape is identical: black-box objective, evaluation cost varies by orders of magnitude across the search space, hard budget in *cost units* not eval units, and the naive move (divide the acquisition function by predicted cost — EIpu) is *provably arbitrarily suboptimal* — shown in the Pandora's-Box/Gittins-index cost-aware BO paper (NeurIPS 2024, read by abstract), which I independently corroborate in CArBO's own benchmarks (EIpu "likely to only display strong results when optima are relatively cheap," arXiv:2003.10870 read by abstract). What transfers, concretely:
- **Ordering rule:** cheap evaluations *first*, expensive *late and only where the cheap model points* (cost-cooling). My current ladder is tier-ordered *per candidate*, not *per run* — the population-level schedule is missing.
- **Allocation rule:** a cost-efficiency acquisition, not plateau-patience. The budget ledger already computes exactly the quantities (predicted_cost, actual_cost, delta_cost per Bundle) that CArBO needs — the input data exists; the decision function doesn't.
- **Initial-design rule:** CArBO spends τ/8 of budget on a *space-filling* cheap design before any exploitation. My Phase 2 seed collapse (36→1) is the anti-pattern this rule exists to prevent.

---

## REQUIRED CLOSE

### ONE THING TO CONSIDER ABANDONING
**The free-form LLM SMILES-string mutator (the `_child` operator), as the core evolutionary operator.** Worst effort-to-payoff in the whole system: it is the part (a) with the documented literature failure mode (ToolMol: fixing validity of LLM direct-mutation *degraded* results — the operator itself is the bottleneck; Practical Cheminformatics: string-trick analogs; EmergentMind: diversity collapse), (b) already demonstrated to fail in this codebase (Phase 2: descendants collapsed to clones), (c) most expensive per generation on the local 27B, and (d) the part least load-bearing if scoring is what actually discriminates candidates. **Replace it with** a ToolMol-style operator set: fixed RDKit-backed tools (substitute-at-position, ring-expand, branch-add, tautomer, salt-attach) invoked with explicit structural parameters, the LLM choosing tool+args, validity guaranteed by construction. Same interface, same prompt budget, and it turns the operator from a *generative* (unreliable) component into a *planning* (testable) one.

### ONE CHEAP EXPERIMENT (falsifiable, < 1 hour)
**Operator A/B for validity, duplication, and rank at equal budget.** Take 10 seed hits (any 10 real SMILES, e.g. the Phase 2 seed set), and produce 50 offspring each via (A) the current LLM-free-text `_child` operator and (B) a deterministic RDKit operator set (uniform-random tool+position substitution, no LLM). Score all 100 with the existing Vina adapter path (the Docker verify image already pins vina 1.2.5 — this runs in the existing test harness). Measure: (i) valid-SMILES rate, (ii) duplicate/near-duplicate rate (Tanimoto > 0.9 against seed or sibling), (iii) the *rank of the 5 best children* by score. **Current approach is falsified if** (B) matches or beats (A) on (iii) while A is worse on (i) or (ii): it proves the LLM mutation adds cost and noise but contributes nothing the deterministic operators didn't find — i.e., the most expensive per-generation component in the pipeline is not the component doing the exploration. (If A wins here, note the next suspect per §3 is the ranking signal itself: top-5 rank correlation of Vina vs. a second engine on the same pool.)

---

## ADJACENT (not in FOCUS.md, worth knowing, capped at 3)

1. **Schrödinger is using AlphaEvolve for ~4× MLFF training/inference speedups** (DeepMind impact blog, 2026-05, read by snippet) — the industry deployment of the "evolve the algorithm" pattern is about optimizing the *physics code*, not generating molecules. Directional signal: the scarce-value use of evolution here may be the scoring layer's internals, not the candidate loop.
2. **PoseBusters (Buttenschoen et al., Chem. Sci. 2024)** — AI docking methods produce poses violating basic chemical physics; cited as the pre-report standard by Liganx (read). Cheap to add as a gate before any candidate leaves the drug front; currently absent from the KILL-criteria set (which checks stamp-honesty and tier ceilings, not pose validity).
3. My own paper-watch flagged, this week, two quantum cost-floor results (arXiv:2608.24493/494, guided ground-state estimation) as "the settled cost floor for QM-tier scoring (Phase 3 items 3-4)" — consistent with §4: the expensive tier's cost envelope is now well-understood enough that spending allocation should be an *optimal policy*, not a ledger.

## Sources read (full or abstract)
https://clawrxiv.io/abs/2604.01170 · https://liganx.com/blog/vina-gnina-glide-scoring-function-comparison · http://practicalcheminformatics.blogspot.com/2024/10/silly-things-large-language-models-do.html · https://www.emergentmind.com/topics/llm-driven-genetic-search · https://arxiv.org/abs/2605.12784 · https://arxiv.org/abs/2003.10870 · (snippet-only: arXiv:2406.16976 / molleo.github.io, arXiv:2506.13131, deepmind.google/blog/alphaevolve-impact, ScienceDirect S1568494626009385, NeurIPS 2024 PBGI)
