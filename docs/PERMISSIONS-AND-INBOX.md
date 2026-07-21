# Permissions, Tool Catalog & Inbox — design

**STATUS: design (draft for review).** Discussed with Rohit 2026-06-26/27. Companion to
`PERSONAS.md` (which references this for its tool/permission/inbox machinery). Covers: the
vetted tool catalog, the risk-class taxonomy, user-local risk overrides, permission modes,
the **Unattended** toggle, and the **Inbox** (incl. multi-inbox routing + resume
reconciliation). Decisions log at the bottom.

## The pieces and how they compose

Five layers, each a clean input to one decision:

1. **Tool catalog** — stable `id → capability`, what a persona references.
2. **Risk class** — intrinsic side-effect category of each tool (`read` / `write_local` /
   `exec` / `external`). Replaces today's hardcoded name sets.
3. **Effective-risk override** — *user-local only* reclassification, mainly to relax MCP's
   conservative default. Never carried by a persona.
4. **Permission mode** — *how autonomous*: Plan / Interactive / Custom / Auto (today's
   `permissions.py`). The ceiling.
5. **Unattended toggle** — *where you're reached*: inline composer vs. **Inbox**, and lets the
   agent suspend/resume instead of blocking on an absent human.

The gate is one function: `evaluate(tool, args, mode, attended?)` → allow / deny / route. Risk
class (after override) decides what's *consequential*; mode decides whether consequential
needs you; the Unattended toggle decides *where* "needs you" lands.

## 1 · Tool catalog

Today tools are plain callables assembled by hand in each agent's `build_tools(context)`
(`agents/code.py` imports the per-tool factories from `tools/` — `files`, `git`, `search`,
`shell`, `todo` (plus `directories`, `plan`, `subagent` on main) — and aisuite `files`/`git`
toolkits, and concatenates them). `tools/registry.py` is runtime plumbing (callable → JSON
schema + exec), keyed by `__name__`. There is no ID→capability layer a manifest can reference
— the agent code *is* the catalog.

Introduce `catalog.py`: each existing factory registered as a **capability**.

```
Capability(
  id          = "files",                 # stable; what a persona's `tools:` lists
  name        = "Files",                 # human label (consent screen)
  description = "Read & edit files in the workspace",
  factory     = <context -> [callables]>,# the existing factory, unchanged
  requires    = ["workspace"],           # context deps: workspace | executor
                                         #               | connector:<x> | secret:<y>
  risk        = { "read_file": "read", "write_file": "write_local", ... },
)
```

`build_tools` then **expands a persona's `tools:` list against the catalog given the session
context** instead of hardcoding imports. Code/Cowork's current hardcoded lists become two
manifests (`tools: [files, git, shell, search, todo]`). Mechanical refactor — factories don't
change.

**Grain:** manifests reference **capability groups** (`files`, `shell`), not individual
function names. Per-function gating still happens via risk class. (Read-only-files is handled
by Plan mode, so no separate qualifier needed in v1.)

**Closed set:** the catalog is **platform-owned only**. Third parties get breadth from us
adding vetted tools + from MCP — never by adding catalog entries. That closed set *is* the
"vetted" guarantee.

**Connector-provided tools:** declaring a connector in a persona (`connectors: [pagerduty]`)
**implies its tool bundle** — you don't list them again under `tools:`. A catalog entry can
declare `requires: [connector:pagerduty]`, so at enable time we tell the user "needs PagerDuty
connected" + offer setup.

## 2 · Risk classes (replaces the hardcoded sets)

`permissions.py` currently hardcodes `WRITE_TOOLS = {write_file, replace_in_file, apply_patch,
apply_unified_diff}`, `SHELL_TOOL = "run_shell"`, plus a per-tool `metadata.requires_approval`.
Formalize that into a declared **risk class** per tool:

| Class | Examples | Behavior |
|---|---|---|
| `read` | `grep`, `read_file`, `git_log`, `web_search` | always allowed |
| `write_local` | `write_file`, `apply_patch`, `replace_in_file` | path-scoped + mode-gated |
| `exec` | `run_shell` | highest local risk; mode-gated |
| `external` | `send_message`, HTTP POST, connector writes, `create_automation` | side effects **off the machine** — the unattended Inbox-routing hook |

The engine reads the declared class instead of matching names. This is a strict improvement
even ignoring personas. `external` is the precise hook for "an unattended agent must queue
this to the Inbox."

Optional **floor flag** on a few genuinely irreversible vetted tools: `never_unattended_auto`
— even a broad posture can't auto-fire them without an explicit extra step.

## 3 · Effective-risk overrides (user-local)

