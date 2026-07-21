# Implementation Ledger — `platform/personas` branch

Running log for the whole `platform/personas` branch, across its workstreams (in order):
**Personas/Permissions/Inbox** (Phases 0–3, ✅ done) → **Messaging refactor** (✅ done) →
**UI Refresh** (in progress — see `UI-REFRESH-KICKOFF.md`).

> **Phase numbers are per-workstream.** "Phase 0–3" below = the *Personas* effort (complete).
> The *UI Refresh* has its **own** Phases 1–5 (`UI-REFRESH-SPEC.md` §9) — when logging UI-Refresh
> progress, write it as **"UI-Refresh Phase N"** to avoid colliding with the Personas phases.

**Purpose.** Single source of truth for *where the implementation stands*, so any session
(or person) can resume without re-deriving context. The design *why* lives in the per-effort docs
(`PERSONAS.md`, `PERMISSIONS-AND-INBOX.md`, `UX-DECISIONS.md`); this file tracks *what's-done /
what's-next*.

**How to use (read this first when resuming):**
1. Check **Current status** below, then the first phase that isn't ✅.
2. Do the next unchecked milestone; make its completion **gate** (the named tests) pass.
3. Update the checkbox + the **Session log** at the bottom, and **commit the ledger** — that
   commit is the handoff to the next session.

Status legend: ⬜ not started · 🟡 in progress · ✅ done. Tests live in `platform/tests/` (pytest).

---

## Current status

- **ALL PHASES (0–3) COMPLETE** — backend, live wiring, and GUI. Local-only, Devika-authored.
  Python: 444 passed (3 pre-existing SDK errors). GUI: full `npm run build` (tsc + vite) passes.
  **First-pass visual review DONE** (Claude, in the running app): all four surfaces verified —
  persona-driven picker (Ops shows), Personas tab (toggles + install), Inbox view, Unattended
  toggle (one-tap confirm → amber on-state). Two correctness fixes applied + verified live:
  toggle reads its persisted state on mount (new `GET /v1/sessions/{id}/unattended`); removed the
  redundant Settings "Surfaces" toggles (now the Personas tab).
- **Phase 4 (UX/IA) — partly built.** DONE + verified live: family-aware Projects, default-first,
  Chat hidden, expand≠switch. DONE in code (not yet screenshot-verified — browser dropped): shared
  top zone (Integrations/Automations above tabs), Pinned band + pin/unpin. Specs in `PERSONAS.md` +
  `PERMISSIONS-AND-INBOX.md`. **Remaining Phase 4 below.**
- **Phase 5 (Messaging ↔ Sessions) DESIGNED, not built** — channel subscription (pub/sub) vs.
  Inbox (request/reply); `ask_user` tool; durable correlation. Spec: `MESSAGING-AND-SESSIONS.md`.

