# Observability golden test

`golden-phase1/` is a copy of `docs/observability/golden/phase1/` in the `ocw-context`
repository: the shared test of recording format 1.0, frozen 2026-10-31. The Rust harness
SDK runs the same files, so both engines fold the same recording events into the same
recording.

Do not edit these files here. A change to the format needs a new version in `ocw-context`
(its `tracing-spec.md` §12), and then a fresh copy of the folder here.

`tests/test_observability_golden.py` replays every scenario's `events.jsonl` into the
recorder and compares the result with `expected-recording.json`. `expected-numbers.json`
(the screen numbers) is checked once the read endpoints are built.