`effective_risk(tool) = user_override ?? catalog_default`.

**The inviolable rule: overrides are user-authored and machine-local. A persona/package can
NEVER carry one.** Otherwise a malicious persona downgrades its own tools past the gate and
declares itself trusted — the whole attack. The persona declares *what it wants*; only the
user decides *how much to trust it*.

Primarily for **MCP**, whose tools we can't vet and therefore default to `external`. Match by
**server + tool-name glob, most-specific wins**:

```toml
[mcp.notion]
default = "read"            # I trust this server; treat its tools as read
[mcp.notion.tools]
"create_*" = "external"     # …except writes
"delete_*" = "external"
[mcp.github]
"*" = "external"            # left conservative
```

Both directions allowed; **upgrade** (`read→external`) is always safe and cheap, **downgrade**
is the deliberate act. Same mechanism *can* reclassify vetted built-ins, but those are vetted
so that's "advanced, at your own risk"; MCP is where overrides are the expected path.

**Primary authoring path is the approval UI, not the file.** When an MCP tool surfaces, offer
*"Always allow," "Trust read-class tools from `notion`."* That writes the same local store the
TOML represents. The file is the portable/power-user form.

## 4 · Permission modes (existing, recapped)

`Mode` ∈ Discuss / Plan (both read-only — `READ_ONLY_MODES`; Plan additionally drives toward a
`propose_plan` approval) · Interactive (auto reads, ask on consequential — default) · Custom
(interactive + auto-allow a configured set) · Auto (full access, still path-scoped). These set
the **autonomy ceiling**. Generalize Custom's `auto_allow_tools` to be **risk-class-aware**
("auto-allow all `write_local`"), not just an enumerated list.

We deliberately **dropped** an earlier proposal for a separate per-class "autonomy grant"
policy layer — the Unattended toggle (next) replaces it. Net simpler.

## 5 · Unattended mode

A **per-session toggle** in the composer area. It does **not** change the autonomy ceiling
(mode does) — it changes *where the human is reached* and lets the agent **suspend/resume**:

- Anything that would prompt inline — approval, question, notification — **routes to the
  Inbox** instead.
- The **composer is disabled** with: *"Running unattended in {mode} — questions & approvals go
  to {Inbox}. Toggle off to take back control."*
- Turning it on is a **one-tap confirm** — *"Run unattended in {mode} → {Inbox}?"* — because
  this is the moment a human hands over control; it deserves one beat of friction.

Because Unattended grants no extra power, it's **safe by construction**: Interactive +
Unattended means "approve everything, just from my phone."

**Mode × Unattended:**

| Mode | Unattended behavior |
|---|---|
| Auto | runs fully autonomously; Inbox gets only notifications / "stuck" / "done" |
| Interactive | every consequential action parks in the Inbox; agent suspends until answered |
| **Custom** | **the overnight sweet spot** — auto-allow safe `write_local`/tests, route only `external` (push/deploy/send) to the Inbox |

## 6 · The Inbox

The canonical, cross-session **human-attention queue** (see `PERSONAS.md`). Three item kinds,
each **deep-linking to its originating session**:

- **Approval** — actionable allow/deny with context.
- **Question** — free-text answer the agent needs to continue.
- **Notification** — FYI / completion (links to artifact).

The Inbox is the **store of record**; messaging connectors + a future mobile app are
**transports** of the same items.

### Sidebar indicators are *views* of this queue (not a second list)

The left-panel "needs attention" signals are **the same Inbox items**, surfaced as breadcrumbs
in the sidebar — there is **no separate "Needs attention" tab** (that would just rebuild the
Inbox and immediately drift). The **attention** signal = *an Inbox item is pending for a
session*, rendered as an **amber count that bubbles up three levels**:

- **session row** → a badge on the exact session waiting on you (click → go there, answer inline),
- **persona accordion header** → a **rolled-up count** so a *collapsed* persona still says
  "something inside needs you" without expanding,
- **footer Inbox** → the **total** across everything (the triage-all view).

