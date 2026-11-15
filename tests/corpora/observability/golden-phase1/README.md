# Golden test, Phase 1

*Frozen 2026-10-31 with [recording-format.md](../../recording-format.md) version 1.0. Made by `phase-1/lab/make_golden.py`, checked by `phase-1/lab/check_golden.py`.*

## What the golden test is

**Context.** OpenWorker's observability is built twice: in the desktop app's Python engine now, and in the Rust harness SDK later. Both engines send the same five recording events (`turn_started`, `turn_ended`, `step_started`, `step_updated`, `step_ended`) to a recorder, which folds them into turns and steps. If both recorders fold the same events into the same recording, the app's screens, the token numbers and the export agree whichever engine ran the work. The golden test checks exactly that: **events in, recording out** (technical design §3.2 and §14, "The shared test").

Each scenario in this folder is one scripted run of the real engine from the Phase 1 lab ([phase-1/scenarios.md](../../phase-1/scenarios.md)), captured as:

| File | What it is |
|---|---|
| `<id>/events.jsonl` | Every recording event the run sent, one JSON object per line, in the order the recorder folded them |
| `<id>/expected-recording.json` | The recording the lab's recorder folded from them: `{"turns": [...], "steps": [...]}`, written by the lab's `canonical_json`, in recording format 1.0 |
| `<id>/expected-numbers.json` | Every number the Phase 1 screens show, computed from that recording by the lab's `calculator.py` |
| `index.json` | The scenario list: title, counts, known gaps, what is simulated, whether a second run sent its events in the same order |
| `vectors/` | The test vectors of recording-format.md in machine-readable form: `usage.json`, `raw-exchange-<wire>.json`, `piece-store.json` |

There are **30 scenarios**: S1-01 to S1-13, S1-16, and M-01a, M-01b, M-02 to M-05, M-06a, M-06c, M-07 to M-10, M-12 to M-14 and M-17.

**Not here.** Only scenarios that run a turn through the recorder produce events. The standalone scenarios S1-14 (machine and provider stamp), S1-15 (coworker fingerprint) and M-11 (token mapping) test small functions, not the recorder; S1-14 and S1-15 are covered by the stamp rules and M-11 by the usage test vectors in recording-format.md section 5 (and `vectors/usage.json`). S1-17 is a live-run checklist, not a scripted scenario.

## Format 1.0 (valid under 1.1)

Format 1.1 (2026-11-03) only adds two decision values, `readonly_tier` and `gate_tier`. No scenario here uses them, so every file in this folder is unchanged and valid under 1.1.


