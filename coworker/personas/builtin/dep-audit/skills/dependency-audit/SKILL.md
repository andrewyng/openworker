---
name: dependency-audit
description: Scan lockfiles for vulnerable dependencies and triage by real reachability
---
Audit the project's dependencies and separate what's exploitable from what's noise.

1. Identify the ecosystems present (package-lock.json / pnpm-lock.yaml / yarn.lock,
   requirements*.txt / uv.lock / poetry.lock, go.sum, Cargo.lock, pyproject).
2. Pick scanners that are present (check first; ask before installing):
   - `osv-scanner --lockfile <each lockfile> --format json` (best cross-ecosystem)
   - `npm audit --json` / `pip-audit -f json` / `trivy fs --scanners vuln . -f json`
3. Deduplicate advisories across scanners (key on advisory id + package), then triage
   each one by reading the code:
   - Direct or transitive? (`npm ls <pkg>`, `pipdeptree -r -p <pkg>` or grep imports)
   - Is the vulnerable functionality actually used here? Grep for the affected API;
     an unreachable advisory in a dev-only tool is LOW no matter its CVSS.
   - Verdict per advisory: fix-now / fix-soon / accept-with-note, one line of why.
4. Map each fix-now to its smallest closing upgrade (advisory metadata's fixed-in
   version); note when only a major closes it and what the migration entails.
5. Deliver: an audit table (advisory · package · direct? · reachable? · verdict ·
   smallest fix) ordered by real priority — then hand off to `safe-upgrade-pr` for the
   actual upgrades. Offer a CI guard (e.g. an osv-scanner step) so new advisories
   surface on PRs instead of in the next audit.
