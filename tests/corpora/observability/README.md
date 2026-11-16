# Observability golden test

`golden-phase1/` is a copy of `docs/observability/golden/phase1/` in the `ocw-context`
repository: the shared test of recording format 1.0, frozen 2026-10-31. The Rust harness
SDK runs the same files, so both engines fold the same recording events into the same
recording.

Do not edit these files here. A change to the format needs a new version in `ocw-context`
(its `tracing-spec.md` §12), and then a fresh copy of the folder here.

`tests/test_observability_golden.py` replays every scenario's `events.jsonl` into the
recorder and compares the result with `expected-recording.json`, and computes the screen
numbers (`coworker/observability/calculator.py`) and compares them with
`expected-numbers.json`.

`golden-phase2/` is a copy of `docs/observability/golden/phase2/` in `ocw-context`: the shared
test of the Issues checks (OB-3), with `signals.yaml` 0.3 and recording format 1.2. Each
`recording.json` must give exactly its `expected-issues.json` through
`coworker/observability/signals.py`, and `vectors/hashes.json` pins the argument hash (RFC 8785).
`tests/test_observability_issues.py` runs it. `coworker/observability/signals.yaml` is a copy of
the shared `docs/observability/signals.yaml`, and `recording-format.schema.json` here is the 1.2
schema; the same rule applies to all three: change them in `ocw-context`, then copy them here.
