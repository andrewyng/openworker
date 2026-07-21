# Coworker Personas — design

**STATUS: design (draft for review).** Discussed with Rohit 2026-06-26. Generalizes the
fixed surfaces of `SURFACES.md` into declarative, distributable **personas**. Supersedes
the four-fixed-surfaces model: **Code becomes a built-in persona, Cowork becomes the
default persona, and MyHelper goes away** — replaced by pinnable long-running persona
sessions. Decisions log at the bottom.

## Why

Today the product ships a fixed set of hardcoded agents (Chat / Code / Cowork / MyHelper).
We want **specialized coworkers** — a SWE Coworker, an Ops Coworker, later Marketing and
Sales — and we want third parties to distribute their own (internal or external) without
forking the app.

The key realization: a "persona" needs **no new primitive**. It is a *packaging and
configuration layer* over things we already have — a system prompt, a tool allowlist,
skills, connectors/MCP, a default permission mode, a recommended model. So a persona is a
**declarative bundle**, and our own built-ins are expressed in the *same* format third
parties use. If SWE Coworker can't be written as a persona manifest, the format isn't good
enough yet (the dogfooding test).

## The model: three orthogonal concepts

Pulling these apart keeps the data model and UI clean — one mechanism covers all three.

| Concept | What it is | Notes |
|---|---|---|
| **Persona** | a *definition / template* (the manifest) | lifecycle: **installed → enabled → surfaced** |
| **Session** | a *running instance*, born from exactly one persona | **pinnable** + **renameable**; identity fixed at birth |
| **View** | how sessions are *grouped* in the sidebar | per-persona **or** flat under Coworker — pure presentation |

- A **session is born from exactly one persona.** It can pull in skills mid-flight, but its
  identity (prompt, tool surface, defaults) is fixed.
