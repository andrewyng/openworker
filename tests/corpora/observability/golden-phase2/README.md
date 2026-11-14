# Golden test, Phase 2

*Written 2026-11-02 with [recording-format.md](../../recording-format.md) 1.2 and [signals.yaml](../../signals.yaml) 0.3; rebuilt 2026-11-03 for signals 0.4 (S2-17d added) and 0.5, and 2026-11-04 for 0.6 and 0.7 (0.7 changed only the version here): only the version and two new turn checks (`wake_capped`, `watchdog_nudged`) under `not_checked` changed, because a 1.2 recording has no `wake`. The recordings are unchanged and valid under 1.3. Made by `phase-2/lab/make_golden2.py`, checked by `phase-2/lab/check_golden2.py`.*

## What it is

**Context.** Both engines must find the same issues in the same turn, and must turn the same arguments into the same hash. This folder pins both. Feed each `recording.json` to an engine's Issues checks, and it must give exactly `expected-issues.json`. Feed each vector to its hashing or masking code, and it must give exactly the expected bytes.

| File | What it is |
|---|---|
| `<id>/recording.json` | One turn as a format 1.2 recording, `{"turns": [...], "steps": [...]}`. It is valid against [recording-format.schema.json](../../recording-format.schema.json) |
| `<id>/expected-issues.json` | What the checks must give: `fired` (check, and the `seq` of each step it points at), `fine`, `not_checked`, the warning count, and the `signals.yaml` version |
| `index.json` | The scenario list: which check each one tests, and whether it should fire, stay quiet or not run |
| `vectors/hashes.json` | `args_hash` cases, each with its value, its RFC 8785 canonical JSON and its 6-hex hash; plan item hashes |
| `vectors/masking.json` | Rule R4 cases: secrets masked (patterns and stored values), and the 8 KB cut on a character boundary |

There are **39 scenarios**, S2-01 to S2-19 with their clean twins. Each is described in words in [phase-2/scenarios.md](../../phase-2/scenarios.md).

## Built, not captured

Unlike `golden/phase1/`, these recordings were **built** from the scenarios by hand rules (`phase-2/lab/synthetic.py`), not captured from a scripted run of the engine. The engine does not record the 1.2 fields yet. Once it does (engine change E8a), each scenario is run through the engine in scripted mode and its recording replaces the built one. Its expected issues must not change. If they do, either the engine or the scenario is wrong, and the difference is a finding.

## The byte-exact rules

- **`args_hash`** is the first 6 hex characters of SHA-256 over the arguments' canonical JSON, encoded as UTF-8. Canonical JSON is **RFC 8785 (JCS)**: object keys sorted by their UTF-16 code units, no whitespace, strings with only the required escapes, numbers in the ECMAScript form. Neither Python's `json.dumps` nor Rust's `serde_json` is the rule: they differ on numbers (`1e-07` against `1e-7`, `100.0` against `100`). The `numbers` vector catches that.
- **A plan item's `h`** is the first 6 hex characters of SHA-256 over the item's own text, encoded as UTF-8. It is not JSON.
- **Masking and cutting** (export rule R4) work in this order:
  1. Mask stored secret values of 8 characters or more, longest first.
  2. Mask the patterns in `attributes.yaml` (`export.content_switches.masking`).
  3. Cut to 8,192 bytes of UTF-8 without splitting a character, then add `[cut: N more bytes]`.