All three resolve via the state machine below — answering on the session row, in the Inbox, or
over Slack resolves the one item everywhere. Distinct from this is **liveness** (a session is
*working* or *sleeping with a pending wake*): a **count-less** dot only, which **never** bubbles
into the attention count (so idle scheduled agents don't inflate "needs you"). Attention and
liveness are orthogonal, and both are orthogonal to pinning (see `PERSONAS.md`).

### Item state machine (the anti-race contract)

Each item has **one authoritative state**: `pending → resolved`, resolution recorded **once**,
**idempotent, first-responder-wins**. The agent only ever consumes the first resolution; a
second attempt from any surface is a no-op that shows "already answered." Every surface
(in-app, Slack, composer) reflects the resolved state (e.g. the Slack message edits to
"✅ approved by you"). This is what makes multi-surface answering safe.

### Resume reconciliation (surface answers back to the composer)

When a user **toggles Unattended off** (resumes attended), the session must reconcile so there
is a single coherent place going forward and **no double-answer races**:

- **Pending** Inbox items for that session surface **inline in the composer area**, so the user
  answers in one place from now on. (They remain answerable from a mirrored channel too — but
  per the item state machine, answering anywhere resolves the one item.)
- **Answered-while-away** items surface as an inline **recap** — *"While you were away:
  approved deploy to staging, answered 2 questions"* — so the session reads coherently and the
  user sees what was decided.
- The composer re-enables; future prompts come inline again; in-flight items stay answerable
  from either place but resolve once.

## 7 · Multi-inbox routing

An **Inbox = a named queue + delivery binding(s)**:

- **In-app is always present** (the canonical store).
- Optionally **mirrored** to a Slack channel / Telegram chat via the messaging connector.
- **Per-persona default + per-session override.** Ops sessions → `#ops-coworker`; personal
  helper → a Telegram DM; a SWE session → in-app only.

Bindings are **bidirectional**: out = the approval/question; in = the reply/approval,
**correlated by item ID** so a "✅ approve" in Slack resolves the right pending action and
wakes the right suspended agent. This reuses the existing connector gateway (inbound dispatch
+ outbound `send_message` + steering) — Inbox routing is mostly wiring those to a queue with
the item state machine in the middle.

## Relationship to existing code

- `permissions.py` — `Mode` enum + `evaluate()` stay; replace `WRITE_TOOLS`/`SHELL_TOOL`/
  `requires_approval` with declared risk classes read from the catalog; add the `attended?`
  axis (route vs. ask); generalize Custom's `auto_allow_tools` to risk classes.
- `tools/registry.py` — stays runtime plumbing; the new `catalog.py` sits above it (IDs,
  factories, `requires`, risk).
- `connectors/` gateway — already has inbound dispatch + `send_message` + steering; the Inbox
  bindings build on it.
- `automation/` scheduler / `TaskStore` — shared by self-wake (suspend/resume) per
  `PERSONAS.md`.

## Open questions

- Risk-class-aware Custom: config grammar for "auto-allow class X."
- Inbox persistence/dedup, read/unread, stale-item expiry.
- Whether resume-recap is ephemeral (a system note) or a real turn in the transcript.
- Floor list (`never_unattended_auto`) — which vetted tools, if any, at launch.
- Wake budgets / runaway detection — still tabled (see `PERSONAS.md`).

## Decisions log

- **2026-06-26** — Vetted tool catalog: platform-owned, closed set; personas reference
  capability IDs; connectors imply their tools; MCP is *not* in the catalog.
- **2026-06-26** — Risk classes `read`/`write_local`/`exec`/`external` replace hardcoded name
  sets; `external` is the unattended Inbox-routing hook.
- **2026-06-26** — `effective_risk = user_override ?? catalog_default`; overrides **user-local
  only, never carried by a persona**; match by server+glob; approval UI is the primary author
  path; downgrade is the deliberate direction.
- **2026-06-27** — Dropped the separate per-class autonomy-grant layer in favor of the
  Unattended toggle.
- **2026-06-27** — **Unattended** = a per-session toggle that reroutes all agent→user
  interaction to the Inbox and enables suspend/resume; does **not** change the autonomy
  ceiling (mode does); **one-tap confirm** to turn on; composer disabled while on. Custom +
  Unattended is the recommended overnight combo.
- **2026-06-27** — Inbox items have one authoritative state (`pending→resolved`, idempotent,
  first-responder-wins); every surface reflects it.
- **2026-06-27** — **Resume reconciliation**: toggling Unattended off surfaces pending items
  inline in the composer + an inline recap of answered-while-away items — single source of
  truth, no double-answer races.
- **2026-06-27** — Inbox = named queue + delivery binding(s); in-app always + optional
  Slack/Telegram mirror; per-persona default + per-session override; bidirectional,
  item-ID-correlated; reuses the connector gateway.
- **2026-06-27** (UX pass) — Sidebar "needs attention" = **views of the Inbox queue**, not a new
  tab: **attention** (Inbox-pending) bubbles as an amber **count** session→persona-header→footer
  Inbox; **liveness** (working/sleeping) is a separate **count-less** dot that never bubbles.
  Both orthogonal to pinning.