- **Pinning is purely an accessibility affordance** — a bookmark. It keeps a session one click
  away (in the sidebar's Pinned band) and has **no behavioral meaning**: it grants no powers,
  changes no permissions, and does not make a session "run." See *No "always-on" mode* below.
- **View** is just a grouping toggle; every session already knows its persona, so nothing in
  the data model changes.

## Family — the workspace model (and the first-class "specialness")

Personas are **not flat**. Every persona belongs to a **family**, and the family is what carries
a persona's structural specialness. Critically, **family encodes the *workspace model*, not the
job role** — and it is **binary**:

| Family | Workspace model | Starts with | UI consequences |
|---|---|---|---|
| **`code`** | works *inside a repo you pick* (git-bound) | a chosen repo, **writable** | Projects grouping, git/diff tools, explorer subagents |
| **`knowledge`** | *produces deliverables* in a scratch folder | an **auto-provisioned writable scratch** (no repo needed) | no Projects; outcome-oriented; low barrier to begin |

**Roles compose on top of a family** — a role = family + tools + connectors + prompt:
- **DevOps** = `code` family (lives in IaC/repos) + shell + cloud/k8s connectors + ops prompt.
- **SecOps** = `code` family (scans repos) + security tools + alerting connectors + a read-leaning prompt.
- **Ops / Marketing / Sales** = `knowledge` family + role tools/connectors/prompt.

So we do **not** add families per role (`devops`, `secops`, …). Family stays binary; everything
role-specific is composition. **Code stays a persona** — the canonical `code`-family persona —
which is exactly what lets a third party ship a DevOps/SecOps/Rust coworker that inherits all the
code-family behavior for free.

**Folders make everything else possible (power users).** Family sets the *starting point and
defaults*, not a hard limit:
- A **knowledge** persona can **add a repo/dir to read** (default read-only) or a dir to **write**
  results into. It already has a writable scratch, so it rarely needs more to begin.
- A **code** persona can **add read-only reference repos/dirs**, or a second **writable** repo for
  cross-repo work.
- **Per-folder writability is a user choice in both families**; family only chooses the sensible
  default/emphasis. (This is already the backend model: writable scratch + `extra_roots` with
  per-root `writable` + the `request_directory` grant flow for knowledge; a single writable repo
  root for code.)

**The frontend must be family-aware, not id-aware.** "New Project"/grouping, the add-folder
defaults, git affordances — all key off `family === "code"`, **never** a hardcoded `"code"` id.
(Today the sidebar hardcodes the id; that's the bug behind "why does only Code have Projects.")

## What a persona is (the bundle)

A persona manifest is **skill-shaped markdown with richer YAML frontmatter** — `persona ⊇
skill`, so authoring is familiar. The markdown body *is* the system prompt; the frontmatter
declares the capability surface.

```yaml
---
id: ops-coworker
name: Ops Coworker
icon: ops          # from the built-in icon set
tagline: Watches your infra and runs the runbook
tools: [shell, files, web_search, http]   # vetted-catalog IDs (allowlist)
connectors: [pagerduty, datadog]          # expected integrations
mcp: []                                    # MCP servers it wants
recommended_models: [anthropic:claude-opus-4-8, openai:gpt-5.5]
skills: [incident-runbook, postmortem-writer]  # referenced skill IDs
default_permission_mode: read-only         # cannot self-grant higher
family: knowledge                          # code | knowledge — the workspace model (see Family)
workspace: deliverable                     # git | deliverable | none
automations: []                            # optional starter schedules
---
You are the Ops Coworker. Your job is to ... (operating doctrine)
```

- **Tools are referenced by ID** from a vetted catalog (see Distribution). A persona never
  ships executable code.
- **`recommended_models` are recommendations, not requirements.** If the user runs a
  different / unconfigured model, that's their choice — at the risk of worse results. We
  surface a gentle nudge (reusing the existing `model_ready` machinery), never a hard block.
- **`default_permission_mode`** is the *starting posture*. A persona cannot self-escalate; a
  higher posture requires explicit user opt-in at enable time.

### Lifecycle states

1. **Installed** — present on disk (built-in, local dir, or fetched from a git URL).
2. **Enabled** — the user turned it on in the Personas settings panel.
3. **Surfaced** — the user chose to show it as a selector when starting a new Coworker
   session. (Enabled-but-not-surfaced personas still run; they just don't clutter the
   new-session picker.)

**Cowork is the default persona and is always present**, but can be disabled for users who
want only specific personas. **If Cowork is disabled, the new-session button defaults to the
user's chosen default persona** (set in the same panel) — so there's never a dead end.

### The collapse (replaces SURFACES.md)

- **Code → a built-in `code`-family persona** (git-bound workspace, coding tools).
- **Cowork → the default `knowledge`-family persona** (deliverable workspace, general tools).
- **MyHelper → gone.** Its "personal helper" role becomes *a pinned, renamed persona session*.
  The win: any persona session can be triggerable (self-wake / messages / events), and there's
  no special fourth subsystem to maintain. There is no "always-on" mode (see above).

## Sidebar information architecture

Decided with Rohit (UX pass, 2026-06-27). Layout = **"Pinned band on top"**:

```
✦ OpenCoworker                       ⚙
🔍 Search   🔌 Integrations   ⏰ Automations   ← shared top zone (global, all coworkers)
── PINNED ──────────────────────────
📌 Ops Watcher              ·  (liveness dot)
📌 Daily Brief            ②   (attention count)
── SURFACES ─────────────────────────
▾ ⬥ OpenCoworker      ★default
    Q3 research memo    ●  (attention on this session)
    + New session
▸ ⬦ Ops Coworker      ①   (rolled-up attention; collapsed)
▸ ◧ Code        › Projects
─────────────────────────────────────
📥 Inbox ②   🗒 Audit   ⚙ Manage
```

Rules:
- **Shared top zone** above the persona accordions for things common to *all* coworkers:
  **Search**, **Integrations** (the global connect page), **Automations**. (Previously these
  lived inside the Cowork accordion only, so Code/Ops couldn't reach them — a real gap.) The
  footer keeps the meta items: Inbox, Audit, Manage.
- **Default persona (OpenCoworker) leads** the persona list; **Chat is hidden by default**
  (not-surfaced — recoverable from the Personas tab, not deleted; Cowork covers quick Q&A).
- **A cross-persona "Pinned" band sits at the top.** It contains **only manual pins** (pure
  bookmarks). Nothing auto-surfaces into it.
- **Expand ≠ switch.** Expanding a persona accordion **browses** its sessions; the chat area
  **only changes when you actually pick a session or hit New session** — so you can peek at
  another persona's sessions while keeping your current conversation loaded.
- **Projects nest only under `code`-family personas** (family-aware, not id-aware).

### Attention vs. liveness (sidebar indicators)

Two **orthogonal** per-session signals, neither tied to pinning (any session can show either):
- **Attention** = an **Inbox item is pending** for that session. Rendered as an **amber count**
  that **bubbles up**: session row → persona accordion header (rolled-up count) → footer
  **Inbox** total. All three are *views of the one Inbox queue* — answering anywhere resolves it
  everywhere (the resolve-once, first-responder-wins state machine in `PERMISSIONS-AND-INBOX.md`).
  There is **no separate "Needs attention" tab** — that would just rebuild the Inbox.
- **Liveness** = "working now" or "sleeping with a pending wake." A **quiet, count-less dot**.
  Informational; it **never bubbles into a count** (so idle scheduled agents don't inflate the
  attention number).

## Distribution

**Hard rule: we never ship or run third-party executable code.** The platform ships a broad
catalog of **vetted tools**; personas compose them by ID. New capabilities arrive via **MCP**
(which has its own boundary), not via persona code. This makes "install a persona" a *light*
trust event — the user consents to a declared set of tools / connectors / permission mode,
not to arbitrary code.

Two distribution modes, different weight:

1. **Persona packages (lightweight, build first).** A folder + manifest, loaded from a
   **local dir** (`~/.coworker/personas/`) or a **git URL**. Runs on stock OpenCoworker. This
   is how "Acme Ops Coworker" reaches people who already run the app. At install we show an
   **install-time capability consent**: "This Coworker will use shell, files, PagerDuty, and
   defaults to read-only mode. Allow?"
2. **White-label builds (heavy, later).** A company configures/brands the app, bakes in its
   default personas, and ships it as *their own* coworker product.

The **vetted-tool catalog** (how tools are namespaced/grouped so frontmatter can reference
them cleanly) is designed in `PERMISSIONS-AND-INBOX.md`.

## No "always-on" mode — being triggerable is a consequence, not a state

There is **no "always-on" agent class.** Every session is equally "on": it can resume whenever
*something can trigger it* — a self-wake (`sleep_*`), an inbound message, or (later) an event.
Being triggerable is simply a **consequence of the tools/connectors that session holds**, not a
mode you switch into. A plain Cowork session that called `sleep_for` is exactly as "always-on" as
a dedicated Ops watcher. This is why **pinning is pure accessibility** (above) and never a
behavioral toggle — there's no behavior to toggle.

## Long-running agents — self-wake tools

A long-running agent is not a process that's *always running* (that was MyHelper's cost
problem). Instead it is **suspend/resume — event-driven**: it sleeps at ~zero cost and the
scheduler **re-invokes the session** on a trigger. "Many long-running agents" is cheap precisely
because idle ones aren't running.

Triggers (a small set):

1. **Timer** — `sleep_for(duration)` / `sleep_until(time)`.
2. **On-completion** — `wake_on(job_id)` when a backgrounded command/job exits; the exit code
   and output are handed back on resume.
3. **On-message (free)** — a user steering message into the session wakes it (reuses the
   inbound queue/steering of the messaging connectors).
4. *(Later)* **On-event** — a connector/webhook fires (an email arrives, a PR opens, an alert
   triggers). Same plumbing; this is what makes a pinned Ops Coworker genuinely useful.

**Relationship to Automations** (`AUTOMATION-SCHEDULING.md`): different surfaces over the
**same scheduler / TaskStore**. An Automation is *user-authored* — a cron that **spawns** a
session. Self-wake is *agent-authored* — a live session scheduling **its own** resumption.
Both reduce to "enqueue a future invocation of a session," so they share plumbing but stay
distinct in the UI.

## The Inbox

> Full design — risk classes, modes, the **Unattended** toggle, item state machine, resume
> reconciliation, and multi-inbox routing — lives in `PERMISSIONS-AND-INBOX.md`. Summary below.

The **Inbox** is the canonical, cross-session **human-attention queue**. While you're working
in one session, it tells you another agent needs you — an approval, a question, or a result.
It decouples agent progress from your attention: agents keep working (or sleep) and route the
human-needed items to **one place**.

Three item kinds, each **deep-linking back to its originating session**:

- **Approval** — actionable allow/deny with context ("Ops Coworker wants to restart the
  service").
- **Question** — a free-text answer the agent needs to continue.
- **Notification** — FYI / completion ("your report is ready" → links to the artifact).

**The Inbox is the store of record; messaging connectors and a future mobile app are
*transports* of the same items**, not separate notification systems. Slack / Telegram / OCW
Mobile render and deliver inbox items to wherever the user is, and replies flow back as
steering. One item model, many delivery channels — matches the existing messaging-connector
design (outbound `send_message` + inbound steering).

**Why this comes before guardrails:** if consequential actions must route to the Inbox rather
than self-execute, *the human becomes the rate-limiter*. An unattended agent can't run away if
every consequential step waits on a person. So the Inbox is also a lightweight substitute for
the heavier safety machinery below.

## Tabled for later — unattended guardrails

Wake budgets / rate caps and runaway-loop detection are **noted but deliberately not designed
yet.** With the Inbox gating consequential actions, the immediate need is lower. Revisit once
truly autonomous (non-gated) unattended work is on the table.

## Phasing

> Live progress + per-phase test gates: `IMPLEMENTATION-LEDGER.md`.

- **Phase 0** — foundation: the vetted tool catalog + risk-class refactor of `permissions.py`
  (no behavior change). Both personas and permissions sit on it. See `PERMISSIONS-AND-INBOX.md`.
- **Phase 1** — persona registry + manifest format; convert Code/Cowork into built-in
  personas; ship 2–3 defaults (SWE, Ops, generic); persona picker at new-session; Personas
  settings panel (enable / surface / set-default). Pinnable + renameable sessions.
- **Phase 2** — load personas from local dir / git URL + install-time capability consent;
  self-wake tools (timer + on-completion); the Inbox (in-app).
- **Phase 3** — connect the Inbox to Slack/Telegram/mobile; on-event wake; registry /
  marketplace; white-label build tooling.
- **Phase 4 (UX/IA)** — family-aware frontend (Projects/folders keyed off `family`, not id);
  "Pinned band on top" sidebar; default-persona-first ordering; Chat hidden by default;
  expand≠switch; attention badges bubbling session→persona→Inbox + a count-less liveness dot.

## Open questions

- **Persona versioning / update** — refresh semantics for git-URL personas; namespacing to
  avoid collisions between two "Ops Coworker"s from different authors.
- Unattended guardrails (tabled, above).
- (Tool catalog, risk classes, Inbox persistence — now in `PERMISSIONS-AND-INBOX.md`.)

## Decisions log

- **2026-06-26** — A persona = declarative skill-shaped bundle; **no third-party executable
  code**; tools referenced from a vetted catalog by ID; new capability via MCP.
- **2026-06-26** — Recommended (not required) models; user may override at their own risk.
- **2026-06-26** — A session is born from **exactly one** persona (identity fixed; may pull
  skills mid-flight).
- **2026-06-26** — Three orthogonal concepts: Persona (installed→enabled→surfaced) / Session
  (pinnable + renameable) / View (per-persona or flat).
- **2026-06-26** — Code → built-in persona; Cowork → default persona (disable-able; if off,
  new-session defaults to the user's chosen default persona); **MyHelper removed**, replaced
  by pinned long-running persona sessions.
- **2026-06-26** — Long-running = suspend/resume via self-wake (timer / on-completion /
  on-message; on-event later); shares the scheduler with Automations but is a distinct,
  agent-authored surface.
- **2026-06-26** — **Inbox** is the canonical human-attention queue (approval / question /
  notification, each deep-linking to its session); messaging connectors + mobile are
  *transports* of the same items; it also serves as the lightweight rate-limiter.
- **2026-06-26** — Wake budgets / runaway detection **tabled** (not yet designed).
- **2026-06-27** (UX pass) — **Family is binary** = the *workspace model* (`code` = git-bound,
  Projects/diffs/explorer; `knowledge` = auto-scratch deliverables). Roles (DevOps/SecOps/Ops/…)
  **compose on top** (family + tools + connectors + prompt); we do **not** add families per role.
  DevOps/SecOps are `code`-family.
- **2026-06-27** — **Code stays a persona** (canonical `code`-family persona); `family` is the
  first-class "specialness". Frontend must be **family-aware, never id-aware** (the `"code"` id
  hardcode is the bug behind "only Code has Projects").
- **2026-06-27** — **Per-folder writability is a user choice in both families**; family only sets
  the default/emphasis. Knowledge can add read/write folders; code can add read-only refs or a
  2nd writable repo. (Already the backend model.)
- **2026-06-27** — **No "always-on" mode.** Every session is equally triggerable as a consequence
  of its tools/connectors (self-wake / message / event). **Pinning = pure accessibility** (a
  bookmark), no behavioral meaning. Supersedes the "pinned = your always-on agent" framing above.
- **2026-06-27** — Sidebar IA = **"Pinned band on top"**: cross-persona Pinned band (manual pins
  only) → default-first persona accordions → footer nav. **Expand≠switch**; **Chat hidden by
  default**; Projects only under `code` family.
- **2026-06-27** — Sidebar **attention** (Inbox-pending) = amber **count** bubbling
  session→persona-header→footer Inbox (views of one queue, not a new tab); **liveness**
  (working/sleeping) = a separate **count-less** dot. Orthogonal to pinning.
