# Spec drift audit — dcode-stack slices
**Date:** 2026-08-20
**Scope:** all 8 slices under `~/dcode-stack/slices/`
**Method:** SPEC.md read in full (or requirement-relevant portions where noted) for every slice, then the implementation in `work/` read in full, then compared. Two live probes run (Node 22.22.1, Python 3.14.4): trial-counter DB-level delete guard and type-coercion behavior. Node on this machine does **not** compile TypeScript ("not compiled with TypeScript support"), so no `.ts` slice could be executed — TS findings below are verified by reading, not running.

---

## 1. DRIFT (spec and code disagree)

### 1.1 `example-logstat` — implementation does not exist
- **Spec requires:** `logstat.py` (stdlib CLI producing a JSON report: `total_valid`, `malformed`, `error_rate_pct`, `top3_ips`, `p95_ms`) and `test_logstat.py` (pytest, all passing), run against `data/server.log`.
- **What exists:** `work/` is **empty**; there is no `data/` directory and no `data/server.log` anywhere in the slice. No `logstat.py`, no `test_logstat.py`.
- **Verdict:** spec and reality disagree — nothing is implemented. *Caveat:* the slice's README describes itself as a **work/template slice** for the `bin/govern` pipeline, so the missing implementation may be intentional. If it is, that is a spec/role mismatch; if it is not, this is the largest gap in the stack. Either way it was not verified as conformant.

### 1.2 `trial-counter` — boolean silently coerced into a Sharpe value (R9: "malformed input is fatal, never skipped")
- **Spec (Edge cases / R9):** `sharpe=float("nan")/inf → ValueError`, `sharpe="0.5" → TypeError`; "A malformed argument MUST raise rather than be ignored or coerced."
- **What the code does:** `record_outcome` gates with `isinstance(sharpe, (int, float))` and `math.isfinite(sharpe)`. In Python `isinstance(True, int)` is `True`, so `record_outcome(trial_id, True)` passes both gates and is stored as `sharpe = 1.0`. A boolean — not a Sharpe ratio — is silently coerced into a number and will flow into `sharpe_variance` and `deflation_inputs`.
- **Status:** verified by reading the code path (a live probe of the coerce case hit the earlier unknown-trial guard, so this is a code-reading finding, not an observed store). The two *specified* cases (`"0.5"` → TypeError, non-str `trial_id` → TypeError) **do** raise correctly (both verified live).
- **Fix is one line:** reject `bool` explicitly before the numeric check.

### 1.3 `trial-counter` — non-string `dataset_id` silently accepted in `trial_count` / `deflation_inputs` / `search_trial_count` (borderline)
- **Live probe:** `trial_count(123)` → returns `0` without raising, instead of failing or at least raising a type error. R9's letter names `params` and `trial_id` for `TypeError`, so this is *borderline* rather than a clear violation — but in spirit of "malformed must be fatal", a numeric dataset id is coerced into a SQLite comparison. `start_trial(123)` would raise `AttributeError` (not `ValueError`) via `.strip()`. Flagging for awareness, not as a hard violation.

### 1.4 `refraction` — spec leaves a hole its own rationale warns about (spec-internal, code follows spec)
- R9 says throw when `temperatureC` is "at or below absolute zero (−273.15 °C)" because that "would make R4's denominator zero or negative." The code implements exactly that boundary (`temperatureC <= -273.15` → `RangeError`). But the denominator `273 + temperatureC` is **zero at exactly −273.0 °C**, which is *above* −273.15, and the code then returns `Infinity` (or `NaN` if base R is 0) instead of throwing. Spec and code agree; both share the blind spot. Noted so the boundary someone else might "fix" is understood — the spec's stated rationale (never divide by ≤ 0) is not actually satisfied at −273.0.

---

## 2. UNKNOWN (could not verify)

- **`magnitude` — execution unverified.** `magnitude.ts` was read in full and matches the spec on every checkable point (R3 `betaRad === Math.PI` guard before any `F` check; R3 explicit input test per the sin(π) warning; R5 table values/keys exactly matching spec, `Record<string, number>`, `null` on miss; R6 `RangeError` guards on `stdMag`/`rangeKm`/`betaRad` via `phaseFunction`, 3-finite-element vector checks, coincident-satellite guard; R4 cosine clamp; R7 no rounding). **But** the Node 22 on this box cannot run `.ts`, so no numerical invariant was exercised and `magnitude.test.ts` was not audited. Classifying as "OK" for the read-comparison, **not** as execution-verified conformance.
- **All TS slices (`magnitude`, `refraction`, `shadow`, `collision-probability`):** mathematical invariants that can only be confirmed by running (magnitude R8 strict monotonicity; refraction R7 monotonicity across the domain; shadow R4 no sunlit→umbra jump; collision R3 Pc∈[0,1], R4 limits, R5 dilution turning point) are **not numerically verified**. Each was confirmed structurally present and correctly factored on paper only.
- **Test files** (`*.test.ts`, `test_*.py`) were read only to locate them and count lines, not audited line-by-line for coverage claims in R9/R12-class test requirements.
- **`collision-probability` spec lines ~120–end:** the spec file was read via head + MUST/RangeError greps rather than full tail (budget); R7 disclaimer-note and R8 validation requirements were confirmed present in the grep output and in the code.

---

## 3. OK (verified matching)

