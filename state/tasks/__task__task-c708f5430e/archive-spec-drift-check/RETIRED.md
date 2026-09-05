# Spec drift check - retired 2026-09-02

Folded into the weekly CI sweep (task-f471043f3e), which now carries a SPEC
CONFORMANCE section. The mechanical half - locating specs and implementations,
counting requirements, spotting an empty work/, choosing which slices are due -
runs in ~/.local/bin/ci-sweep-stage and lands as spec-manifest.md.

## Why it was retired rather than kept

It succeeded once in four runs: 31.5 minutes when it worked, and three failures
otherwise. Three of the four died of budget exhaustion before comparing anything,
because the agent spent its calls FINDING files. Its prompt also pointed
filesystem-directory_tree at ~/dcode-stack/slices, a path the filesystem MCP
server cannot resolve (it sees this home as /data/home), and a scheduled run is
confined to its own workspace anyway.

## What it was RIGHT about, which is why the function survives

Its one successful run (2026-08-20) found that trial-counter's guard,
`isinstance(sharpe, (int, float))`, admits booleans - isinstance(True, int) is
True in Python - so record_outcome(id, True) silently stores a Sharpe of 1.0,
violating spec R9 ('a malformed argument MUST raise rather than be ignored or
coerced'). Re-verified live on 2026-09-02: still present, 13 days later, and the
274-test suite contains zero references to bool or True, so CI is green and blind
to it. It also found that refraction's guard at -273.15 does not satisfy the
spec's own stated rationale, because the denominator 273+t reaches zero at -273.0.

Both findings are seeded into spec-findings.json in the CI sweep's workspace, so
they are re-verified and AGED each run rather than rediscovered. That the first
sat unfixed for 13 days is why the ledger exists.

Task definition and full run history preserved alongside this file; the 08-20
audit remains in the parent directory.
