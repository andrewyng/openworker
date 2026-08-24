---
name: safe-upgrade-pr
description: Ship minimal, test-verified dependency upgrades as focused PRs
---
Turn triaged advisories into upgrade PRs a reviewer can merge without fear.

1. One branch per ecosystem (`security/deps-npm`, `security/deps-python`), smallest
   viable bumps: the fixed-in patch/minor, not "latest". Majors get their own branch
   and a migration note.
2. Regenerate lockfiles with the repo's OWN toolchain (`npm install pkg@ver`,
   `uv lock`, `poetry update pkg` …) — never hand-edit a lockfile.
3. Verify before proposing: clean install, build, and the project's test suite. Red
   suite → investigate; if the bump itself breaks the build, document what's entangled
   and propose the next-smallest path instead of forcing it.
4. PR body per upgrade: advisory id(s) closed, package old→new version, reachability
   verdict from the audit (one line), and the verification commands run. Skip CVE
   boilerplate walls — link the advisory instead.
5. Leave `accept-with-note` advisories OUT of the PR; record them in the PR body's
   "consciously not fixed" list with their justification, so the decision is visible
   and revisitable.
6. Never merge your own upgrade PR — deliver it with what a reviewer should check
   (typically: lockfile diff sanity and the test run).