**Context.** The files here are recording format 1.0. The lab recorder was brought to the format on 2026-10-31 and this folder was made again from it, so:
- the events in every `events.jsonl` are format 1.0 events (section 2 of recording-format.md): each has `v`, `at_ms` and `mono_ms`; content travels in its own `content` part; every `step_ended` carries a copy of the step's identity; `turn_started` carries `first_message_id`;
- every `expected-recording.json` stores `duration_ms` (never `ended_at`), `call_index`, the auto-approver's `result`, `schema_version` on each turn, and steps in the canonical order of section 4, with `seq` counted per turn;
- every expected recording and every event validates against [recording-format.schema.json](../../recording-format.schema.json): **30 of 30 recordings and 466 of 466 events** (`check_golden.py --schema`, which fails on any invalid file);
- `check_golden.py --design-order` re-sorts each recording by the canonical order as recording-format.md writes it: all 30 are in that order. S1-11 has one tie up to `name`, broken by rule 6 (the parent's `call_index`).

**What moved when the folder was made again.** The numbers (`expected-numbers.json`) did not change in any scenario. The step order changed only where the design's order rule says it must:
- S1-05 and S1-07: a refused tool call (zero-length) at the same millisecond as the next model call now comes before that call (zero-length first, M-R3);
- S1-10 and M-06a: a summary and its own model call, which start together, are now summary first (kind order `context, summary, model, …`);
- M-09 and M-10: no order within a turn changed, but steps are now listed turn by turn (the title call of M-10's first turn is listed with that turn).

There are fewer events than before (466, was 559): a step's `message_id` now arrives on its `step_ended`, so the separate `step_updated` events that carried it are gone.

## How a second engine uses it

1. For each scenario folder, read `events.jsonl` line by line. Each line is one event.
2. Pass the events, in file order, to a fresh recorder: no state from another scenario.
3. Write the folded recording as JSON: `{"turns": [...], "steps": [...]}`, turns sorted by `started_at` then `turn_id`, steps by `seq`.
4. Compare it with `expected-recording.json` by the rules below.
5. Compute the screen numbers from the recording and compare them with `expected-numbers.json`.

The Python reference does exactly this in `check_golden.py`, with only `recorder.py` and `calculator.py`: it does not need the OpenWorker engine.

## Comparison rules

- **Compare JSON values, not text.** Key order and whitespace do not matter; `1` and `1.0` are different values (counts and times are integers).
- **Ids are compared as they are**, with one renaming. Every id in a recording comes from the events (turn ids, step ids, call ids, message ids), so the same events give the same ids in any engine. But the lab's step ids come from a counter whose order depends on thread timing, so the lab's `canonical_json` renames every step id to `s1`, `s2`, … by the step's position in the file (turns in order, each turn's steps by `seq`, then steps of no turn), and renames `parent_step_id` and a summary's `attrs.model_step_id` the same way. A second engine comparing against these files applies the same renaming.
- **Times are compared exactly.** The scenarios are scripted, so every time is exact (milliseconds from 0). The design's "ignoring ids and exact times" applies to the second kind of test, which runs a real engine and checks the events it sends; that set is not in this folder yet.
- **`seq` is compared**, so the canonical order is tested too.
- **`cache_share`** is a fraction rounded to 3 decimals; `null` when no step reports a cache read.
- **Nothing else is ignored.**

**`expected-numbers.json`.** `usage` holds the session numbers (design §6.3): `pill` (the token pill), `at_least` (the "≥" marker), `unreported_calls`, `failed_calls`, `overflow_calls` (calls over the context limit), `by_purpose`, `by_model` (a non-main purpose is added to the key, for example `anthropic:claude-sonnet-5 (title)`), `cache_share`, and `auto_approver_checks_not_reported` (a Draft 4 count, 0 everywhere since TD-16). `usage_by_session` repeats them per session when a scenario has more than one (M-09). `buckets_by_turn` holds each turn's time buckets (design §7.1): `waiting`, `tools`, `model`, `auto_approver`, `summary`, `other`, `other_shown` and `total`, in milliseconds.

## How the events were captured

The events are exactly what the lab's engine hooks sent, in the order the recorder folded them; nothing is corrected afterwards. The lab sends `turn_started` once the turn's first message has been appended, so that it carries `first_message_id`; the turn's other events wait for it and keep the times at which they happened.

A few scenarios with work on several threads (S1-07, S1-11, M-17) send their events in a slightly different order from run to run, but every order folds into the same recording; the files hold one run's order, so regenerating them can reorder lines of their `events.jsonl` without changing the expected recording. `index.json` says, per scenario, whether the two runs of the last `make_golden.py` sent them in the same order (`events_identical_on_second_run`); for these three it depends on thread timing.

## Known gaps

**Context.** A known gap is a place where today's engine sends less than the design asks for, fixed by a listed engine change. In this test the events come from today's engine, so the expected recordings show the gap; the fold itself is correct and passes. When the engine change lands, the events change and this folder is regenerated.

| Where | Gap | What the files show today | Fixed by |
|---|---|---|---|
| S1-13 | V-04: the sandbox's `network_note()` returns hosts only, not counts, first times or `late` | `content.blocked` entries with `count`, `first_at` and `late` `null` (the format's "not reported"), and `attrs.sandbox.blocked_count: null` | E13 |
| M-07 | C-5: the bad-arguments `TOOL_FINISHED` event has no tool call id; the lab finds it from the transcript | Nothing in the recording, which is valid; the lab notes the fallback beside it, and the scripted check reports the gap | E1 |
| `vectors/usage.json`, from M-11 | V-20: the app ignores Ollama's `prompt_eval_cached_count` | The three Ollama vectors fail in `check_golden.py --vectors` | E3 |
| `vectors/usage.json`, from M-11 and the probe | V-21: the app does not read `cache_write_tokens` and subtract it | `openai-chat-probe-call-1` fails | E3 |
| `vectors/usage.json` | V-13 (a cache write of 0 recorded as not reported), V-15 (Anthropic thinking tokens not read) | Four more vectors fail | E3 |

The lab's `provider_facts` now has exactly the design's names, so the raw-exchange vectors pass for both `provider_meta` and `provider_facts` (their `lab_provider_facts_differs` lists are empty).

Simulated parts (decision-model reviewers, the sandbox, the title call, `resolved_by`, the trigger) are listed per scenario in `index.json`.

## How to regenerate and check

From `phase-1/lab/`, with openworker installed (`pip install -e <openworker checkout>[dev]`); on Windows set `PYTHONIOENCODING=utf-8`:

```bash
python make_golden.py               # rewrite every scenario folder and index.json
python make_golden.py S1-03         # rewrite one scenario
python make_golden.py --vectors     # rewrite vectors/ from the scripted values and the captured live runs
python check_golden.py              # replay every scenario and compare; exit code 1 on any difference
python check_golden.py --schema     # also validate every file against recording-format.schema.json (pip install jsonschema)
python check_golden.py --design-order  # also re-sort by recording-format.md section 4 and report any difference
python check_shared_files.py        # check attributes.yaml, the schema and recording-format.md agree (pip install pyyaml)
python check_golden.py --vectors    # also run the test vectors through the app's code; known gaps reported
```

`make_golden.py` refuses to write a scenario unless replaying its captured events gives the run's own recording and numbers, a second run gives the same recording, and the events and recording validate against the schema. It never changes the lab's files or the runs in `phase-1/runs/`.

Any change to the golden files follows the change rule of recording-format.md section 7: a new format version, a changelog entry, regenerated files, and both engines updated.
