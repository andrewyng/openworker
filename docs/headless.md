# Running OpenWorker with nobody at the keyboard

OpenWorker has two headless modes. They answer different questions.

| Command | What it does | Who drives it |
|---|---|---|
| `openworker up` (after `openworker join`) | Keeps this computer serving as a machine the desktop app controls, forever | The desktop app: tasks and chat arrive over its channel |
| `openworker run` | Runs one task to the end and exits, leaving a record behind | You at a terminal, a script, a CI job, an evaluation harness |

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
| `ask` | asks | asks |
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

The desktop app offers `auto` as the third position of the Unattended toggle ("Answer for
me while I'm away"); there it can only combine with modes that keep the checks, so the
worst case is a refused card.

## `openworker run`

```
cd ~/src/project
openworker run --prompt "Fix the failing test in tests/test_parser.py"
```

The task comes from `--prompt`, from `--prompt-file`, or from standard input when it is
piped in. The coworker's answer is printed on standard output; progress, prompts and the
closing summary line go to standard error.

### What a run reads from this computer

A run uses this computer's OpenWorker settings: the default model, the stored provider
keys, the connectors you have connected, the sandbox and its allowed sites. It reads them
and changes none of them. The one thing it may write is a renewed sign-in, as the app does
when a sign-in expires.

A connector's actions go through the approval mode like any other tool call.

Each run is its own process. Two runs started from two terminals do not share anything
but those settings, and each is saved as its own session.

The app and any number of runs can work at the same time. When a sign-in needs renewing,
one of them renews it and the others use the result.

### Approvals and questions

These are separate, and each has its own switch.

**Approvals** follow `--approval-mode` (the table above). The default is `ask`.

| | An approval card |
|---|---|
| At a terminal | asked in the terminal: yes once, yes for this session, or no |
| No terminal (a pipe, a CI job) | refused and recorded; nothing hangs |

With no terminal, mode `ask` refuses every approval, and the run says so when it starts.
For a run with nobody present, use `--approval-mode auto-approve` (the reviewer decides)
or `--approval-mode bypass-approvals`.

**Questions** follow `--auto-answer`.

| | A question from the coworker |
|---|---|
| At a terminal | asked in the terminal |
| At a terminal with `--auto-answer` | answered by the fixed rule above, and recorded |
| No terminal | answered by the fixed rule, and recorded |

`--auto-answer` does not touch approvals: at a terminal they are still asked.

Two other requests go with the questions. A request for another folder, to read or to
write, is asked at a terminal: yes, yes but read only, no, or a different folder; with
`--auto-answer` or no terminal it is declined by rule. A pinned tool install is asked at a
terminal and installed by rule otherwise.

### Options

| Flag | Meaning |
|---|---|
| `--prompt TEXT` / `--prompt-file PATH` | the task. With neither flag, the task is read from standard input when it is piped in (`cat task.md \| openworker run`); `--prompt-file -` does the same |
| `--workspace DIR` | the folder the agent works in; the current folder when left out |
| `--add-dir DIR` | an extra folder the agent may read and write, beside the workspace (repeatable), for a harness whose output contract lives outside the workspace — e.g. `--add-dir /output`. The file tools only write inside the session's folders, in every mode; the shell is not scoped, so without this a delivery would depend on which tool the model happened to pick. Recorded under `args.extra_dirs` in `summary.json`. |
| `--coworker ID` | the coworker that does the task, by its id: `cowork` (default) or `code`. An unknown id stops the run with exit code 2 |
| `--model ID` | `provider:model` or `provider/model` (first slash splits). Default: this computer's setting |
| `--approval-mode` | `ask` (default), `auto-approve`, `bypass-approvals`, `dangerously-bypass-approvals` |
| `--auto-answer` | nobody will answer questions; see above |
| `--allow-site HOST[:PORT]` | a site this run's commands and web tools may reach, beside this computer's allowed sites (repeatable). For this run only; the computer's list is not changed. Exact host names, no wildcards |
| `--allow-sites-file FILE` | the same, from a file: one `HOST[:PORT]` per line, `#` starts a comment |
| `--out DIR` | where the record goes. Default: `sessions/<workspace path>/<session id>/` in OpenWorker's state folder, the workspace path written with dashes |
| `--isolated` | read nothing from this computer; see below |
| `--reasoning-effort` | `low` … `max`, sent to the provider; unset = provider default, recorded |
| `--max-output-tokens`, `--max-iterations`, `--timeout-seconds` | ceilings; `--timeout-seconds 0` when an outer runner enforces its own |
| `--tool-result-max-bytes` | bound each tool result (default 10,000; 0 = off); full text spilled under `out/tool-output/` |
| `--provider-order` | OpenRouter only: pin the upstream host, no fallback |
| `--trajectory-atif PATH` | also write the trajectory here |

### Isolated runs, for benchmarks

```
openworker run --isolated --prompt-file task.md --workspace /work \
    --model anthropic/claude-sonnet-5 --approval-mode bypass-approvals --out ./run-record
```

`--isolated` reads nothing from the computer: no settings, no stored keys, no sandbox. The
model key comes from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`TOGETHER_API_KEY`, `OPENROUTER_API_KEY`, …), and OpenWorker's state and scratch folders
are placed under `--out`. Two runs of the same task on two computers then start from the
same place. It needs `--out` and `--model`, and a harness should pass `--approval-mode` too, so the
record states it.

### Exit code

| Code | Meaning |
|---|---|
| 0 | the task finished |
| 1 | the run crashed before it could write a record |
| 2 | the command line could not be run as given |
| 3 | the run stopped early (time limit, model error, iteration limit, cut-off reply); the record is written |
| 130 | stopped with Ctrl-C or a stop signal (what `timeout`, a CI job or `docker stop` sends); the record is written |

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

The trajectory and the exit code are the stable contract.

A transient provider failure or an empty reply does not end the run: the runner waits
and re-enters the conversation with a nudge. The waits grow: 2 s, 5 s, 10 s, 30 s, 60 s,
120 s, 240 s, 300 s, 300 s. The first ones are short because the usual failure is one
dropped connection, which the next request gets past.
Permanent errors (bad key, unknown model, context overflow) are not retried.

Each retry prints a line with the error and its cause, for example
`provider error (APIConnectionError: Connection error. Cause: the proxy answered 403
Forbidden); retry 1/9 in 2s`. A stop signal or Ctrl-C ends the run at once, also during
the wait between retries, and the record is still written.
