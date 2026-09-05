# Spec conformance - slice manifest

Generated 2026-09-02T18:35:24Z by ci-sweep-stage.

Specs and implementations are already located for you. Do NOT spend tool calls
searching for them - three of the predecessor job's four runs died of budget
exhaustion before comparing anything.

| slice | spec lines | requirements | normative words | implementation | impl lines | last audited | days ago |
|---|---|---|---|---|---|---|---|
| collision-probability | 212 | 10 (R1-R10) | 12 | collision-probability.ts | 153 | never | - |
| example-logstat | 60 | 0 | 3 | **none** | 0 | never | - |
| magnitude | 245 | 9 (R1-R9) | 7 | magnitude.ts | 100 | never | - |
| modified-var | 126 | 4 (R1-R4) | 10 | modified_var.py, probe_test.py | 156 | never | - |
| polars-mix | 146 | 5 (R1-R5) | 5 | _harness.py, mix.py | 824 | never | - |
| polars-run-eval | 196 | 9 (R1-R9) | 9 | _verify.py, idiom_rules.py, run_eval.py | 1521 | never | - |
| purged-cv | 137 | 5 (R1-R5) | 10 | probe_r2.py, probe_r2_r3.py, purged_cv.py | 257 | never | - |
| refraction | 184 | 12 (R1-R12) | 10 | refraction.ts | 44 | never | - |
| shadow | 218 | 9 (R1-R9) | 8 | audit.js, probe.js, probe2.js, probe3.js, shadow.ts | 308 | never | - |
| trial-counter | 168 | 10 (R1-R10) | 21 | audit_probe.py, trial_counter.py | 287 | never | - |

## THIS RUN'S TARGETS

The three least-recently audited, never-audited first:

  - `/home/iconbaypark2900/dcode-stack/slices/collision-probability/SPEC.md`  vs  `/home/iconbaypark2900/dcode-stack/slices/collision-probability/work/`
  - `/home/iconbaypark2900/dcode-stack/slices/example-logstat/SPEC.md`  vs  `/home/iconbaypark2900/dcode-stack/slices/example-logstat/work/`
  - `/home/iconbaypark2900/dcode-stack/slices/magnitude/SPEC.md`  vs  `/home/iconbaypark2900/dcode-stack/slices/magnitude/work/`

Audit THESE THREE properly rather than all ten shallowly. Partial coverage
stated honestly beats full coverage claimed. When you have audited a slice,
record it in spec-audit-state.json as {"<slice>": "YYYY-MM-DD"} so the
rotation advances; a slice you did not actually read must NOT be recorded.

## Detected mechanically - no reading required, already established

- **example-logstat** - SPEC.md declares 0 numbered requirements and `work/` contains no implementation file. Nothing to compare; report as DRIFT or UNKNOWN, never as OK.

## Open findings carried forward

Re-verify each. An open finding that is still present gets LOUDER with
age, not quieter -- report it with its age, never as a fresh discovery.

- **trial-counter** - record_outcome() guards with isinstance(sharpe, (int, float)), and isinstance(True, int) is True in Python, so a bool passes both that guard and math.isfinite() and is stored as a Sharpe of 1.0. Verified live on 2026-09-02: isinstance(True, (int,float))=True, math.isfinite(True)=True, float(True)=1.0. The 274-test suite contains zero references to bool or True, so CI is green and cannot see this. Fix is one line: reject bool explicitly before the numeric check.
  first flagged 2026-08-20 (13 days ago); spec ref: R9 - 'A malformed argument MUST raise rather than be ignored or coerced.'; failing test written: False
- **example-logstat** - work/ is empty: no logstat.py, no test_logstat.py, no data/ directory. Nothing is implemented. Caveat recorded by the original audit and still unresolved: the slice's README describes it as a work/template slice for the bin/govern pipeline, so the absence may be intentional. If it is, the SPEC and the slice's role disagree and one of them should change. Either way it has never been verified as conformant.
  first flagged 2026-08-20 (13 days ago); spec ref: SPEC.md requires logstat.py and test_logstat.py run against data/server.log; failing test written: False
