# ClearCode Intelligence evidence package

This directory contains an independent, evidence-backed static repository
assessment of OpenWorker before and after the accompanying hardening work. It
is contributed for maintainer review; it is not a statement by, certification
of, or endorsement from the OpenWorker maintainers.

## Measured change

Both scans used the same ClearCode source digest and settings against clean,
exact commits.

| Evidence signal | Baseline | Remediated | Change |
| --- | ---: | ---: | ---: |
| Overall score | 75 | 92 | **+17** |
| Maintainability | 50 | 71 | +21 |
| Security | 63 | 100 | +37 |
| Architecture | 100 | 100 | 0 |
| Open Source | 91 | 100 | +9 |
| Delivery | 87 | 93 | +6 |
| AI Governance | 69 | 100 | +31 |
| Findings | 41 | 12 | **-29** |
| High-severity findings | 11 | 6 | -5 |
| Mutable GitHub Action references | 15 | 0 | -15 |
| Workflows with implicit token permissions | 2 | 0 | -2 |
| Known dependency vulnerabilities | 1 | 0 | -1 |
| Repository governance gaps | 6 | 0 | -6 |
| Report confidence | 84/100 | 100/100 | +16 |

The provenance-locked comparison reports 29 resolved findings, zero introduced
findings, and identical generator/settings digests.

## Exact targets

- Repository: `https://github.com/andrewyng/openworker`
- Baseline commit: `db93d75bf634e3a855b29e00d8f5d677438cac1f`
- Remediated commit: `bbb806ce5453335d64ef163f9d5586544398c7e8`
- ClearCode generator source digest:
  `e326855af2006bc8db60f21a75e17ac585521637adb016dd22b66cb63b82b1d4`
- Scan settings digest:
  `3c833fd477ec8ab293692b8b152de71df0b15b30fcb1f7e65a9292ba6fec51fd`
- Scan coverage: 89.7% baseline; 90.2% remediated
- Working trees at scan time: clean

The reports are committed in a later documentation-only commit. This prevents
the generated evidence package from changing the code commit it assesses.
`docs/clearcode/**` is explicitly excluded from the scanner evidence base.

## What the remediation covers

- Pinned GitHub Actions to immutable commits and declared least-privilege
  workflow permissions.
- Removed the Tauri script-evaluation boundary and replaced it with typed event
  handling.
- Split oversized Python, React/TypeScript, and Rust hotspots into narrower,
  testable modules.
- Hardened service lifecycle, tool execution, email/integration boundaries,
  frontend API boundaries, and safe HTML rendering.
- Added repository ownership/governance evidence and reproducible dependency
  audit coverage.
- Preserved behavior with focused tests while completing production frontend
  and Rust compile checks.

## Executed validation

- Python: `uv run --group dev pytest -q` — 891 passed, 1 skipped.
- Frontend: `npm test -- --run` — 69 tests passed.
- Frontend: `npm run build` — production build passed.
- Browser E2E: `npm run e2e` — 154 Chromium scenarios passed.
- Rust/Tauri: `cargo fmt --check` and `cargo check` — both passed.
- Dependencies: npm audit and pip-audit completed for both applicable
  ecosystems with zero known vulnerabilities reported at the remediated
  commit.
- ClearCode: 117 tests passed; production dependency audit passed.

The desktop application was not packaged or runtime-tested on macOS, and no
production deployment or live traffic was evaluated.

## Package contents

- [Baseline PDF — score 75](./OpenWorker-ClearCode-Baseline-75.pdf)
- [Post-remediation PDF — score 92](./OpenWorker-ClearCode-Post-92.pdf)
- [Before/after PDF — 75 to 92](./OpenWorker-ClearCode-Delta-75-to-92.pdf)
- [Baseline scan JSON](./baseline-scan.json)
- [Post-remediation scan JSON](./post-remediation-scan.json)
- [Machine-readable delta](./before-after-delta.json)
- [Post-remediation SARIF](./post-remediation-findings.sarif.json)
- [Artifact checksums and QA record](./artifact-index.json)

The public JSON artifacts omit workstation-only paths. The three PDFs were
rendered and inspected page by page; they contain no blank/short pages and only
repository-assessment content.

## Claim boundary

ClearCode scores are prioritization-model outputs, not certifications or
measurements of exploitability. A resolved finding means the scanner's rule
evidence is absent at the remediated commit. It does not by itself prove runtime
security, incident prevention, productivity, or financial value. Those
outcomes require production telemetry and organization-specific operating
data.
