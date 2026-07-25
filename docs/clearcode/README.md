# ClearCode Intelligence evidence package

This directory contains the reproducible static-analysis package for the
OpenWorker hardening change. The primary deliverable is the
[21-page PDF report](./andrewyng-openworker-intelligence-report.pdf).

## Measured change

| Evidence signal | Baseline | Remediated | Change |
| --- | ---: | ---: | ---: |
| Overall score | 69 | 77 | +8 |
| Security | 63 | 85 | +22 |
| Open Source | 91 | 100 | +9 |
| AI Governance | 69 | 95 | +26 |
| Findings | 45 | 37 | -8 |
| Mutable GitHub Action references | 15 | 0 | -15 |
| Workflows with implicit token permissions | 2 | 0 | -2 |
| High-severity dependency audit findings | 1 | 0 | -1 |
| Repository governance gaps | 6 | 1 | -5 |
| Report confidence | 84/100 | 100/100 | +16 |

The comparison found eight resolved findings and no introduced findings. Both
applicable package-manager audits completed successfully in the remediated
scan.

## Exact targets and reproducibility

- Repository: `https://github.com/andrewyng/openworker`
- Baseline commit: `db93d75bf634e3a855b29e00d8f5d677438cac1f`
- Remediated commit: `602415a52adfc1adb762b85cc55bde3a2df24242`
- ClearCode generator source digest:
  `fad562fefa9f8da41755117e7a4c66a5d530d45e3fa8e7e41f1fc8fd6760baac`
- Scan settings digest:
  `67beca315c40aeb9b0fa758488e5769a55d560896f600cdb88365be9e6b3c129`
- File discovery: Git-tracked plus non-ignored untracked files
- Working trees at scan time: clean
- Comparison status: provenance-locked and comparable

The report artifacts are added in a later documentation-only packaging commit,
so the report can identify the exact remediation commit it assesses without a
self-referential commit hash.

## Verification performed

- Python: 891 passed, 1 skipped in a fresh environment installed from
  `uv.lock` with `uv sync --frozen --extra messaging --extra dev`.
- Python dependency audit: frozen production export from `uv.lock`, then
  `pip-audit`; no known vulnerabilities were reported.
- GUI unit tests: 69 passed across 13 Vitest files.
- Chromium end-to-end tests: 154 passed across two Playwright shards.
- GUI production build: passed with Vite 6.4.3.
- npm audits: full development tree and production tree both reported zero
  known vulnerabilities.
- Rust tests were not run because a Rust toolchain was not installed in the
  validation environment. The change does not modify Rust source.

## Package contents

- `andrewyng-openworker-intelligence-report.pdf` — decision-ready report.
- `andrewyng-openworker-intelligence-report.html` — accessible report source.
- `andrewyng-openworker-before-after-delta.json` — machine-readable,
  exact-commit comparison with claim boundaries.
- `andrewyng-openworker-scan.json` — complete remediated scan payload.
- `andrewyng-openworker-findings.sarif.json` — SARIF 2.1.0 findings.
- `andrewyng-openworker-quality.json` — report quality-gate result.
- `andrewyng-openworker-manifest.json` — portable relative paths and SHA-256
  hashes for every artifact.

## Claim boundary

ClearCode scores are prioritization-model outputs, not certifications or
measurements of exploitability. A resolved finding means the scanner's rule
evidence is absent at the remediated commit. It does not by itself prove runtime
security, incident prevention, productivity, or financial value. Those outcomes
require production telemetry and organization-specific operating data.
