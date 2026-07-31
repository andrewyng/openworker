# Using skills

A skill is a folder of instructions the agent can pull in when a task calls for it —
the same `SKILL.md` format Anthropic's tools use, so a skill written for Claude Code
works here and vice versa. Use one to
teach OpenWorker a repeatable procedure: your report format, a deploy checklist, how to
fill in a specific spreadsheet, the house style for customer emails.

## Where skills live

| Directory | Scope |
|---|---|
| `<state-dir>/skills/<skill-name>/` | Global — every session |
| `<workspace>/.coworker/skills/<skill-name>/` | One project |

`<state-dir>` is `~/.config/coworker` on macOS/Linux and `%APPDATA%\coworker` on
Windows (`$COWORKER_STATE_DIR` overrides it anywhere). Each skill is one folder
containing a `SKILL.md`, plus any files it wants to reference.

## Format

```markdown
---
name: weekly-report
description: Write the weekly status report. Use when asked for the weekly report or a status update.
---

# Weekly report

1. Read `template.md` in this skill's folder for the section layout.
2. Pull last week's numbers with the `github` and `linear` tools.
3. Keep the summary under 200 words; numbers go in the table, not the prose.
```

Frontmatter:

- `name` — the skill's id (defaults to the folder name).
- `description` — **the one line the model sees up front**, so make it say both what
  the skill does and when to reach for it; a vague description means the skill never
  gets loaded.
- `allowed-tools` — optional, comma-separated. Parsed for compatibility with the
  shared format, but currently informational: what the agent may actually *do* is
  governed by OpenWorker's permission modes and approvals, not by this list.

The body is free-form markdown. It can reference files shipped in the skill's folder
(templates, examples, scripts) by relative name — the agent gets the folder's path
when it loads the skill and reads them with its normal tools. A script bundled with a
skill still runs through the shell tool's approval gate like any other command.

## How the agent uses them — progressive disclosure

Skill bodies are not stuffed into every prompt. At session start the agent sees only a
catalog — each skill's name and description. When a task matches, it calls the
`load_skill` tool, which returns the full instructions plus the folder path. So you
can keep many skills installed without weighing every conversation down; only the
description line is always-on context.

That also means the description is doing the routing. If a skill isn't being picked
up, sharpen its description ("Use when …") rather than growing the body.

## Skills, `AGENTS.md`, and personas

- `AGENTS.md` (global in `<state-dir>`, per-project in the workspace root) is standing
  guidance injected into **every** session — use it for always-true preferences.
  A skill is for procedures that apply only to some tasks, loaded on demand.
- A persona is a bigger unit: a persona defines who the agent *is* (system prompt,
  tools, connectors), and may recommend skills; a skill just teaches a procedure.

## Checking what's installed

The sidecar lists discovered global skills at `GET /v1/skills` (token header
required). Workspace skills are discovered per session from the session's workspace.