### `refraction` (`work/refraction.ts`, read in full)
- **R1/R2 Saemundsson, not Bennett:** formula is `1.02 / tan(h + 10.3/(h + 5.11))` with degrees→radians conversion before `Math.tan`. Correct direction per spec (true→apparent correction).
- **R3/R4:** defaults `1010`/`10`; scaling `(p/1010) * (283/(273+t))` exactly as spec.
- **R5:** `h < -1` returns exactly `0`; domain pole at −5.11 unreachable.
- **R6:** `Math.max(0, R)` clamp.
- **R8:** `apparent = h + refractionArcmin/60` — the `/60` is present.
- **R9:** `RangeError` on non-finite `trueAltitudeDeg` (covers `undefined` via `Number.isFinite`), `pressureMbar <= 0`/non-finite, `temperatureC <= -273.15`/non-finite. Never returns NaN for bad input.
- **R10/R11:** no rounding, pure, two named exports only.

### `shadow` (`work/shadow.ts`, read in full)
- **R1:** both cones use `EARTH_RADIUS/cos(alpha) ± alongAxis * tan(alpha)` — the `1/cos` tangent factor is present (the 68-m simplification trap the spec warns about is avoided).
- **R2:** all 8 steps in order; `zeta <= 0 → "sunlit"` inclusive; both boundary comparisons inclusive (`<=`); umbra checked before penumbra (R3 order).
- **R6 (the "silent sunlit" hazard):** every guard present and correct — 3-element arrays, finite elements, `sunDist === 0`, `|sat| < 6378.137`, and `sunDistanceKm <= SUN_RADIUS + EARTH_RADIUS` → `RangeError`. `typeof val !== "number"` catches `undefined`/string elements.
- **R7/R8:** exact three-state lowercase union, no rounding, pure, two exports.
- **Constants:** 6378.137 / 695700 exactly as spec.

### `trial-counter` (`work/trial_counter.py`, read in full + 2 live probes)
- **R7 append-only — LIVE VERIFIED:** `DELETE FROM trials` → `IntegrityError: Deletions are forbidden` (trigger, not API absence); `dataset_id` and recorded-`sharpe` UPDATE triggers present.
- **R2/R9 (live):** `start_trial("")` ValueError path present; `record_outcome(tid, "0.5")` → TypeError (live); `record_outcome(7, 0.5)` → TypeError (live); non-dict `params` → TypeError.
- **R3:** unknown `trial_id` raises; second `record_outcome` on same trial raises (row[0] not None check).
- **R5 (live-adjacent):** `n < 2 → None`; sample variance uses `n-1`.
- **R4/R6/R8:** per-dataset `COUNT(*)`, all four dict keys present, `search_trial_count` by `search_id`.
- **Ordering:** `ORDER BY start_time ASC, trial_id ASC` — deterministic tie-break, oldest first.
- (The two caveats in §1.2/§1.3 do not detract from the rows above.)

### `modified-var` (`work/modified_var.py`, read in full)
- **R1:** Cornish–Fisher expansion as published — `(1/6)(z²−1)S`, `(1/24)(z³−3z)K`, `−(1/36)(2z³−5z)S²` — terms, coefficients, kurtosis sign all match; source cited; excess-kurtosis convention **stated** in docstring per the "Units and conventions" demand.
- **R2:** `VaR = −(mean + vol·w_p)` — higher mean lowers VaR; zero vol gives `−mean` (a gain = negative loss) exactly as spec; positive-for-loss convention met.
- **R3:** S=K=0 collapses to plain `inv_cdf(p)` exactly (`statistics.NormalDist()` per the anti-hand-roll warning).
- **Edge cases:** `TypeError` on non-numeric (all 5 params), `ValueError` on confidence ∉ (0,1), negative volatility, non-finite. (The bool-is-int quirk applies here too; not listed separately.)

### `purged-cv` (`work/purged_cv.py`, read in full)
- **R1:** contiguous disjoint folds via `array_split`, cover all indices; numpy arrays yielded (numpy 2.3.5 is actually available in this env — the "no numpy" note belongs to the *sibling* modified-var spec).
- **R2:** test interval = `[min(label_start[test]), max(label_end[test])]` — the `max(label_end)` detail the spec emphasizes is present; overlap check is the standard interval test.
- **R3:** embargo applied **after** the test window only (`label_start > test_window_end + embargo_size`), `embargo_pct` × **observation count** (not time span), default 0.01.
- **R4:** standalone `assert_no_leakage(train_idx, test_idx, label_start, label_end)`, raises/None — signature order matches spec, usable on any splitter.
- **R5:** no flag or alternate class bypasses purging.
- **Edge cases:** all six specified `ValueError` conditions implemented (unsorted, end<start, length mismatch, n_splits<2/non-int, embargo range, empty-train-set → ValueError).

### `collision-probability` (`work/collision-probability.ts`, read in full)
- **R1:** polar quadrature over the disk with the `r` Jacobian (`integrand = prefactor*exp(quad)*r`), 200×200 steps (≥200 as spec requires), prefactor `1/(2πσxσz)`, correct `dr·dθ` completion; principal-axes/no-rho convention documented.
- **R3:** result clamped to `[0,1]`.
- **R4:** `radiusKm === 0 → 0` explicit path; monotonicity properties hold structurally (see §2 for the not-executed caveat).
- **R6:** `assumedSigmaKm = 1.0 + 0.5·tleAgeHours` with finite/non-neg guards throwing.
- **R7:** `PC_ASSUMPTION_NOTE` exported as a string (assumption travels with the result, as the spec's whole framing demands).
- **R8 (validation):** all five inputs must be finite → `RangeError`; `σx, σz <= 0` → `RangeError`; negative radius → `RangeError`. Malformed input is fatal, never coerced.

---

## Coverage statement
**8/8 slices covered** at the spec-vs-code reading level. Live execution was possible only for the two Python paths probed (trial-counter). No TS file could be executed on this host — see §2. Slices I did *not* reach: none — though budget meant test files and 3 spec tails were not read line-by-line (§2), which is the honest boundary of this audit.
