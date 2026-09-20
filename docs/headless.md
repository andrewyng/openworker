# Running OpenWorker with nobody at the keyboard

OpenWorker has two headless modes. They answer different questions.

| Command | What it does | Who drives it |
|---|---|---|
| `openworker up` (after `openworker join`) | Keeps this computer serving as a machine the desktop app controls, forever | The desktop app: tasks and chat arrive over its channel |
| `openworker run` | Runs one task to the end and exits, leaving a record behind | A script, a CI job, an evaluation harness |

This page is about `openworker run` and the two switches that decide what happens when
the agent would normally stop and ask.

## Two switches: approving and answering

**Approving** is a yes/no on a tool call the agent has already decided to make. It is
answered by a person, by the reviewer model, or by the permission *mode*.

**Answering** is the agent asking for something it does not have: a question, a folder,
a tool install. The answer is content, not permission. It is answered by a person, parked
in the Inbox, or supplied by the engine. That is the *attendance* setting.

### Mode (approving)

| Mode | Routine tool calls | The safety checks (run a downloaded file, write outside the workspace, edit git hooks / CI / settings files, grant authority that outlives the session) |
|---|---|---|
| `interactive` | asks | asks |
| `auto-approve` | the reviewer decides; anything it is unsure about asks | asks |
| `bypass-approvals` | runs | asks |
| `dangerously-bypass-approvals` | runs | runs, each one recorded as "cleared by mode" |

`bypass-approvals` is for someone who wants no reviewer and few prompts but keeps the
basic checks. `dangerously-bypass-approvals` grants every approval and switches the
checks off. Use it only on a disposable machine or container. It is never offered in the
desktop app; the server accepts it only when started with `--allow-dangerous-mode`, and
`openworker run` prints a warning line when it is on.

### Attendance (answering)

| Attendance | Questions, folder requests, pinned tool installs | An approval card only a person could clear |
|---|---|---|
| `attended` | appear on screen | appears on screen |
| `inbox` | parked in the Inbox until someone returns | parked in the Inbox |
| `auto` | answered by the engine, by fixed rule, and recorded | refused, unless the mode clears it |

In `auto`:

- a question gets: *"No one is available. Choose the least destructive option that still
  satisfies the task as written."*
- a folder request is declined with guidance: work inside the workspace (or, in the
  dangerous mode, use the shell for paths outside it), and stop and say what is missing if
  the task cannot continue without the user;
- a pinned catalog tool (any tool in `coworker/toolchain.py`'s catalog) is installed by the
  verified installer, same version and checksum as the desktop card would use;
- an approval card that would have needed a person is refused, never parked. Nothing hangs.

`openworker run` always uses `auto`. The desktop app offers it as the third position of the
Unattended toggle ("Answer for me while I'm away"); there it can only combine with modes
that keep the checks, so the worst case is a refused card.

## `openworker run`

```
openworker run --prompt "Fix the failing test in tests/test_parser.py" \
    --workspace ~/src/project --model anthropic/claude-sonnet-5 \
    --mode bypass-approvals --out ./run-record
```

Options:

| Flag | Meaning |
|---|---|
| `--prompt TEXT` / `--prompt-file PATH` | the task |
| `--workspace DIR` | the folder the agent works in |
| `--model ID` | `provider:model` or `provider/model` (first slash splits) |
| `--mode` | see above; default `bypass-approvals` |
| `--attendance` | `auto` (the only value here) |
| `--persona` | `cowork` (default) or `code` |
| `--reasoning-effort` | `low` … `max`, sent to the provider; unset = provider default, recorded |
| `--max-output-tokens`, `--max-iterations`, `--timeout-seconds` | ceilings; `--timeout-seconds 0` when an outer runner enforces its own |
| `--tool-result-max-bytes` | bound each tool result (default 10,000; 0 = off); full text spilled under `out/tool-output/` |
| `--provider-order` | OpenRouter only: pin the upstream host, no fallback |
| `--out DIR`, `--trajectory-atif PATH` | where the record goes |

The model key comes from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`TOGETHER_API_KEY`, `OPENROUTER_API_KEY`, …). OpenWorker's state and scratch folders are
pointed under `--out` before the engine starts, so a run never touches the machine's own
conversations, keys or settings.

### The record

| File | Contents |
|---|---|
| `trajectory.json` | the run in ATIF-v1.7, with per-call token counts, the serving host, and an estimated cost from `coworker/headless/prices.yaml` (a cross-check; a consumer should price the counts itself) |
| `summary.json` | outcome, iterations, tool calls, tokens, cost, the arguments, the context window and compaction trigger used, the hosts that served the run |
| `answers.json` | every answer the engine gave on the absent user's behalf: refused cards, cleared checks, answered questions, declined folder requests, installs |
| `events.jsonl` | every engine event, in order, written as it happens |
| `messages.json` | the final conversation in OpenWorker's own shape |
| `model_calls.jsonl` | one line per model call: stop reason, usage, ceiling, effort, host |
| `provider_errors.log` | full cause chains of failed model calls, credentials redacted |
| `audit.db` | OpenWorker's audit log for the session |

The trajectory and the exit code are the stable contract. Exit code 0 means a trajectory
was written (completed, iteration cap, timeout, model error); 1 means the run crashed
before producing one.

A transient provider failure or an empty reply does not end the run: the runner waits
(30 s, 60 s, 120 s, 240 s, 300 s, 300 s) and re-enters the conversation with a nudge.
Permanent errors (bad key, unknown model, context overflow) are not retried.
