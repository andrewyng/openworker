# Golden test, Phase 3

*Written 2026-11-04 with [recording-format.md](../../recording-format.md) 1.3 and [signals.yaml](../../signals.yaml) 0.6; rebuilt the same day for 0.7, which adds only the not-installed vector. It is made by `phase-3/lab/make_golden3.py` and checked by `phase-3/lab/check_golden3.py`.*

## What it is

**Context.** Phase 3 adds up many recordings: the Usage page, the coworker page, TeamView and the Scheduled view. It also adds checks that look at more than one turn. Both engines must give the same totals for the same recordings, and the same issues for the same team, security task or automation. This folder pins both.

There are two kinds of scenario.

**Rollup scenarios (S3-01 to S3-15).** Feed `recording.json` and `issues.json` to an engine's rollups. It must give exactly `expected-rollup.json`.

| File | What it is |
|---|---|
| `recording.json` | Turns and steps as a format 1.3 recording, valid against [recording-format.schema.json](../../recording-format.schema.json). S3-01 is 30 days (1.3 MB), written one turn or step per line |
| `issues.json` | The stored check result of each turn, `{turn_id: {warn, info}}`, or `null` for a turn never checked. Rollups read stored results; they do not run the checks |
| `expected-rollup.json` | `turns`: each turn's total, "at least", unreported, failed and over-limit calls, waiting and active time, approval cards, warnings. `pills`: each session's total. `rollups`: the total per group, for 11 groupings, with keys joined by `\|` and `null` for no value. `turn_counts`: turns, checked and with a warning. `no_turn`: proxy calls. `approver`: the Auto-approve strip |
| `scenario.json` (S3-11) | S3-01 with one session deleted: the total from the steps, and the total from the usage ledger |

Every rollup uses the viewer's days in [vectors/days-europe-london.json](vectors/days-europe-london.json). The rule is in [tracing-spec.md](../../tracing-spec.md) §6:
- a step belongs to its turn;
- a turn belongs to the day it started;
- a day is the viewer's local day.

**Check scenarios (S3-40 to S3-62).** Feed `input.json` to an engine's series and task checks. It must give exactly `expected.json`.

| Kind | `input.json` holds | `expected.json` holds |
|---|---|---|
| `team` (S3-50) | A 1.3 recording of a lead and three workers, 58 turns, with `wake` and `stamp.team` | Turns explained by 1.2 (33) and by 1.3 (57) for the 95% gate; the split by wake reason, adding up to the team total; `lead_polling`; and the turn checks `wake_capped` and `watchdog_nudged` |
| `approvals` (S3-53) | A recording with approval cards and their tool steps (`args_hash`) | `same_approval_twice` |
| `board` (S3-54, S3-54c) | Board transitions in sequence order, and `blocks` links | `reviewed_before_prereq` |
| `coverage` (S3-40 to S3-44) | A one-turn recording of a coworker that declares `persona_coverage`, with scan steps (`scanner`, `exit_code`), plus the task's report, findings and fix | The coverage checks that fired, with their facts |
| `automation` (S3-60 to S3-62) | The automation's runs in order, and for S3-62 the run turns' recording (`automation`) and stored results | Each run's health and `failed_in_a_row` |

**Vectors.**

| File | What it is |
|---|---|
| `vectors/days-europe-london.json` | The 30 local days from 6 Oct 2026 as `[label, start, end)` epoch ms. 25 Oct is 25 hours long |
| `vectors/scanners.json` | Shell commands, and the `scanner` each must give by the rule in `signals.yaml` (`scanners`) |
| `vectors/scanner-not-found.json` | Ten shell steps after they ran (command, exit code, output), and whether the scanner was not installed, by `scanners.not_found` (0.7, P3B-1). When it was not, the recorded `scanner` is `null`. The answers are written by hand, from the app's own test |

`index.json` lists the 26 scenarios: what each one tests, whether its check should fire or stay quiet, and, for the edge cases, the values worked out by hand in [phase-3/scenarios.md](../../phase-3/scenarios.md).

## Built, not captured

These recordings were **built** from the scenarios (`phase-3/lab/synthetic3.py` and `checks3.py`), not captured from the engine, because the engine does not record the 1.3 fields yet. Once it does (engine changes E17, E18, E25, E26), each recording is replaced by a scripted run. The expected answers must not change. If they do, either the engine or the scenario is wrong, and the difference is a finding.

## How the check proves itself

`check_golden3.py` reaches the rollups two ways:
- the per-turn reference, which adds up whole turns in plain code;
- the store queries, which run one GROUP BY each over the recording loaded into the app's own two tables.

Both must equal the expected file. The edge cases must also equal the numbers worked out by hand. The 30 Phase 1 and 39 Phase 2 golden recordings are valid under 1.3 unchanged.