> ## 🧭 RESUME HERE (next session, after compaction) — remaining-work plan
>
> Worktree `/Users/rohit/fleet/ro4d/aisuite-personas`, branch `platform/personas` (local-only,
> Devika-authored, 0 behind origin/main). Test runner: `./.runtests.sh`. Servers may be running:
> backend `127.0.0.1:8765`, vite `localhost:1420` (restart backend after Python changes; vite
> hot-reloads). Sample persona for the install demo: `~/coworker-personas/research-analyst.md`.
>
> ⚠️ **Run the SERVER under the aisuite venv, not the agent-platform (test-runner) venv.** The
> agent-platform venv lacks the `anthropic`/`google-genai` SDKs, so a server started under it
> errors *every* turn with `ModuleNotFoundError: No module named 'anthropic'` (the default model is
> `anthropic:claude-opus-4-8`). Correct launch:
> `PYTHONPATH="…/aisuite-personas/platform:…/aisuite-personas" …/aisuite/platform/.venv/bin/python
> -m coworker.server.run --port 8765`. (Tests still use `./.runtests.sh` / the agent-platform venv.)
>
> **Ordered plan:**
> 1. ✅ **Phase 4 (frontend)** — attention/liveness badges, family-aware App gating, Search→top,
>    Inbox→session link (answer-in-context B). Committed `d0b64d6`. Dots + Inbox badges seen live by
>    Rohit. **Pending visual:** the Inbox→session link + inline answer card — a real review session
>    is left suspended for this (see hands-on testing below).
> 2. **Phase 5 messaging (per `MESSAGING-AND-SESSIONS.md`):**
>    - ✅ **`ask_user` tool** (commits `181260b`, `238b0c4`) — engine-intercepted like
>      `request_directory`. **Mode-aware routing (the key contract):** Unattended → KIND_QUESTION
>      Inbox item + suspend on `inbox.wait` (`manager.inbox_question_asker`, also the default for
>      background/self-wake runs); **attended → a live `question_requested` event answered inline
>      from the composer (`question_response`), NO Inbox item** — the Inbox is for Unattended only.
>      Question + optional quick-reply `options` + free-text escape + `multi` (mirrors Claude Code's
>      AskUserQuestion); `InboxItemCard` renders option chips for both the inline live prompt and the
>      Inbox. Tested e2e both ways (attended → live event, no Inbox; unattended → Inbox, no event):
>      PASS. **Not yet done:** durable `(session_id, tool_call_id)` capture (still live-only).
>    - ✅ **Unified interactive prompts on parked Inbox items** (commit `1e97e0f`). All four prompts
>      (approval / question / directory / plan) are parked items awaited via `inbox.wait` (the
>      per-connection asyncio queues are gone), so they **survive a dropped socket** (redelivered on
>      reconnect) and resolve from any surface. **`visibility` decides where a prompt shows, not how
>      it works:** attended → inline only (the cross-session Inbox list filters `inline` out, so the
>      **Inbox = explicit Unattended only**); unattended → cross-session Inbox + answer-in-context.
>      Backend: `InboxItem` gains `visibility`+`data`+`directory`/`plan` kinds; live WS responses
>      resolve the session's single pending item, REST resolves by id; approver takes both
>      vocabularies. Frontend: `InboxItemCard` renders directory/plan; live cards suppressed when
>      Unattended (App tracks it via ref + toggle `onChange`). Verified e2e (approval + ask_user,
>      both modes). **Boundaries (honest):** (1) *Layer 2* — a turn **started by** a socket that
>      drops mid-stream still dies on the next send (its event stream is bound to that socket);
>      re-attaching a live turn to a new socket = the deferred durable-resume / event-routing work.
>      Server-*triggered* turns (self-wake, schedule, message) aren't socket-bound, so they park+resume
>      fine. (2) live WS resolve uses "the session's one pending item" (safe: agent blocks on one at a
>      time; a human can't out-race item creation). (3) directory grant from the Inbox/reconnect has
>      no folder picker yet (uses the suggested path).
>    - ✅ **Channel subscription (inbound)** (commit `66208e0`). `SubscriptionStore` (persisted
>      `(session_id, channel)`, permanent until unsubscribe/delete); agent tools `subscribe_channel`
>      / `unsubscribe_channel` / `list_subscriptions` / `get_channel_messages` (ring buffer); agent
>      bootstraps via `ask_user`, parses Slack `<#id|name>`. Gateway dispatch: token → Inbox; channel
>      msg → buffer + fan out to subscribers via `manager.deliver_to_session` (busy→steer/idle→turn,
>      shared with self-wake); DM → super-agent. Loop prevention free (adapter drops bot-self). v1
>      filter = the subscription itself; mention/thread filtering deferred. **Sessions are durable
>      (never end except explicit delete)** — documented in `MESSAGING-AND-SESSIONS.md`. Tested
>      (store/parsing/buffer/tools/dispatch fan-out). **Not yet:** GUI to view/manage subscriptions;
>      `@bot` mention surfacing in `MessageEvent`; channel name→id picker; live Slack e2e (needs a
>      connected workspace — logic is unit-tested).
>    - ✅ **Interactive Inbox prompts over Slack** (commit `9d8798a`). Mirrored items render as Block
>      Kit cards with **buttons** (approve/deny, ask_user options); the item id rides in each button
>      value, so a click resolves the exact item (no `[ocw:id]`-reply fragility). Socket-mode action
>      callback → `manager._on_interaction` → `inbox.resolve` → swaps buttons for the outcome.
>      **Free text isn't offered over messaging — open the app** (token = legacy fallback).
>      Provider-agnostic `Button`; v1 = single-select, Slack. Unit-tested. **Not yet:** Telegram
>      inline keyboards, plan/directory buttons, live Slack click round-trip (needs Interactivity
>      enabled on a connected workspace).
> 3. ✅ **Hands-on testing (done 2026-06-27, via WS+REST against the running server, real model):**
>    (A) self-wake — `sleep_for` → scheduler `resume_due_wakes` resumed the session in ~16s: PASS.
>    (B) Unattended — a `write_file` approval parked in the Inbox (real `attention=1`, liveness
>    `working`), resolving it released the suspended agent and the file was written: PASS. **Not yet
>    tested:** the free-text `ask_user`/KIND_QUESTION path (doesn't exist until Phase 5 — only the
>    KIND_APPROVAL path is wired). A **review session `handson-unatt-6a3fed71`** is left suspended
>    with a real pending approval so Rohit can see the badge + Inbox→session link + inline card in
>    the GUI (resolve/dismiss it from the Inbox to clean up).
>    - ✅ **Durable resume** (commit `08f3bc4`). A prompt (approval/question/directory/plan)
>      pending when the process dies now continues its turn on answer, instead of orphaning. Inbox
>      items are idempotent by `(session_id, tool_call_id)` (id persisted) + the thread is saved when
>      a prompt suspends; `engine.resume()` re-drives ONLY the trailing assistant message's
>      *unanswered* tool-calls — callbacks find the resolved item (no re-prompt), approvals
>      re-execute via the normal authorize→execute path, answered calls are skipped (no double-exec).
>      `manager.resolve_inbox` (REST + Slack button) durably resumes when the agent isn't live;
>      Inbox-based default approver/directory/plan callbacks added so a rebuilt no-socket engine can
>      resolve all four. Tested: question (inject answer) + approval (re-execute tool) survive a
>      simulated restart. **Boundaries:** the legacy token-text-reply path (`_resolve_inbox_reply`,
>      sync) does NOT trigger durable resume — buttons + REST do; the same-process socket-drop case
>      is still the Layer-2 limitation (orphaned task completes server-side, events lost).
> 4. **Messaging GUI** — ✅ **view-only** (`4b0e828`) + ✅ **manage** (`<this>`). Global "Channel
>    subscriptions" table in Integrations (session → inbound channel + outbound routing + collision
>    warning) with per-row unsubscribe + an add form (session select + ChannelPicker + Subscribe);
>    per-session plug chip in the composer head opens a popover to add/remove this session's
>    channels. `ChannelPicker` = datalist of recently-seen channels (`GET /v1/channels/recent`) +
>    free typing (`slack:C…` or `#mention`). REST: `POST /v1/subscriptions`, `/v1/subscriptions/
>    remove`. Tested. **Browser down on my end → Rohit verifies visually.** (Recent list is empty
>    until Slack messages arrive — type `slack:Ctest` to exercise the UI.)
> 5. **deepseek-chat picker bug — TABLED** (Rohit, 2026-06-28). The quick filter patch was built +
>    reverted; the real fix is a provider-model redesign (first-class OpenAI-compat providers) — see
>    "Known bug" below. Current behavior kept (harmless cosmetic leak).
>
> Design items parked (not now): per-coworker integrations (Connect-global vs Grant-per-persona);
> white-label build tooling; wake budgets/runaway detection.
- **Marketplace (decided with Rohit 2026-06-27):** NOT a hosted service. Personas load from a
  local dir or a GitHub repo and are **snapshotted into our managed area** at install (done).
  Built-in Code/Ops stay bundled/immutable. Updating a persona can change live sessions' prompt/
  tools — accepted, noted, deferred. (White-label build tooling: deferred.)
