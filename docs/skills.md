# Using skills

A skill is a folder of instructions the agent pulls in when a task calls for it — the
same `SKILL.md` format Anthropic's tools use, so a skill written for Claude Code works
here and vice versa. Use one to teach OpenWorker a repeatable procedure: your report
format, a deploy checklist, how to fill in a specific spreadsheet, the house style for
customer emails.

Manage skills from **Settings ▸ Skills**: create and edit in place, upload, move
between scopes, enable/disable, or reveal the folder on disk. The folder is the truth —
everything below works the same whether a skill arrived via the app or was dropped into
the directory by hand.

## Scopes

| Directory | Scope |
|---|---|
| `<state-dir>/skills/<skill-name>/` | Global — every session |
| `<workspace>/.coworker/skills/<skill-name>/` | Project — sessions in that workspace |

`<state-dir>` is `~/.config/coworker` on macOS/Linux and `%APPDATA%\coworker` on
Windows (`$COWORKER_STATE_DIR` overrides it anywhere). A project skill lives in the
repository, so committing `.coworker/skills/` shares it with teammates for free. When a
project skill and a global skill share a name, the project copy wins.

Skill names become folder names, so they are restricted to letters, digits, dots,
dashes, and underscores (max 64 characters).

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
- `source` — provenance stamp (`uploaded` for uploads); absent means created locally.
- `allowed-tools` — optional, comma-separated. Parsed for compatibility with the
  shared format, but currently informational: what the agent may actually *do* is
  governed by OpenWorker's permission modes and approvals, not by this list.

The body is free-form markdown. It can reference files shipped in the skill's folder
(templates, examples, scripts) by relative name — the agent gets the folder's path when
it loads the skill and reads them with its normal tools. A script bundled with a skill
still runs through the shell tool's approval gate like any other command.

## How the agent uses them — progressive disclosure

Skill bodies are not stuffed into every prompt. At session start the agent sees only a
catalog — each enabled skill's name and description. When a task matches, it calls the
`load_skill` tool, which returns the full instructions plus the folder path. So you can
keep many skills installed without weighing every conversation down; only the
description line is always-on context.

That also means the description is doing the routing. If a skill isn't being picked up,
sharpen its description ("Use when …") rather than growing the body. A skill created
mid-session is loadable immediately (`load_skill` rescans on a miss); its catalog line
appears from the next session.

## Turning skills off

Two switches, both personal:

- **Settings ▸ Skills** disables a skill everywhere. The off-switch lives in your
  `<state-dir>/skills-settings.json`, deliberately *not* inside the skill folder — a
  project skill travels with the repo, and your disable must not be committed to
  teammates.
- **Per session**, a skill can be muted for just that conversation.

Any-off-wins: a Settings disable cannot be resurrected by a session toggle. If a
skill's instructions were already loaded into a running conversation, disabling it also
tells that conversation to stop following them — instructions already in history would
otherwise keep steering the model.

## Adding skills

- **Create in the app** — Settings ▸ Skills ▸ New: name, description, instructions.
- **Upload** — a `.zip` containing exactly one skill (its `SKILL.md` at the root or in
  one top-level folder; resource files ride along), or a bare `SKILL.md` whose
  frontmatter carries the name. Uploads are staged and previewed — nothing lands in a
  scope directory until you confirm.
- **Drop a folder** into a scope directory by hand.
- **Ask the agent** — after working out a procedure in conversation, the agent can
  offer to save it via the `save_skill` tool. The save is approval-gated: the card
  shows the full name, description, instructions, and any bundled files before
  anything is written, and bundled files may only come from the session's own folders.

## Skills, `AGENTS.md`, and personas

- `AGENTS.md` (global in `<state-dir>`, per-project in the workspace root) is standing
  guidance injected into **every** session — use it for always-true preferences.
  A skill is for procedures that apply only to some tasks, loaded on demand.
- A persona is a bigger unit: a persona defines who the agent *is* (system prompt,
  tools, connectors), and may recommend skills; a skill just teaches a procedure.

## For API users

The sidecar exposes management over REST (token header required):
`GET /v1/skills?workspace=…` (rows with scope, source, enabled, file count),
`POST /v1/skills`, `PATCH/DELETE /v1/skills/{name}`, `POST /v1/skills/{name}/move`,
`POST /v1/skills/upload` + `/upload/confirm` (staged), and per-session toggles at
`GET/POST /v1/sessions/{id}/skills`.
