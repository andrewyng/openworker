# OB-7: helpers and team workers in the timeline

**Context.** A turn can hand work to a helper (the `explore` agent) or to a team worker. Before this change, the Steps panel showed a helper's steps always open, with nothing on the `explore` row about how the helper ended. A lead's timeline had no trace of the workers it woke. TeamView's token totals also missed helper tokens, because it read them from the transcript and helper calls are not in the transcript (V2-29).

This branch builds OB-7 as Part E (§13) of `docs/observability/phase-2/technical-design.md` designs it. The recording fields follow `recording-format.md` §3.11 and `recording-format.schema.json` 1.2.

Branch `wip/ob7-nesting`, started from `wip/ob3-issues` (`208c03f7`). Not pushed, not merged.

## What was built

**E6b: what the helper did, on the `explore` step.**
- The `explore` step now has `attrs.helper = {index, status, model_calls, tool_calls, tokens}`. It is for display only: the pill still adds up the helper's own model steps.
- `index` is the explore call's place among the turn's explore calls, in the order the model listed them. So two helpers run in parallel are always Helper 1 and Helper 2, whichever thread starts first.
- `status` is `ok`, `step_limit` (the helper used its 10 steps), `error` or `interrupted`.
- The helper's model calls now go through the helper's own recording, which counts them. Before, they went straight to the parent's recording, so nothing could count them.
- Files: `tools/subagent.py`, `observability/engine_hooks.py` (`HelperRecording`, `TurnRecording.helper_index`).

**E19a: team ids on team tool steps, and who woke a worker.**
- Team tool steps carry `attrs.team = {team_id, item_id?, worker_session_id?}`. It holds ids only, never titles or text.
  - The board tools (`create_item`, `assign`, `transition`, `comment`) are covered in `teams/tools.py`.
  - `steer_worker`, `post_chat` and `decide_worker_call` are covered in `server/manager.py`.
  - An approved `propose_team` also carries `worker_session_ids`, the sessions it created. This is set in `engine.py` `_observe_event`, because `propose_team` is a waiting tool that never runs through the normal tool path.
  - A tool adds these ids through one small hook, `note_team()` in `engine_hooks.py`. It writes to the running tool's own step. Outside a recorded tool call it does nothing.
- A worker's turn carries `woken_by = {lead_session_id, team_id, item_ids, via}`.
  - The manager works it out when it wakes a worker (`_woken_by` in `server/manager.py`).
  - `via` is `steer` for `steer_worker`, `mention` when the wake holds only team-chat lines, `board` for other board activity, and `timer` for a timer.
  - `item_ids` lists every item the wake's rows name, once each. So a batched wake shows all of its items.
  - The engine takes it as a new `woken_by=` keyword on its turn entry points (`engine.py`), next to `trigger=`.
- A `steer_worker` delivery is recorded as `trigger: team_wake` with `via: steer`, not as `channel`. The trigger fix was already on the base branch (OB-3); this branch adds `via`.

**E19b: worker rows in the lead's timeline, worked out on read.**
- `RecordingStore.worker_turns(lead_session_id, since_ms)` finds the worker turns whose `woken_by.lead_session_id` is the lead.
- `read_api.worker_rows()` puts each one under the first lead turn that had not ended when it started. That is the lead turn it started in, or, if it started between two lead turns, the next one. A worker turn that starts after the lead's last turn ended waits for the lead's next turn.
- `GET …/recording/turns` now gives each lead turn a `workers` list. Each row has: session, turn, turn number, the worker's name from the team record, start, length, status, tokens, `item_ids` and `via`.
- The worker's tokens are display only. They are not in the lead's pill or the lead's turn usage.

**E19c: TeamView's totals come from the recording.**
- `teams/summary.py` now reads each member's tokens from its recording, counted the way the session's pill counts them, so helper tokens are included.
- A recording can be missing older turns, from before recording existed or deleted by "Keep for". So any transcript message older than the first recorded turn is still counted from the transcript.
- With no recording at all, TeamView works as before.

**E11 (part): the Steps panel.**
- Under each `explore` row there is now a summary row, "Helper 1 · 6 steps · 12.3k tokens · ok". The helper's steps are folded behind it by default; a click opens or closes them.
- While a helper still runs, or in a recording from before 1.2, the summary is counted from the helper's nested steps instead.
- Each worker row shows "Worker qa · turn 3", the worker's tokens as a badge, the turn's length, and an "Open worker session ↗" link. Rows sit in time order among the lead's steps.
- Strings are in English and Chinese.
- Files: `StepsPanel.tsx`, `api.ts`, `styles.css`, `locales/en.json`, `locales/zh.json`, and a one-line `onOpenSession` prop in `App.tsx`. The `StepsPanel.tsx` changes are limited to the helper and worker rows.