- **Live wiring progress:**
  1. ✅ Approver-swap — the `/ws/session` approver routes to the Inbox + suspends when the
     session is Unattended (reuses the tested inbox machinery; resolved via `/v1/inbox/.../resolve`).
  2. ✅ Scheduler/engine **resume** — no engine surgery needed (Rohit's framing): a self-wake
     tool records a wake + the agent ends its turn; the scheduler tick (`extra_tick`) calls
     `resume_due_wakes()`, which delivers the wake message **busy → `queue_steering` into the
     live turn; idle → run a fresh background turn** (the super-agent's proven pattern). Per-session
     in-flight flag set by `/ws/session`. Self-wake tools registered for knowledge personas.
  3. ✅ Gateway inbound — `Gateway.reply_resolver` consumes inbound messages carrying an
     `[ocw:<id>]` token (resolve the item + release the suspended agent) instead of routing them
     as a new turn; the unattended approver mirrors the approval out to the bound Slack/Telegram
     channel with the token embedded. Bidirectional loop closed.
  4. ✅ **GUI pass** (build-verified; visual review pending): Personas tab in Manage modal
     (enable/surface/set-default + install from dir/GitHub with consent summary); the new-session
     **picker is now driven by surfaced personas** (Ops appears), static set as fallback; an
     **Inbox view** (approve/answer/dismiss, polled) wired as a sidebar surface; a per-session
     **Unattended toggle** in the composer head (one-tap confirm). `api.ts` extended. Full
     `npm run build` passes. *Deferred polish for review:* composer-disable while unattended;
     inbox routing config UI; reactive persona refresh after enable; inbox unread badge.
- **Base:** branch `platform/personas` off `origin/main` (local-only, Devika-authored).

**Dev env (how to run tests):** no venv in this worktree — reuse the `agent-platform` venv
interpreter with this worktree on `PYTHONPATH` (helper: `./.runtests.sh <pytest args>`, which
runs `…/agent-platform/platform/.venv/bin/python -m pytest` with
`PYTHONPATH=<root>/platform:<root>`). That venv lacks the `anthropic`/`google-genai` SDKs and
`black`, so 3 provider tests error on import (pre-existing, unrelated) and formatting is by
hand. Everything else runs green.

---

## Phase 0 — Foundation: tool catalog + risk classes

*Goal: a declarative `id → capability` catalog + risk-class-driven permissions, with **no
behavior change** for existing agents. Both personas and permissions sit on this.*

- ✅ `risk.py` — `RiskClass` (read/write_local/exec/external) + `classify()` with a user-local
  override seam; `permissions.py` reads it instead of `WRITE_TOOLS`/`SHELL_TOOL`/`requires_approval`
  (those re-exported for back-compat).
- ✅ `catalog.py` — capabilities (`code_files`, `files`, `git`, `search`, `shell`, `todo`) with
  `requires` + `risk`; `expand(ids, ctx)` skips capabilities whose context is absent.
- ✅ `agents/code.py` + `agents/cowork.py` build via `expand(...)` (hand-written factories gone).
- ⬜ `never_unattended_auto` floor flag — deferred to Phase 2 (where it's consumed).

**Completion gate (tests): ✅ 41 passed.**
- `test_catalog.py` — expand reproduces the Code & Cowork toolsets exactly; requirement
  skipping; the single-root vs multi-root file-capability distinction preserved.
- `test_permissions_risk.py` — `classify` mapping (incl. `external` + override precedence) and
  engine decisions across all 5 modes.
- Existing `test_tools_permissions.py` + full suite green (348 passed; 3 pre-existing SDK-import
  errors unrelated).

**Note:** `read_file_lines` stays Cowork-only (Code folds it into the windowed reader) — the
two file capabilities (`code_files` single-root numbered vs `files` multi-root) preserve each
surface's exact toolset.

## Phase 1 — Personas core

*Goal: personas as data; Code/Cowork become manifests; a session is born from exactly one
persona; lifecycle toggles in settings.*

- ✅ `personas/manifest.py` — `PersonaManifest` + strict parser/validator (YAML frontmatter +
  markdown body); `to_agent()` → catalog-expanded Agent with traits. (pyyaml added to deps.)
- ✅ `personas/registry.py` — `PersonaRegistry`: builder-backed core (Code/Chat/Cowork keep exact
  prompts) + markdown built-ins; lifecycle (`enabled`/`surfaced`/`default`) persisted to
  `<data>/personas.json`; module singleton installed by the manager. `agents/registry.get_agent`
  delegates here (lazy import; MyHelper still direct).
- ✅ Ops persona shipped as a markdown manifest (`personas/builtin/ops.md`) — dogfoods the parser.
- ✅ Agent traits (`family`/`messaging`/`connectors`) replace the `agent.name == …` branching in
  `build_engine` + `manager.get_engine` (so Ops behaves like Cowork: orphan scratch, connectors,
  scheduling, request_directory). Session binding/pin/rename ride existing `SessionRecord` fields.
- ✅ REST: `GET /v1/personas`, `POST /v1/personas/{id}` (enable/surface/default); `/v1/agents`
  now returns the surfaced persona picker.
- ⬜ **GUI deferred** to a visual-review pass (persona picker + Personas settings tab) — backend
  + endpoints ready; React UI to be built with Rohit per the visual-review rule.

**Completion gate (tests): ✅ 28 passed.**
- `test_persona_manifest.py` — valid parse + 8 rejection cases (no/again frontmatter, missing id,
  empty body, unknown tool, bad family/workspace/mode); `to_agent` traits + tools.
- `test_persona_registry.py` — lifecycle + set-default; Cowork-disabled → fallback; surface toggle
  filters the picker but keeps the persona resolvable; state persists.
- `test_builtin_personas.py` — Code/Cowork personas resolve to the **exact** builder toolsets;
  Ops composes the knowledge toolset.
- `test_session_persona.py` — persona recorded on the session + stable across reload; pin + rename
  persist.
- Full suite: 404 passed (1 prior test updated: unknown-agent fallback is now the default persona
  Cowork, not Code); same 3 pre-existing SDK-import errors.

## Phase 2 — Distribution + autonomy + in-app Inbox

*Goal: third-party personas (local dir / git URL) with consent; effective-risk overrides;
Unattended + suspend/resume + self-wake; the in-app Inbox.*

- ✅ `personas/loading.py` + registry `install_from_dir`/`install_from_git` (injectable clone);
  `consent_summary`; installed sources persist + reload; third-party personas land
  disabled+unsurfaced pending consent. REST `POST /v1/personas/install`.
- ✅ `overrides.py` — user-local `RiskOverrideStore` (glob, most-specific wins), wired into
  `build_engine`'s PermissionEngine. Never written by persona loading (no-self-grant).
- ✅ `unattended.py` — per-session toggle registry (persisted). REST
  `POST /v1/sessions/{id}/unattended`. (Composer-disable + one-tap confirm = GUI, deferred.)
- ✅ `inbox.py` — `InboxStore` (3 kinds), `pending→resolved` state machine (idempotent,
  first-responder-wins), `reconcile_on_resume`, async `inbox_approver`. REST `GET /v1/inbox`,
  `POST /v1/inbox/{id}/resolve`, `GET /v1/inbox/reconcile`.
- ✅ `selfwake.py` — `WakeStore` (timer + on-completion) + `due()`/`complete_job()` + the
  `sleep_for`/`sleep_until`/`wake_on` tools.
- ⬜ Live wiring (carried forward): approver-swap when Unattended; scheduler resuming due wakes; GUI.

**Completion gate (tests): ✅ 25 passed.**
- `test_persona_loading.py` (5) — dir + git-URL load; consent summary; disabled-pending-consent;
  persistence; invalid manifest fails loud.
- `test_risk_overrides.py` (6) — most-specific glob wins; relaxes MCP in classify + engine;
  persistence; tighten direction; **no-self-grant** (manifest override/elevated-mode ignored).
- `test_inbox.py` (7) — kinds/filter; idempotent first-responder-wins; persistence; reconcile;
  approver allow/deny.
- `test_unattended.py` (2) — toggle + persist; unattended routes approvals to the Inbox.
- `test_self_wake.py` (5) — timer due-after-fire; completion due-after-complete; mark_fired;
  persistence; tools.

## Phase 3 — Channels + events + marketplace

*Goal: Inbox mirrored to Slack/Telegram (bidirectional); on-event wake; registry/marketplace;
white-label builds.*

- ✅ **Marketplace = dir/GitHub load + snapshot** (see decision above): `install_from_dir` now
  copies manifests into `<data>/personas-installed/<id>/` (stable, source-independent);
  `install_from_git` clones then snapshots. Built-ins bundled/immutable.
- ✅ `inbox_routing.py` — named inboxes + bindings (in-app / Slack / Telegram); `route_for`
  (session override > persona default > default); `deliver` (embeds `[ocw:<id>]`); 
  `resolve_from_reply` (correlate inbound reply → resolve). REST `GET /v1/inbox/routing`,
  `POST /v1/inbox/routing/binding`.
- ✅ On-event wake: `WakeStore.add_event` / `fire_event` + the `wake_on_event` tool.
- ⬜ White-label build tooling — deferred.
- ⬜ **Live wiring + GUI** — see Current status (the remaining work).

**Completion gate (tests): ✅ 13 passed (+ snapshot test).**
- `test_inbox_routing.py` (7) — route precedence; persisted bindings; deliver embeds item id;
  in-app-only delivers nothing; inbound reply resolves the right item (approve / free-text);
  tokenless reply ignored.
- `test_self_wake.py` — on-event due-after-fire + the `wake_on_event` tool.
- `test_persona_loading.py` — snapshot survives source deletion.
- Full suite: **438 passed**, 1 skipped, same 3 pre-existing SDK-import errors.

## Phase 4 — UX / IA polish (built 2026-06-27)

*Goal: make the persona model feel coherent in the GUI. All frontend (+ one backend field); design
settled in `PERSONAS.md` (Family, Sidebar IA) + `PERMISSIONS-AND-INBOX.md` (attention/liveness).*

- ✅ **Family-aware frontend** — Sidebar Projects/grouping keys off `familyOf(id) === "code"`;
  App's `needsWorkspace`/`gatesWorkspace` now read the persona's `needs_workspace` / `family ===
  "code"` (id-based fallbacks only until personas load). A code-family *third-party* persona now
  gates a folder like Code; a knowledge persona starts orphan like Cowork.
- ✅ **Sidebar default-first + Chat hidden** — registry: Cowork registered first; Chat
  `default_surfaced=False`. Verified live: picker = OpenCoworker, Code, Ops; no Chat.
- ✅ **Expand ≠ switch** — header toggles the accordion only (`browseKey = openKey`); chat area
  changes on select/New. Verified live.
- ✅ **Attention badges** — `list_sessions` now returns `attention` (count of pending Inbox items
  for the session). Amber **count** renders on the session row → persona header → footer Inbox
  (views of the one Inbox queue; no new tab). Verified with seeded data; honest path needs an
  Unattended session (items only reach the Inbox when Unattended — attended sessions answer inline).
- ✅ **Liveness dot** — `list_sessions` returns `liveness` (`working` = in-flight turn via
  `is_running`, `sleeping` = a self-wake pending via `wakes.pending`, else `idle`). Count-less dot
  (green pulse / grey); never bubbles into the attention count.
- ✅ **Pinned band** — cross-persona band above the accordions, manual pins only; pin/unpin on
  every session row.
- ✅ **Shared top zone** — Search + Integrations + Automations in `.shared-nav` above the persona
  accordions (Search moved to top, first).
- ✅ **Inbox → session link (answer-in-context, "B")** — each Inbox item shows a clickable chip
  (persona icon + session title) that opens its originating session; and the session view renders
  its pending Inbox item inline above the composer (`InboxItemCard`, shared with `InboxView`), so
  the blocking question/approval is answerable in context — resolving the same item id (first
  responder wins). Decided *not* to add the reverse (clickable badge → filtered Inbox) this pass.
- ⬜ Carried deferred polish: **composer-disable while Unattended** + **reconciliation on turn-off**
  (real behaviors Rohit reaffirmed: Inbox items exist only for Unattended sessions; turning
  Unattended off surfaces pending items inline before the session proceeds); inbox routing config
  UI; reactive persona refresh after enable; clickable attention badge → filtered Inbox.

**Completion gate:** typecheck + `npm run build` green ✅. Visual: dots + Inbox badges seen live by
Rohit; the Inbox→session link + inline answer-in-context to be shown next review with a *real*
Unattended session (not synthetic seeding).

### Design item (not Phase 4) — per-coworker integrations

Separate **Connect** (authenticate a connector — *global*, the one Integrations page common to all)
from **Grant** (which connected integrations a coworker may use — *per-persona*). Evolve the
manifest `connectors` from a boolean to a **list of connector ids** the persona wants; a coworker
sees only connectors it declares AND that are connected. Surface a user override in the **Personas
tab** (per-persona connector toggles, with a "Connect" deep-link to the global page). Design later.

### Known bug — phantom `deepseek-chat` in the model picker (TABLED 2026-06-28; fix architecturally)

`deepseek-chat` shows in the composer model selector though there's no DeepSeek provider. Root: a
**bare** `deepseek-chat` in `prefs.json` (added under the OpenAI provider — bare = OpenAI; almost
certainly a pre-#340 default), and `_model_provider` defaults **any** bare name to `openai`, which
is configured → the filter keeps it.

**The deeper cause (why we tabled the quick fix):** OpenAI-compatible services (DeepSeek, OpenRouter,
vLLM, Azure) are modeled as **"OpenAI provider + a custom `base_url`"**, so their models live in the
OpenAI bucket and are stored **bare**. A bare name is therefore **ambiguous** — junk on stock
`api.openai.com`, but a *valid* model on a custom endpoint. You can't tell from the name.

A targeted patch was built + reverted (Rohit's call 2026-06-28): hide a bare name on stock OpenAI
unless it matches an OpenAI naming family (`gpt-`/`o…`/KNOWN_MODELS), allow anything when a custom
`base_url` is set (it correctly used base_url as the stock-vs-compat discriminator, hide-not-delete).
Correct for today's design, but a heuristic on an ambiguous model.

**Decided fix (later, comprehensive):** make OpenAI-compatible services **first-class providers** —
distinct descriptors (DeepSeek / OpenRouter / …) each with its own key + base_url + model list — so a
model is **explicitly** `provider:model` and only shows when that provider is configured. Zero
bare-name ambiguity. Work: new descriptors over the openai-compat build, GUI panes, and a migration
for existing bare names. Until then the phantom is a harmless cosmetic leak; current behavior kept.

---

## Session log

Append one entry per working session (newest at top): date · who/branch · what changed · tests.

- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh — "New project" regression fix + Codex-style Projects.**
  Rohit flagged that the sidebar "New project" line did nothing when clicked, and shared a Codex screenshot of
  its Projects panel. **(1) Regression** (introduced with `isProjectScoped`): the button only set the gate flag,
  but the gate renders behind `surface==="session" && gatesWorkspace(activeAgent)` — so it silently no-op'd
  whenever the *active* session wasn't project-scoped (e.g. browsing Code/Ops from a Cowork session). Fix:
  `onNewProject(browseKey)` → `newProject(persona)` switches to that persona, starts a fresh session, and opens
  the gate in **create** mode. Verified live: `+` from a Cowork session switched to Ops and showed the "New
  project" create gate. **(2) Codex-style Projects (Option A, agreed with Rohit — keep persona-first, polish the
  per-persona Projects sub-section):** "New project" moved from a list row to a **`+` in the Projects header**;
  each folder is now **collapsible** (active project open by default, else the most-recent); session rows carry a
  right-aligned **compact age** (`compactAge`: now/5m/6h/3d/2w/4mo/2y); folders truncate to **5** with a
  **"Show more"** disclosure; search expands matching folders and hides the rest. `tsc`+`build` clean, vitest
  20/20. Commits `60f2d44` (regression), `2d016c5` (Projects). **Note:** the lone existing Ops folder shows a
  scratch-dir hash (`4cfb0fb3-46a`) — pre-change orphan data; new projects get the real folder name.
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh visual-parity — post-review fixes (Rohit's eyeball).**
  After Rohit reviewed the Tailwind port: (1) **Sources bar was invisible** — it rendered as a sibling before
  the workspace, so it sat at y=0 behind the `position:absolute` glass topbar; moved it inside the chat column
  (which already pads to clear the topbar) as a fixed sub-header. Now SOURCES + the connector avatar stack show
  under the title and open the Session-connections drawer. (2) **Connector cards confirmed working** — drove a
  live FakeSlack message into a subscribed session; it rendered as a brand-tinted card with resolved names
  (`ocw-test` / `Alex Rivera` via the real users.info/conversations.info). The "legacy-looking" messages were
  pre-Phase-2 data (no `source`); new ones card correctly. (3) **Markdown restyle** — visible list markers +
  real item/heading spacing (lists were run-together), zebra/tinted tables, refined inline code. (4) **Dropped
  the redundant per-persona "New session"** (top split button covers it; mock omits it). (5) Fixed the persona/
  drawer icon rendering a raw logo-id (`cowork`) instead of a glyph. `tsc`+`build` clean, vitest 20/20. Commits
  `776737a`, `abeaba0` (+ the surface ports). **Note:** a demo connector message + a denied turn were left in
  the local `hi` session by the live card test — delete if unwanted. The `_say is not a valid argument` Slack
  log is benign (message delivered, names resolved).
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh visual-parity pass — port the GUI to the mock (Tailwind).**
  Rohit's feedback after the phases landed: functional but "a far cry from the mocks." Root cause: the app and
  `ui-mocks/redesign.html` share the exact CSS tokens, but the mock is Tailwind and the app was hand-CSS, so
  per-component polish was never ported. **Added Tailwind v3** (config mirrors the mock; color tokens wrapped in
  `color-mix` so the mock's `/NN` opacity utilities work; imported before styles.css so un-ported surfaces keep
  their CSS). Then ported all four surfaces to the mock markup, surface by surface, each screenshot-verified in
  the running app: **session view** (topbar pills, sources bar, brand-tinted connector cards, StepGroup,
  composer card, dark bubbles; dropped the dotted main bg) · **sidebar** (wordmark + layout toggle, split
  button + persona menu, pinned/recent + grouped cards, bottom nav, footer) · **persona page + sources drawer**
  (identity/enable, recommends with connect state, default-connection toggles; logo-id icons now render a glyph)
  · **Integrations** (Connectors/Messaging/Activity/MCP sub-nav + connector-card grid with real brand badges).
  Behaviour unchanged throughout; `tsc`+`build` clean, vitest **20 passed** at every step. 6 commits, local-only,
  Devika-authored. **Not visually verified (code-complete + test-covered):** the live connector card (needs a
  real connector message), the Sources drawer (needs opening), the Ops persona's recommends sections (needs an
  Ops session to navigate to). **Dev note:** the tailwind.config change needs a dev-server restart — a stale
  vite won't show it (review on a freshly-started dev server).
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh Phase 5 — frontend polish + the e2e merge gate.**
  Frontend (§7): Integrations restructured into a left sub-nav (Connectors · Messaging routing · Activity ·
  MCP); a dual sidebar (flat ↔ grouped-by-persona, persisted via a new `nav_layout` pref, grouped cards carry a
  gear → PersonaView); a New-session split button (primary = last persona, ▾ = enabled personas + "Manage
  personas…"); and a `StepGroup` that collapses tool/approval items into "N actions · M approvals ✓".
  `ConnectorBadge` (real brand color/logo) now also renders on the connector rows. Backend: `nav_layout` pref
  (`GET /v1/settings` + `POST /v1/settings/nav-layout`). **Product bug found + fixed by the e2e:** the async
  `SlackAdapter.send`/`send_interactive` wrapped the **blocking** httpx senders and were awaited directly on the
  server loop (mirror/interaction paths) — freezing all sessions/sockets for the Slack round-trip; now offloaded
  via `asyncio.to_thread` (matches the engine's send path). **The merge gate** `test_ui_refresh_e2e.py` drives
  the WHOLE refresh against FakeSlack with the **real** SlackAdapter/slack_bolt stack: connect → channel message
  becomes a structured connector card with names resolved via real `users.info`/`conversations.info` (provider
  gets framed text, no `source`) → Unattended approval mirrored as a real Block Kit card → inject Approve →
  durable resume + reply posts back → mute drops delivery (still buffers) → attention == unconnected recommends.
  Tests: e2e + nav_layout → Python **500 passed** (3 pre-existing SDK errors); vitest **20 passed**; `tsc`+`build`
  clean. State-dir isolated (real secrets hash byte-identical across a full-suite run). Local-only, Devika-authored.
  **UI-Refresh Phases 1–5 + FakeSlack COMPLETE.** Remaining: Rohit's visual review of the GUI; deferred polish
  (Integrations 2-col card grid; connector brand color on the transcript `ConnectorMessageCard`; session `detail`
  channel names need the adapter cache); push (Rohit).
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh Phase 4 — persona + session connection surfaces.**
  Backend (5 endpoints): `GET /v1/personas/{id}` (identity + tools + recommended_models + permission mode +
  workspace + `recommends` with `connected` annotated + `default_connections`); `POST /v1/personas/{id}/
  connections` (set persona default); `POST /v1/personas/{id}/enable` (delegates to the registry);
  `GET /v1/sessions/{id}/connections` (effective `connected` w/ a detail string + `recommended` not-yet-connected
  + `attention` count); `POST /v1/sessions/{id}/connections` (session override, `clear` to inherit). Frontend:
  `PersonaView` (identity + Enable toggle + capabilities + recommends + "new sessions get by default" toggles),
  `SourcesBar` (avatar stack + ⚠ N) + `SourcesDrawer` (per-session connection toggles), a shared `Toggle`, and
  `connectors/visuals.ts` threading **real brand_color/logo** from `/v1/connectors` into these surfaces (closes
  the Phase-2 neutral-gray follow-up for the connection surfaces; the transcript `ConnectorMessageCard` is still
  neutral — its caller doesn't thread a color yet). SourcesBar mounted under the session topbar; PersonaView
  reachable via a topbar "About this persona" button + the drawer link (grouped-nav gear + New-session "Manage
  personas…" entry = Phase 5). **Design call (per Phase-3 review):** persona `default_connections` lists the
  recommended defaults (spec-literal); the *session* `connected` list shows ALL effective connectors (honest
  where "why is this on" matters). Tests: `test_persona_connections.py` (5, isolated via `COWORKER_STATE_DIR`)
  → Python **498 passed** (3 pre-existing SDK errors); vitest **17 passed**; `tsc`+`build` clean.
  **Test-hygiene fix (separate commit):** isolated `test_connections.py` (Phase 3) — it built a real
  `SecretStore` against the global state dir and wrote a fake `github:default` into the developer's real
  `~/.config/coworker/secrets.json`. Now pinned to a tmp `COWORKER_STATE_DIR` (verified: global secrets hash
  unchanged across a run). **Known/flagged (pre-existing, needs Rohit):** other tests (`test_attachments`,
  `test_mcp`, historically `test_connectors`) still write to the real state dir; a fake `github:default`
  (`ghp_test`) currently sits in the real secrets store (deletion was correctly blocked — pending Rohit's call).
  Deferred: session `detail` shows chat ids (names need the live adapter cache); `recommended` excludes mcp.
  **Next: Phase 5 (frontend polish + e2e gate).**
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh Phase 3 — connection hierarchy (load-bearing data model).**
  New `connections.py`: `PersonaConnectionStore` (per-persona default on/off, seeded from manifest `recommends`)
  + `SessionConnectionStore` (per-session overrides) + a pure `effective()` resolver — **connected AND
  (session-override if present, else persona-default, else inherit-on)**. Manager owns both stores +
  `effective_connectors(session)`; runtime gating in two places: connector **tools** (a `connector_filter`
  threaded into `build_engine`, applied in BOTH `get_engine` and `_build_task_engine`) and **inbound delivery**
  (`_dispatch_inbound` channel + DM paths skip a muted connector but still buffer). `delete_session` drops the
  session's overrides. **Adversarially reviewed before commit** — the resolver was proven correct; the review
  caught + we fixed: (1) MAJOR seed-staleness — a `tier:core` connector seeds **on** regardless of current
  connectedness (effective()'s connected-gate is the single source of truth), so it self-lights when later
  connected instead of being frozen off (intentional deviation from §4.2's literal wording, documented in
  code); (2) unified the inbound gate onto `effective_connectors` (was a seed/no-seed asymmetry); (3) the
  scheduled-task engine builder now also applies the filter. Tests: `test_connections.py` (8, incl.
  connect-after-seed self-lighting + the muted-not-delivered/tools-absent/DM-muted gates) → Python **493
  passed** (3 pre-existing SDK errors). Local-only, Devika-authored.
  **Phase 4 design note (decided, to implement in §5):** since unrecommended-but-connected connectors inherit
  *on*, the persona-detail `default_connections` will enumerate **all connected connectors** with their
  effective default (keep inherit-on, make the UI honest) rather than special-casing built-in personas.
  **Next: Phase 4 (persona + session connection surfaces).**
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh Phase 2 — structured connector messages.**
  Connector inbound messages now carry a display-only `source` sidecar so the GUI renders a rich card while
  the model still gets the framed text. Backend: `MessageSource` dataclass (§3.1); `engine.run(..., source=)`
  stores it on the user message and emits it in `TURN_START`; **`_outbound_messages` strips `source`
  unconditionally** (the sole provider feed — proven by a no-context strip test); `deliver_to_session` +
  `_dispatch_inbound` build+thread the source on both channel and DM paths (steering path too); persisted
  verbatim (store json-dumps each message) so `GET /messages` + WS `turn_start` surface it for free. Frontend:
  `ConnectorMessageCard` (brand-tinted header/edge, names with id-on-hover swap, relative time); extracted
  `itemsFromMessages.ts` (testable) maps `source.connector` → a `connector` item; `Transcript` renders it.
  Tests: `test_message_source.py` (5, incl. persisted-and-stripped) → Python **485 passed** (3 pre-existing
  SDK errors); vitest **11 passed**; `tsc`+`build` clean. Updated 3 existing tests for the new
  `deliver_to_session`/`_outbound_messages` contract (signatures + `is`→`==`). Local-only, Devika-authored.
  **Deferred (small follow-up, tracked for Phase 4):** connector cards render in neutral gray — `source`
  carries only the connector id, not `brand_color`; a `brandColor` prop hook exists to thread the real color
  (from `/v1/connectors`, which Phase 1 added) once the session view loads connector data. **Next: Phase 3
  (connection hierarchy — the load-bearing data-model change).**
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh Phase 1 — connector registry metadata + contract.**
  Backend: `ConnectorDescriptor` gains `brand_color`+`logo`; `connector_list` surfaces them; placeholder
  `available:false` descriptors for the not-yet-shipped recommended connectors (datadog/salesforce/pagerduty
  — github+hubspot already ship here, so they got brand metadata, not duplicate placeholders); `SlackAdapter`
  gains cached `_channel_name` (conversations.info) + public `resolve_user_name`/`resolve_channel_name`
  wrappers, and populates a new `SessionSource.chat_name` in `_on_message` (mirrors `user_name`). Frontend:
  `src/connectors/registry.tsx` (logo-id → inline SVG + `FALLBACK`) and `ConnectorIcon`/`ConnectorBadge`
  (brand color from API, fallback plug on unknown id); `Connector` interface gains `brand_color`/`logo`. Stood
  up a minimal **vitest + @testing-library/react** harness (separate `vitest.config.ts`, devDeps only — reused
  by later frontend phases). Tests: `test_connector_registry.py` + `test_slack_resolve_channel_name` (Python
  **480 passed**, 3 pre-existing SDK errors); vitest 4/4; `tsc --noEmit` + `npm run build` clean. Local-only,
  Devika-authored, not pushed. **Next: Phase 2 (structured connector messages).**
- **2026-06-29** · Devika / `platform/personas` · **UI-Refresh: FakeSlack harness (foundation, build-first).**
  Built `coworker/testing/fake_slack/` — an in-process Slack test double on Starlette+uvicorn (ephemeral
  port): Web API (`auth.test`/`apps.connections.open`/`users.info`/`conversations.info`/`chat.postMessage`/
  `chat.update`), Socket Mode WS speaking real-`slack_bolt`-shaped `events_api`+`block_actions` envelopes,
  a `/control/*` HTTP API + programmatic `FakeSlack` object, and a standalone `python -m
  coworker.testing.fake_slack` runner. One production change: a `SLACK_API_URL` base-URL override on the
  Slack adapter (bolt `AsyncWebClient`) **and** both httpx senders (default = real Slack). `aiohttp>=3.9`
  declared in the `messaging` extra (so CI installs it) + pip-installed into the test venv. Added the
  `fake_slack` pytest fixture (`tests/conftest.py`, monkeypatched env). Tests: `test_fake_slack.py` 6/6
  incl. a guard that the **real** `AsyncSocketModeHandler` dispatches both fake-sent envelope shapes;
  full suite **477 passed** (3 pre-existing SDK-import errors). Local-only, Devika-authored, not pushed.
  **Unblocks UI-Refresh Phases 1–5** (the integration/e2e tests run the real adapter against this fake).
- **2026-06-28** · Devika / `platform/personas` · **Live Slack re-test + 5 follow-up fixes.** Ran the
  full messaging re-test end-to-end against real Slack (after re-auth). **Verified live:** super-agent
  retired; live channel reaction (per-session event bus — the original bug); error visibility
  (Unrouted panel); DM routing (park + deliver); allow-list/recent-senders UI; real inbound→reply
  round-trip; and the marquee **Unattended approval → Slack Block Kit buttons → durable resume**
  (needed Slack "Interactivity" enabled). Added a new **"Unattended approvals → channel" routing UI**
  (`99a8748`) and fixed bugs the test surfaced: reconnect clobbering a stored token/allow-list with
  the masked placeholder (`d3cb0a2`), recent senders showing "unknown" → resolve via users.info
  (`b978044`), approval card missing tool args (`b4a822d`), and Slack setup docs missing the
  Interactivity toggle + users:read (`c6f4669`). Each fix has tests; full suite 466 passed (3
  pre-existing SDK errors). Note: an env-gated `COWORKER_DEBUG_INJECT` endpoint (feeds the real
  inbound path without a live bot) rode along in `b4a822d`; off by default. **Remaining:** push (Rohit).

- **2026-06-28** · Devika / `platform/personas` · **Messaging refactor — 5 fixes (4 commits).** Acting
  on the gaps the live Slack channel-subscription test surfaced. **Fix 1** per-session event bus
  (`_session_clients` + `broadcast_session`): background turns (channel delivery, self-wake, durable
  resume) now stream live to any open socket instead of being discarded; `turn_start` surfaces the
  inbound message as a user item. **Fix 2** error visibility: `unrouted.py` dead-letter store —
  background-turn ERRORs + undeliverable inbound are logged + parked (`GET /v1/unrouted` + Integrations
  panel) instead of vanishing. **Fix 3** allow-list onto the (reachable) Connectors tab: `/v1/connectors`
  now carries `allowed_users` + recent senders; ported the allow/recent UI from the orphaned super-agent
  view. **Fix 4+5** DM routing (prefs `dm_session` + `/v1/messaging/dm-route`; DM → designated session
  or parked) **and retired the super-agent** (deleted `connectors/superagent.py`, the `_sa_*` surface,
  `/ws/superagent`, `SUPERAGENT_SESSION_ID`, `SuperAgentView`; `myhelper` kept resolvable). Tests:
  `test_session_events.py`, `test_connectors_allowlist.py`, `test_dm_routing.py` + updated
  subscriptions/connectors (462 passed, 3 pre-existing SDK errors). GUI tsc clean. **Remaining =
  Rohit's visual review + live Slack re-test (per the plan's E2E checklist).**
- **2026-06-27** · Devika / `platform/personas` · **Messaging↔sessions design + plan (pre-compact).**
  Wrote `MESSAGING-AND-SESSIONS.md` (channel subscription vs Inbox; one-bot-identity mention model;
  `ask_user`; durable correlation best-effort→hardened). Added the "RESUME HERE" remaining-work plan
  to Current status. Also committed this session: shared top zone (Integrations/Automations) +
  Pinned band (code-complete, not screenshot-verified — browser dropped). No new tests this entry.
- **2026-06-27** · Devika / `platform/personas` · **Phase 4 first fixes (verified live).** Sample
  persona `~/coworker-personas/research-analyst.md` (for manual install demo). Backend: default-
  first ordering (Cowork registered first) + Chat hidden by default (`default_surfaced`). Frontend:
  family-aware Projects (`familyOf(id)==="code"`), expand≠switch (`browseKey`), `startNewSession`
  takes a persona. Verified in the running app (screenshots saved): picker = OpenCoworker/Code/Ops
  (no Chat); Code→Projects, OpenCoworker/Ops→Recents; expanding Code/Ops keeps the OpenCoworker
  chat loaded. Tests: persona/server updated (Python 445 passed, 3 pre-existing SDK errors); GUI
  `npm run build` green. **Remaining Phase 4: Pinned band, attention/liveness badges, family-aware
  App gating — stopped here for Rohit's manual review.**
- **2026-06-27** · Devika / `platform/personas` · **UX pass (design + spec only).** Settled the
  family model (binary workspace model; roles compose; Code stays a persona; frontend must be
  family-aware), the "Pinned band on top" sidebar IA (expand≠switch, default-first, Chat hidden,
  pins = pure accessibility, no "always-on" mode), and attention-vs-liveness sidebar indicators
  (attention count bubbles to the Inbox; liveness is a count-less dot; no new tab). Updated
  `PERSONAS.md` + `PERMISSIONS-AND-INBOX.md` + added Phase 4 here. No code yet.
- **2026-06-27** · Devika / `platform/personas` · **First-pass visual review + 2 fixes.** Drove
  the running app: all 4 GUI surfaces verified. Fixed the Unattended toggle to read its persisted
  state on mount (added `GET /v1/sessions/{id}/unattended`) and removed the redundant Settings
  Surfaces toggles (superseded by the Personas tab). Verified live; typecheck + server tests green.
  Remaining deferred polish: composer-disable while unattended; inbox routing config UI; reactive
  persona refresh after enable; inbox unread badge.
- **2026-06-27** · Devika / `platform/personas` · **GUI pass done** (4 commits: Personas tab +
  api client; persona-driven picker; Inbox view; Unattended toggle). `npm install` + full
  `npm run build` (tsc + vite) pass. **All phases 0–3 complete.** Remaining = Rohit's visual
  review + deferred polish.
- **2026-06-27** · Devika / `platform/personas` · **Phase 3 wiring done** (3 commits: unattended
  approver-swap; self-wake resume busy→steer/idle→run via scheduler extra_tick; gateway inbound
  correlation + outbound channel mirror). Full suite 444 passed.
- **2026-06-27** · Devika / `platform/personas` · **Phase 3 backend done** (2 commits: snapshot
  install; on-event wake + multi-inbox routing). Marketplace decided = dir/GitHub load + snapshot
  (no hosted service). Added `inbox_routing.py`, `WakeStore` events, manager routing store + REST.
  Tests: inbox_routing/self_wake/loading (13 + snapshot; full suite 438 passed). **Remaining:
  live wiring (approver-swap, scheduler-resume, gateway inbound) + GUI pass.**
- **2026-06-27** · Devika / `platform/personas` · **Phase 2 backend/logic done** (2 commits:
  2a overrides+loading, 2b inbox+unattended+self-wake). Added `overrides.py`, `personas/loading.py`,
  `inbox.py`, `unattended.py`, `selfwake.py`; manager stores + REST. Tests: loading/overrides/
  inbox/unattended/self-wake (25 passed). Live approver-swap + scheduler-resume + GUI carried
  forward. Next: **Phase 3 — ask Rohit about marketplace before building.**
- **2026-06-27** · Devika / `platform/personas` · **Phase 1 backend done.** Added `personas/`
  (manifest + registry), Ops markdown persona, Agent traits replacing name-branching in
  build_engine/manager, `/v1/personas` endpoints. Tests: manifest/registry/builtin/session
  (28 passed; full suite 404 passed, 3 pre-existing SDK errors). GUI (picker + settings)
  deferred to a visual-review pass. Next: Phase 2.
- **2026-06-27** · Devika / `platform/personas` · **Phase 0 done.** Added `risk.py` +
  `catalog.py`; refactored `permissions.py` to risk classes and Code/Cowork to build via the
  catalog. Tests: `test_catalog.py`, `test_permissions_risk.py` (41 passed; full suite 348
  passed, 3 pre-existing SDK errors). Next: Phase 1 manifest + registry.
- **2026-06-27** · Devika / `platform/personas` · Wrote design docs (`PERSONAS.md`,
  `PERMISSIONS-AND-INBOX.md`) + this ledger. No code yet.