**Export.** A worker turn's span carries `openworker.team.id` and `openworker.lead.session.id`. `export_spans.py` already did this in OB-6, so it is not changed here; a test now covers it.

## Differences from the design

1. **Worker timers count as team wakes.** Before, only a lead's timer was `team_wake`; a worker's timer was `schedule`. The design lists `timer` as a `via`, which needs a worker's timer turn to be a team wake. So a worker's timer is now `trigger: team_wake` with `via: timer`. A session outside a team still records `schedule`.
2. **Worker board calls carry ids too.** The design's lead-side list is satisfied. A worker's own `create_item`, `transition` and `comment` also carry `attrs.team`, because the same tools serve both roles.
3. **Unknown ids are left out, not set to `null`.** `item_id` and `worker_session_id` are left out when not known. The schema accepts both forms.
4. **One more helper status.** `attrs.helper.status` can also be `interrupted` (a Stop), which the 1.2 schema allows and §13.3 does not list.
5. **The summary is its own row.** The helper summary is a separate row under the `explore` row, so the `explore` row can still be selected for its details.
6. **Per-item tokens still come from the transcript.** In TeamView, each item's tokens are still read from the transcript, because helper calls cannot be tied to an item there. Only member and team totals, and the usage chart's points, come from the recording.

## Tests run

- **New: `tests/test_observability_team_helpers.py`, 14 tests, all passed.** They cover:
  - the helper summary, and that the pill counts helper tokens once;
  - two parallel helpers numbered in the model's order;
  - a helper at its step limit;
  - the ids on the board tools, with no text in the step;
  - an approved `propose_team` naming the worker sessions it created;
  - `note_team` doing nothing outside a recorded tool call;
  - the ids on `steer_worker`, `decide_worker_call` and `post_chat`;
  - `woken_by` for steer, board (a batched wake), mention and timer; none for a lead or a solo session; and the schema check;
  - where worker rows go: inside a lead turn, between two lead turns, under a running lead turn, two workers woken by one action, and after the lead's last turn;
  - the read API through a real store, with the worker's tokens kept out of the lead's pill;
  - TeamView totals that include helper tokens and older transcript messages;
  - the worker span's team and lead names.
- **Related Python files, run one file at a time.** Covered: all 13 `tests/test_observability_*.py` files (golden 91 and schema 10 included), every `tests/test_team_*.py`, `test_subagent.py`, `test_engine.py`, and the other files that use the code changed here (wakes, deliveries, subscriptions, personas, gates, worker connectors).
  - Everything passed except 3 tests. None of the 3 touches code changed here; all are Windows failures from the known baseline:
    - `test_board_image_lifecycle::test_escape_and_symlink_to_ungranted_file_are_denied` and `test_team_rehearsal_fixes::test_runtime_facts_do_not_read_configuration_or_follow_workspace_symlinks` need symlink rights (WinError 1314);
    - `test_board_image_lifecycle::test_concurrent_identical_uploads_dedupe_without_shared_temp_race` hits a Windows lock on a file rename in the attachment store (WinError 5).
  - These 3 were not run again on the base branch.
  - `test_team_file_artifacts.py` passed with its known Windows hang, `test_text_pages_pin_version_and_replay`, left out.
- **GUI.**
  - `StepsPanel.test.tsx`: 21 tests passed, 5 of them new (folding, a running helper, the worker row and its link, no link without a handler, and Chinese).
  - The full unit suite (`npm run test`): 67 files, 650 tests passed.
  - `tsc --noEmit` is clean.

## What is not verified

- **Not tried in the running app.** Nothing was checked in a browser or the desktop app, and no real team run was made: port 8765 and the GUI on 1420 belong to another session. The panel was checked only through unit tests.
- **Worker rows do not appear live.** The lead's panel picks up a new worker row only when it reloads its turns, after the lead's own step events or a turn's end. A worker turn that starts while the lead is idle shows the next time the lead's turns are loaded.
- **Recording gaps stay gaps.** If recording was off for some turns in the middle of a session, TeamView's total from the recording misses those turns.
- **Export names not checked against a real backend.** The export names `openworker.team.id` and `openworker.lead.session.id` match `attributes.yaml` (they are marked `verified: false` there). They were not sent to a real backend. The docs repo was not edited.
