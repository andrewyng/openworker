# UX Decisions — OpenCoworker GUI

**Owner:** Rohit. **Status of this doc:** living spec — the source of truth for *why the UX is the
way it is*. Every entry is a deliberate decision with a rationale.

> **For agents/contributors:** Do **not** restructure or "improve" any UX listed here without the
> owner's explicit sign-off. If a change seems warranted, propose it (add a `↪ Proposed` note under
> the relevant entry) and ask — don't silently override a decision. New UX requests get appended
> here with their rationale before/while they're built.

Mocks that realize these decisions live in `platform/ui-mocks/` (`redesign.html`). The mock is the
visual spec; this doc is the reasoning + data-model spec. **Implementation** is specced in
[`UI-REFRESH-SPEC.md`](UI-REFRESH-SPEC.md) (+ [`UI-REFRESH-VERIFICATION.md`](UI-REFRESH-VERIFICATION.md),
[`FAKE-SLACK-SPEC.md`](FAKE-SLACK-SPEC.md)).

Status legend: **Decided** (settled), **Proposed** (endorsed, not finalized), **Mocked** (shown in
the HTML mock), **Built** (in the real React/Python app).

---

## 1. Connector-agnostic source model
- **Decision (Decided / Mocked):** Every external source renders through one connector registry:
  `{ id → label, brand color, logo }`, with a **default fallback** (neutral plug glyph + gray) so an
  unknown/custom/not-yet-shipped connector still renders cleanly.
- **Rationale:** We're going multi-connector (Slack now; Salesforce, HubSpot, GitHub, Datadog,
  PagerDuty, Telegram, custom/MCP next). UI must never hardcode "Slack"; adding a connector should be
  a registry entry, not new components.
- **Data model:** the registry lives on the connector descriptor (brand color + logo id). Recommended
  connectors that we don't ship yet still get a label + fallback badge.

## 2. Session conversation
### 2a. Connector inbound-message card
- **Decision (Decided / Mocked):** Inbound messages from a connector get a dedicated card (not the raw
  `💬 New message on slack:C…` bubble): a subtle brand-tinted header with **connector logo +
  channel/entity name + person name + time**, body below, brand-tinted left edge.
- **IDs on hover:** the header shows friendly names by default; hovering reveals the raw IDs
  (`C0BD7KZ1AH5 · U07JK68S4BH`). Names come from connector resolution (e.g. Slack `users.info`).
- **Rationale:** IDs are noise in the common case but essential when debugging routing; hover keeps
  both without clutter. The card generalizes to Salesforce "Case #123", etc.

### 2b. Collapsible tool/approval steps
- **Decision (Decided / Mocked):** Tool calls + approvals (e.g. `send_message`, `write_file` approval)
  collapse into a single `<details>` disclosure (`"2 actions · 1 approval ✓"`), **collapsed by
  default**. Expand for the per-step detail (args, who approved, where).
- **Rationale:** The thread should read message → reasoning → reply. Mechanics are available on demand,
  not in your face. (Re-introduces the earlier collapsed-steps pattern.)

## 3. "Sources" bar + per-session connections drawer
- **Decision (Decided / Mocked):** Below the session title sits a **Sources** bar showing **connector
  icons only** (overlapping avatars) + a short "needs attention" badge. Clicking it opens a
  **per-session connections drawer** (right slide-over).
- **Attention badge:** use a compact **⚠ N** (yellow exclamation + count) to denote *N connections
  recommended but not yet connected* — short, not the verbose "N recommended". *(Req 2026-06-29)*
- **Drawer content (persona-aware):**
  1. A **why-connect blurb** for the session's persona ("Ops works best wired into GitHub, Slack, a
     monitoring dashboard…") + a small progress indicator (`2 of 4 connected`).
  2. **Connected** sources for this session (with live status **and an enable/disable toggle** — see §4).
  3. **Recommended connectors** and **recommended MCP servers**, each with **the value it unlocks**
     ("so I can pull the firing alerts"), tiered **core vs optional**, with Connect/Add.
  4. A link out to **global Integrations**.
- **Rationale:** Turns an empty integration state into a capability story; progressive setup the user
  can finish later (explicitly *not* gating onboarding). Per-session scope keeps it contextual; the
  drawer reuses the global Integrations visual language so it's one mental model.
- ↪ **Superseded (2026-07-11):** the always-visible bar + `⚠ N` badge is replaced by the
  session-settings row (rest = one icon; the nudge moves into the drawer) — see §23. The drawer
  itself lives on, renamed "Session settings" and extended with working directories.

## 4. Connection hierarchy: persona-level vs session-level enablement  *(Decided — data-model change)*
- **Decision (Decided 2026-06-29 by owner):** Today connectors are enabled **per persona**. We're
  introducing **per-session enable/disable**: a connection can be enabled for the persona but toggled
  **off for a specific session** (Connect *and* an Enable toggle in the drawer).
- **Hierarchy (confirmed):** `account-connected connector` → `persona enables it` → `session may
  disable it`. Effective state for a session = connected AND persona-enabled AND not session-disabled.
- **Rationale:** A session may want a narrower surface than its persona's default (e.g. mute Slack for
  one focused session) without disconnecting the account or editing the persona.
- **Open:** storage shape (a per-session override set, mirroring how Unattended/subscriptions are
  stored). To design before building.

## 5. Richer persona manifests — `recommends`  *(Decided → Built: data model)*
- **Decision:** Persona manifests gain a `recommends` list: each item references a `connector:` or
  `mcp:`, with a `reason` (the value it unlocks) and a `tier` (`core` | `optional`). This drives the
  drawer's persona-aware recommendations (§3) — data, not hardcoded UI.
- **Rationale:** "Good evolution before we go live." Recommendations must travel with the persona
  (incl. third-party personas), so they belong in the manifest, not the frontend.
- **Validation:** `recommends` is **not** strictly validated against shipped connector descriptors (a
  persona may legitimately recommend a connector we don't ship yet, or an external one). Only the
  structure (kind/ref/tier) is validated. *(Contrast: `tools` ARE validated against the catalog.)*

## 6. Global Integrations page
- **Decision (Decided / Mocked):** Replace the single long scroll with a **left sub-nav**:
  **Connectors · Messaging routing · Activity · MCP servers**.
  - *Connectors*: card grid; a connected two-way connector expands to its allow-list + recent senders.
  - *Messaging routing*: groups the three previously-scattered controls — channel subscriptions, DM
    routing, Unattended-approvals routing — into one panel.
  - *Activity*: the Unrouted/failed dead-letter table (with a count badge in the nav).
- **Rationale:** Things were "dumped one after another," hard to find. Grouping by intent fixes
  discoverability. (Global = account-wide; the per-session drawer in §3 is the scoped counterpart.)

## 7. Left navigation — dual layout  *(Decided / Mocked)*
- **Decision:** Support **two session layouts**, user-toggleable via a small icon next to the
  "OpenCoworker" wordmark:
  1. **Flat** — Pinned + Recent (current).
  2. **Grouped by persona** — sessions clustered under their persona.
- **Boundaries (Decided 2026-06-29):** the grouped layout's per-persona sections must have **clear
  visual boundaries** — render each persona group as a **bounded card** (faint surface + border +
  header), not just a text header (the first cut had indistinct boundaries).
- **Per-group gear → Persona page (§9):** each persona group header carries a **settings gear** that
  opens the persona's detail page.
- **Rationale:** Different mental models — time/recency vs. persona/role. Don't force one. Pins remain
  pure accessibility in both.
- **Single-line session rows (Decided 2026-07-21 by owner):** Pinned/Recent rows are **one line**
  (title only) — the persona/workspace subtitle is removed. With personas disabled for the first
  release the subtitle read as noise ("Coworker" on every row). When personas return, surface the
  persona **on hover** (row tooltip), not as a second line.
- **Open:** persist the choice (prefs); default = Flat.

## 8. "New session" — persona picker  *(Decided / Mocked)*
- **Decision (Decided 2026-06-29):** "New session" is a **split button** — the main action starts a
  session with the **last-used / default persona**; the **▾** opens a dropdown to pick from the
  **enabled personas** (icon + name + tagline) + "Manage personas…" (→ §9). Not a modal, not an
  always-dropdown.
- **Rationale:** Fast path for the common case + discoverable choice without a heavyweight picker.
  Personas are the product's organizing concept, so surfacing them at session creation is right.

## 9. Persona detail page  *(Decided / Mocked)*
- **Decision:** A per-persona detail page (opened from the grouped-nav gear, or "Manage personas…")
  shows: **identity** (icon, name, tagline) + an **Enable** toggle; **About**; **built-in
  capabilities** (tools); **Connections for full benefit** (the manifest `recommends`, §5 — core/
  optional + the value each unlocks + connect state); **"New sessions get by default"** (the
  persona→session default connections, each toggleable — the middle layer of the §4 hierarchy); and
  **defaults** (models, mode, workspace).
- **Rationale:** The owner needs one place to answer "what *is* this persona, what does it need
  connected, and what does a session inherit by default?" It's also where the persona-level layer of
  the §4 hierarchy is configured.

## 10. Persona enablement / onboarding  *(Proposed — tabled)*
- **Question (owner):** Enabling a persona is just a toggle today, but it implies setup (connecting
  the recommended sources). Should that onboarding happen **inside the first session**, or be a more
  **explicit upfront** step? Onboarding can be heavy if it requires connecting several sources.
- **Recommendation (mine):** Keep it **lightweight and progressive, never gating**. Enabling a
  persona flips it on and surfaces its recommended connections (reuse the §9 page / a slim "set up
  Ops" panel) where the user connects what they want **now** and the rest stay as `⚠ N` nudges in the
  session's Sources bar (§3) — completable later. The first session works immediately with whatever's
  connected; it just shows the nudge. Avoid a blocking multi-step wizard.
- **Status:** **tabled** for later by owner; design intent recorded so we don't accidentally build a
  heavy gated wizard.

## 11. Session top bar — no model / mode chips  *(Decided 2026-06-29 by owner)*
- **Decision:** The session top bar shows **only** the title · persona · ⋯ menu (left) and the
  persona/panel icons (right). The read-only **model** chip (`anthropic:claude-opus-4-8`) and
  **permission-mode** chip (`Interactive`) are **removed**.
- **Rationale:** Both were non-interactive `<span>`s that *duplicated* controls already in the
  composer — the model dropdown and the **"Ask for approval"** permission-mode dropdown — where the
  user actually changes them. Worse, the mode chip showed the raw enum **"Interactive"** while the
  composer labels the identical setting **"Ask for approval"**, so the two names read as two different
  things. The composer is the single source of truth; the chips were clutter. (The persona detail
  page §9 still documents the persona's *default* model + mode — that's a different surface.)
- **Note:** The three permission modes remain unchanged — **Plan** (read-only, propose first),
  **Interactive** = "Ask for approval" (asks before edits/commands), **Full access** (runs without
  asking). This is a separate axis from the composer's **Unattended** toggle (routes approvals to the
  Inbox for hands-off runs). Mock (`redesign.html`) updated to match.

## 12. Sidebar bottom — Inbox + a single ⚙ menu  *(Decided 2026-06-30 by owner)*
- **Decision:** The sidebar bottom was a stack of 5 rows (Integrations · Automations · Inbox ·
  Activity + a path/gear footer) — "very busy." Collapse it to **two rows**: **Inbox** stays visible
  (its attention badge must be glanceable), and **Settings · Integrations · Automations · Activity**
  move into one **⚙ "Settings & more"** click-to-open menu that opens upward (Codex/Claude-style). The
  current workspace path becomes the menu's header (was the standalone footer).
- **Rationale:** Inbox is the only high-frequency destination; the rest are occasional (Integrations
  is set-up-once, Activity/Settings are rare). Claude/Codex use a bottom menu for *account* items —
  we have no account (local, BYO-key), so ours holds *app* destinations instead. Net: 5 rows → 2,
  primary nav (New session, Search) stays clean at top.
- **Not chosen:** moving Inbox to the top cluster (kept it bottom-adjacent to its badge); keeping
  Integrations/Automations visible (folded them in for a cleaner bottom — promotable later if usage
  warrants).
- ↪ **Superseded (2026-07-11):** the no-account premise went stale when Phase 3 shipped cloud
  sign-in — the bottom is now a single account row with a state-driven inbox chip, and the ⚙
  menu became the account menu. See §26.

---

## 14. Per-session Slack channels — Sources drill-down  *(Decided 2026-07-01 by owner)*
- **Decision:** Managing which channels a session listens to belongs in the **Sources drawer**, not
  the composer's `+` menu. On a connected **two-way** messaging connector's row (Slack/Telegram), a
  **"Channels · N ›"** control opens a **child panel with a ‹ back button** — subscribed channels
  (× to stop listening) + an add picker (recent-channels datalist). The `+` menu stays about
  attachments only.
- **Rationale:** Sources is *the* per-session connection surface — it already owns "is Slack on for
  this session" (the mute toggle). Channels are the same category (per-session, connector-scoped
  standing config); the `+` menu is per-*message* attachment — a different mental model. Placing it
  on the Slack row makes it discoverable in context and gates itself (only shows when the connector
  is connected+enabled and two-way). The child panel gives the picker room and generalizes into a
  per-connector settings drill-down (allow-list, DM routing could live there later).
- **Reuse:** pure GUI — existing `subscribeChannel`/`unsubscribeChannel`/`getSubscriptions`/
  `getRecentChannels`; `two_way` read from the connector index the drawer already loads. No backend.
- **Not chosen:** the composer `+` menu (wrong mental model, needs conditional gating in the
  composer); inline row-accordion (cramped in the 420px drawer, clutters the Connected list).

---

## 13. Settings as a full page; Activity re-shelled  *(Decided 2026-07-01 by owner)*
- **Decision:** Retire the top-tab **ManageModal** and make **Settings** a full-page surface that
  reuses the Integrations shell (208px left sub-nav + centered panel + `PanelHead`). Split by scope
  (**Option 2**): Settings holds the *local/app* concerns — **Appearance · Files · Models · Personas**;
  anything *external* (Connectors · Messaging · MCP · Activity) stays under **Integrations**. The
  **Activity** page (old `AuditView`) moves onto the same page shell, dropping the legacy `page-view`
  layout and its duplicate header.
- **Rationale:** The modal was the last top-tab surface and the last `page-view` straggler, and it
  *duplicated* MCP/Connectors that already live under Integrations. One page idiom everywhere; no
  duplicated homes; more room than a modal. Models + Personas field bodies were re-skinned to the
  Tailwind card idiom to match Appearance/Files (left-aligned segmented control, carded config).
- **Wiring:** the ⚙ menu's *Settings*, the desktop tray's Settings event, the composer's "no model"
  chip, and the persona-card gear all route to `surface: "settings"` with an initial section. The
  shared tab bodies (`ModelsTab`, `ConnectorsTab`, `McpTab`) moved to `ManageTabs.tsx`; dead
  `SettingsTab`/`AuditTab`/`ManageModal`/`UnattendedToggle` removed.
- **Not chosen:** Option 1 (one unified Settings+Integrations hub) — cleaner long-term but a bigger
  rebuild and a very long single nav; the Settings/Integrations split reads more naturally.
- **Follow-up:** dead-CSS pass for now-unused modal/legacy classes (`manage-*`, `mtab`, `page-view`,
  `sa-view-*`, `persona-row`, `persona-install`, `consent-card`, `audit-*`); sync `redesign.html`.

---

## 15. Persona Gallery as a modal; delete; team-sharing-ready  *(Decided 2026-07-03 by owner)*
- **Decision:** The Gallery leaves the inline Settings ▸ Personas section and becomes a
  **screen-sized modal** opened from a "Browse the Persona Gallery" link on the Personas page
  (modal, not a route — installs finish back on Personas, disabled pending consent). Modal anatomy:
  header (title · search · close) + source chips (**All · From OpenCoworker · From your team**),
  a **featured carousel** (publisher-flagged `featured` cards, user-scrolled — never auto-rotating),
  and the catalog **list**; every card opens the in-modal **solo page** (hero + pitch + locally
  derived capabilities) — install only happens there. Personas page rows gain a **delete**
  affordance (non-builtin only, inline confirm, works signed out).
- **Not chosen:** loading cloud-served HTML in an iframe — it would reopen the
  "cloud describes capabilities" channel the solo-page design deliberately closed, need a spoofable
  postMessage install bridge, and break offline/theming/e2e. Rich showcase comes from publisher
  markdown + images rendered by our own components instead.
- **Visuals:** connector chips show hand-drawn SVG **brand marks** (`brandIcons.tsx`, neutral plug
  fallback); personas without publisher imagery get a deterministic **generated hero**
  (`PersonaHero.tsx`, hue from slug) so the carousel/solo pages never look empty.
- **Team-sharing-ready (design-only for now):** the "From your team" chip + empty-state teaser ship
  now; publish-to-tenant later reuses the gallery's existing `tenant_only` visibility. Team personas
  get **zero extra trust** — same validation, local capability derivation, disabled-until-approved.
- **Updates (design-only):** installed personas will record source (slug+version+hash); update
  re-runs consent **iff the capability surface changed** (never a silent permission expansion).

---

## 16. Workspace enum collapsed into family  *(Decided 2026-07-03 by owner)*
- **Decision:** The persona `workspace` enum (`git | project | deliverable | none`) is retired as a
  behavioral axis. `family` alone decides: **knowledge → transparent per-conversation scratch**
  (real folders added as session roots when needed; no folder gate, ever); **code → explicit
  directory picked by the user** (gate + project-grouped sidebar). Manifests may still carry the
  key (parsed + typo-checked for back-compat) but it's inert — the effective value derives from
  family. Built-in Ops moves to scratch + multi-root; Chat keeps `none` via its builder.
- **Rationale:** the only combo the enum enabled — knowledge+`project` (Ops) — predates multi-root
  knowledge sessions, which cover "work against a real folder" strictly better (progressive, no
  modal wall). The two-axis model split behavior across code paths (engine branched on family, the
  gate/sidebar on workspace) and produced the 2026-07-03 smoke-test contradictions: a gate demanding
  a folder while a scratch-backed chat ran behind it, and grouped-sidebar sessions with no home.
- **Future:** a `git: true` refinement of code-family may later force "start from a git repo", with
  clone-into-scratch as the safe execution mode. Personas may also *suggest* a root ("works best
  with a folder attached") — a banner, never a wall.

---

## 17. The model is fixed per session  *(Decided 2026-07-04 by owner)*
- **Decision:** a session's model is chosen up to the first turn, then **locked for the session's
  life**. The composer's model picker is interactive on a fresh session and becomes a read-only
  pill afterwards ("start a new session to switch"). Enforced **server-side** — the first
  user_message's `model` binds the engine; later message models and `set_model` are ignored —
  not just in the GUI, so API callers and socket races can't rebind a running conversation.
- **Mechanism:** every user_message carries the composer's visible model (the first one binds).
  This replaced a separate `set_model` handoff that could race the socket lifecycle (silent no-op
  before the socket exists; losable during the reconnect every new cowork session does to adopt
  its scratch dir; `ready` overwriting the visible selection) — the owner's repro was picking
  Opus and getting Kimi.
- **Rationale:** sessions are task-scoped; mixed-model transcripts invite provider-quirk breakage
  (tool-call replay, vision content), thrash prompt caches, and make behavior impossible to
  reason about. Mid-session switching was inherited chat-app convention, never a designed feature.
- **Future (owner, 2026-07-04, not built):** mid-session switching may return as a *designed*
  feature — a compatibility-matched switch, not a free dropdown: only models whose capabilities
  cover what the transcript already uses (tool-calling always; vision iff images are in history;
  reseller/vendor quirks vetted via the model matrix), with an explicit affordance + warning.
  Also covers the provider-died-mid-session case. Until then: start a new session.
- **REVISED (owner, 2026-07-22): mid-session switching SHIPPED** — the "future" above, built.
  The composer picker now stays actionable for the session's whole life; a switch lands as a
  persisted `model_switch` info marker in the transcript ("Model switched to <label>", with a
  degradation warning when history holds images the target can't see — those go out as visible
  placeholders, mirroring the PDF fallback). Still server-enforced sanely: first bind is silent,
  rebinds are refused mid-turn, and the marker is history the provider never sees. Grounding
  that unlocked it: history is canonical OpenAI shape with per-call provider conversion, and the
  Gemini 3 signature sidecar work proved cross-model histories replay cleanly (live-drilled
  A→B→A, 2026-07-22). Also the recovery path for provider-died and poisoned-template sessions.

---

## 18. Disable a persona = archive its conversations  *(Decided 2026-07-04 by owner)*
- **Decision:** disabling a persona **archives all of its real sessions** (unarchived,
  non-internal) in the same server-side action. Its sidebar section then disappears naturally —
  the grouped layout's never-orphan rule ("a persona with unarchived sessions always gets a
  section") stays untouched, because after the archive there is nothing left to orphan. No
  greyed-out sections, no time-based ("inactive for N hours") heuristics: every sidebar
  visibility change traces to an explicit user action.
- **Confirm:** unchecking *Enabled* on Settings ▸ Personas only **arms an inline confirm** when
  the persona has conversations — "Disabling archives its N conversations — they stay available
  under 'Show archived'" with Disable / Keep enabled (the same two-step idiom as row delete).
  With zero conversations the checkbox flips instantly; the confirm exists for the side effect,
  not for ceremony.
- **Re-enable never unarchives.** That would overwrite the user's archive state; history returns
  one click at a time via the Show-archived disclosure. The archive step lives in the manager
  (`set_persona_enabled`), so both persona routes — and any future client — share the semantic.

## 19. Unauthorized senders: park, don't drop; connector card is the config surface  *(Decided 2026-07-04 by owner)*
- **Problem:** first contact on a two-way connector took a double-send — the allow-list
  (correctly closed by default) silently dropped the first message; the sender only appeared
  under "Recent senders", and after being allowed had to message AGAIN.
- **Decision:** an allow-list drop **parks the message** instead of losing it. The connector's
  expanded card shows "Messages from senders you haven't allowed" with three resolutions:
  **Allow & deliver** (allow-list the sender AND re-inject the original message through the
  normal inbound path — buffer + subscriptions — no re-send), **Allow only** (future messages
  flow; this one is discarded), **Dismiss** (throw away, nothing else changes). Parked items
  are capped and persisted (`parked.json`).
- **The expanded connector card is the one-stop config surface** for a two-way connector:
  tools, allow-list + recent senders, parked messages, and "Sessions listening" — the
  per-connector cut of the global Channel-subscriptions table (which stays under
  Integrations ▸ Messaging routing; the owner looked for it on the connector and couldn't
  find it there).
- **Tokens hot-reload (no restarts):** a platform socket authenticates at connect time, so new
  creds require reopening that socket — and nothing else. Connect/disconnect of a messaging
  connector now refreshes the gateway listeners in-process; the sidecar never restarts. (Found
  when pasted Slack tokens "did nothing": the listener only started in the app lifespan.)
- ↪ **Partially superseded (2026-07-08):** the parked/allow-list semantics stand, but the config
  surface moves from the expanded card to the connector's **detail subpage** — see §21.

## 20. Collapsible left nav + RECENT header group/filter  *(Decided 2026-07-05 by owner)*
- **Decision:** The left nav gains three refinements (extends §7):
  1. **Collapse (⌘B) with hover-peek.** The nav can collapse so the content reclaims the width
     (grid → single column; the sidebar is taken out of flow). Collapsed, hovering the left edge
     (`.nav-hover-zone`) *peeks* it back as a floating overlay (shadow, over content, auto-hides on
     leave); ⌘B, the brand pin button, or the floating reveal button (`.nav-reveal-btn`, cleared
     past the traffic lights on desktop) dock it. Collapse is persisted per-device (localStorage).
  2. **RECENT header owns grouping + filtering.** The old brand-bar layout toggle is gone; the
     brand bar now holds only the wordmark + the collapse/pin control. A **RECENT** section header
     (like PINNED) carries a sliders control that opens one popover with **Group by** (Persona =
     the accordion ↔ Chronological = the flat list) and **Filter by coworker** (persona checkboxes;
     none checked = all shown). The popover stays open so you can group AND filter in one visit.
  3. **Artifact preview auto-collapses the nav.** Opening a full artifact preview (PDF/webpage/
     sheet) collapses the nav for max width and restores it on close — unless you manually toggled
     the nav meanwhile (the manual action takes control; the auto-collapse never overwrites the
     saved pref).
- **Inspiration (not copied):** Claude/Codex collapsible sidebars + their group/filter menus.
- **Tests:** `e2e/nav-collapse.spec.ts` (collapse/dock, ⌘B, popover grouping); `Sidebar.test.tsx`
  updated to the new group/filter control.

## 21. Connectors redesign: connected-first list, detail subpages, add-modal, privacy filters  *(Decided 2026-07-08 by owner; mock: `ui-mocks/connectors-redesign.html`)*

Driven by managed-Slack going multi-workspace (M3.5): the connector card had outgrown itself, and
the interim "Slack workspaces" Integrations tab was the wrong shape. One pattern for ALL connectors:

- **Navigation:** the Integrations sub-nav stays fixed (Connectors · Messaging routing · Activity ·
  MCP servers) — **no per-connector nav items**. Each connected connector opens a **detail subpage**
  under Connectors (breadcrumb `‹ Connectors`). Slack manages workspaces there; Gmail accounts;
  HubSpot portals; Teams orgs. Supersedes both the expanded-card config surface (§19) and the
  short-lived "Slack workspaces" tab.
- **Connectors list:** **Connected first**, in its own section — single-column rows with brand
  badge, one-line status, a **health chip** (● Live / ⚠ Reauthorize / ● Ready) and a chevron; the
  row itself navigates. "Available" below (row list + Connect pills), long tail behind "show all".
  Cloud sign-in shrinks to a slim strip. Problems must surface **in the list**, not after a click.
- **Visual grammar:** macOS System-Settings style — grouped inset lists on gray paper, hairline
  separators, 44px rows, pill buttons, sentence-case group headers, minimal copy (footnotes, not
  paragraphs; IDs on hover). Owner: "I really like Apple for their aesthetics"; v1 was too verbose.
- **One entry point to add a connection:** the header button (`＋ Add workspace` / `＋ Add account`)
  opens a **modal** with a One click | Manual pill switcher, each pane carrying its instructions
  (Slack: relay vs Socket-Mode tokens; HubSpot: OAuth vs private-app token). **Modal only when a
  connector has ≥2 connect modes** — single-mode connectors (Gmail, Teams) launch directly. The
  bottom-of-page "Add a …" sections are gone (duplicate CTA).
- **Multi-account everywhere:** Slack workspaces, Gmail accounts, HubSpot portals, Teams orgs —
  same skeleton: one group per account with per-account auth state and Disconnect. A **Default**
  badge answers "which account do tools use?"; sessions can override in Sources; **every approval
  card names the account/portal it will act on** (a sandbox test can never quietly hit production).
  Profile keying generalizes the Slack pattern: `gmail:account:<email>`, `hubspot:portal:<hub_id>`.
- **Tools are a collapsed disclosure on every detail page** ("Tools · 3 of 5 enabled") — the lever
  exists everywhere but stays quiet; write tools carry "asks first" tags; destructive tools (e.g.
  HubSpot delete) are **never offered**.
- **Connection health = three honest layers** (Slack page): desktop↔relay WS (Live/Reconnecting/
  Offline + last event), cloud sign-in (required for relay), per-workspace/account token health.
  We never claim "Slack↔cloud is down" — event silence is indistinguishable from a quiet workspace.
- **Privacy filters, enforced at the DESKTOP, silent to agents:** the invariant is **"the cloud
  knows routing; the desktop knows content and policy."** Gmail: "Never show agents" senders/
  domains/labels — filtered mail **does not exist** from the model's perspective (no tombstone:
  `<truncated>` markers leak sender/subject and invite the model to reason around policy); the
  user sees "N hidden by filters" out-of-band on the tool card (MessageSource-sidecar pattern) +
  audit entries. HubSpot: **hidden fields** (property denylist stripped before model context) +
  **read-only vs read & write chosen at consent time** (scope minimization is the real ACL) +
  "agents see what the connecting HubSpot user sees" (record ACLs belong server-side in HubSpot
  permission sets — client-side per-record filters are theater and are deliberately NOT built).
- **Teams (concept):** relay-only (bots need a public endpoint — no manual mode). Consent is
  self-serve: chats = user installs it; channels = any **team owner** (RSC, per-team consent);
  org-blocked = Teams' native "Request approval" asks IT once. No tenant-admin Graph permissions
  by design — same per-actor privacy posture as the Slack relay.

## 22. Session screen cleanup: contextual top-left cluster, facts subtitle, three-control composer  *(Decided 2026-07-11 by owner; mocks: `ocw-context/docs/ux-improvements/mocks/UX-002-session-screen.html`)*

Owner sketch (hand-drawn) → discussed and resolved in the UX ledger (ocw-context, UX-002). Both the
fresh-session and in-progress screens shed chrome:

- **Top-left cluster `[sidebar] [+ new session] [search]` renders only when the sidebar is
  collapsed** — the expanded sidebar already owns those actions; never duplicate them. Build note:
  the cluster sits inside §20's hover-peek zone — the peek must not fire while the cursor is on
  these icons (start the zone below the icon row or add a short delay; the pin/reveal co-location
  logic already handles this corner). Keyboard shortcuts stay global regardless of sidebar state.
- **Centered title with a facts subtitle** beneath: `(Coworker · Opus 4.8)`; code sessions include
  the workspace folder. The subtitle is the session's **fixed facts, not controls** — it replaces
  the locked-model pill (§17's lock expressed spatially) and the topbar "About this persona"
  sliders button (subtitle click → the coworker page). Topbar right: the **panel toggle only**
  (mirrored variant of the left nav's sidebar glyph, one glyph both states); the right rail
  absorbs **artifacts only**. *(Revised at visual review 2026-07-11: the topbar
  session-settings icon was dropped as redundant — §23's row owns the drawer.)* *(Revised
  again 2026-07-11: the ⋮ conversation menu is REMOVED — the nav row's hover cluster owns
  pin/rename/archive/delete, so the topbar menu was a strict subset; the title STAYS (with
  the sidebar collapsed it is the only session identifier, and the subtitle orphans without
  it). The topbar goes edgeless: no bottom border, paper-tinted glass — invisible at rest,
  frosts only when the transcript scrolls under it.)* *(Revised 2026-07-11, third pass —
  ledger UX-008, mock `UX-008-merged-topbar.html`: the §23 session-settings row DOCKS into
  the bar's left region — one bar, not two strips. With the nav expanded the settings icon
  is the first element after the panel edge; collapsed, it follows the [sidebar][+][search]
  cluster. The §23 contract is unchanged.)*
- **Composer = `[+ attach] [Mode ⌄] [send]`.** The **Mode menu** carries the five permission
  options (Discuss / Plan / Ask for approval / Full access / Custom) **plus the
  Unattended/send-approvals-to-Inbox toggle** at the bottom — "who approves, and when" is one
  mental model; the separate InboxControl leaves the row. *(Revised 2026-07-11, competitor
  composer comparison: the trigger is borderless and names the CHOSEN mode — "Ask for
  approval ⌄", not a generic bordered "Mode ⌄" pill.)*
- **The model picker appears only on a fresh session** (quiet chip on the composer's right);
  after the first turn the fact moves up to the subtitle. No interactive-then-disabled control.
- **Folder/roots control and branch chip leave the composer** → the session-settings drawer
  (§23). Folder access is standing session config, not per-message attachment — the same
  reasoning §14 used to keep channels out of the `+` menu. The `+` menu stays attachments-only.
- **Open at build time:** fresh-session greeting copy; whether suggestion chips survive.

## 23. Session settings row: hover to glance, click to manage  *(Decided 2026-07-11 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-003-session-row.html`)*

> **↪ RETIRED 2026-07-13 (§32, ledger UX-014):** the row, the glance, and the drawer merged into
> the right rail's **Access** section — the glance summary now lives permanently on that
> section's header, and the drawer's content edits inline in the rail. The trust semantics below
> (brand = live, gray = unavailable, nudge text never at rest) carry over in summary form.

Replaces §3's always-visible SourcesBar (ledger UX-003). One sub-header row above the conversation
whose contract is **rest = icon · hover/focus = glance · click = manage**:

> **↪ Geometry revised 2026-07-11 (ledger UX-008):** the row DOCKS into the topbar's left
> region — the standalone strip under the bar is gone (one 48px bar, ~36px returned to the
> conversation). Everything below — the rest/glance/click contract, gray-is-the-nudge, zero
> reflow, deep links — is unchanged; only where the row renders moved.

- **Rest:** a single quiet icon, constant row height. **No nudge text at rest, ever** — the
  "recommended source not connected" nudge lives only in the drawer.
- **Hover/focus** (~150–200ms reveal delay so mouse-crossing doesn't flicker; reveals on keyboard
  focus too): a glance strip — **connected source icons in brand color**, persona-**recommended
  but unavailable ones in grayscale** (the gray icon IS the nudge, wordless), a **folder count**
  ("2 folders"; code sessions show the folder *name* — the workspace is the session's identity),
  and a trailing **"Configure ›"**. Icons only, no labels or chips. **No reflow** — row height is
  identical resting and hovered.
- **Gray covers both** "not connected" and "connected but muted for this session" (§4 override):
  the strip answers "what can *this session* touch right now"; tooltips disambiguate. Only
  persona-recommended connectors ever appear gray — never the whole catalog.
- **Everything in the glance is a shortcut:** icons and the folder count click straight into the
  matching drawer section. Tooltips are load-bearing once labels are gone (per-icon name + state;
  folder paths on the count).
- **Click:** opens the drawer, renamed **"Session settings"** — sources (connect-in-context,
  channels child panel §14, mute toggles §4) plus a new **working directories** section (roots
  list, add/remove, branch). ⚠ recommendations render here.
- **Rejected:** the icon morphing to "Click to configure" on hover (self-narrating UI, layout
  shift, permanent noise — affordance comes from the glance's content being clickable); at-rest
  nudge text (owner call: drawer only); placing the icon in the topbar with an anchored popover
  (max-clean but spatially disconnected — in-place morph keeps discoverability; cf. the
  cloud-sign-in placement regression, 2026-07-09).

## 24. First-run onboarding: model → recipe → tips  *(Decided 2026-07-11 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-004-onboarding.html`; ledger UX-004)*

Replaces the settings-shaped first run. Three steps; **only step 1 gates** (§10's
progressive-never-gating rule):

- **1 — Connect a model.** Fields adapt per provider from the existing descriptors: key-only
  (Anthropic/OpenAI/Gemini, each with a create-a-key deep-link), endpoint-only (Ollama, prefilled
  — the free/local escape hatch), endpoint+key (Fireworks etc.). One **default-model dropdown**
  per provider (curated matrix, recommended pre-selected) — deliberately *not* an
  enable-checklist; curation stays in Settings ▸ Models. Inline Test/verify.
  *(Revised 2026-07-11 at owner's Mac-app walkthrough: headline = **"Welcome to
  OpenCoworker"** with "connect a model to get started" as the sub-line; native `<select>`s
  → the Settings-Models **SelectMenu** (sectioned Ready-to-use / Needs-setup, key-set dots);
  the **default-model dropdown is DROPPED** — the model is per-session (§17) and the old
  select never persisted anything — replaced by one pointer line to Settings ▸ Models; an
  optional endpoint (base_url with a default, on a keyed provider) collapses behind a
  **"Configure custom endpoint ›"** link (keyless providers keep it visible — the endpoint IS
  the connection); Test joins the action row: Skip setup … [Test] [Continue], status line
  fixed-height above it.)*
- **2 — Get your first automation running** (skippable). **Role tabs — Engineering · Sales ·
  Everyday** — each with a recipe one-liner, two connect rows, then the recipe card (source ·
  channel/time · cadence · consent). **One Cloud sign-in, lazily triggered by the first Connect**
  — never per-integration; tokens-stay-local copy on the pane. **Connections persist across
  tabs.** Channel fields carry the invite-@ocw hint. **Everyday** = morning brief (Calendar +
  Gmail) delivered in-app, Slack DM secondary. **Create automation enables only when the tab's
  connectors are connected.** The consent line — "post the digest to #X without asking each time;
  anything else still asks first" — is a **standing scoped approval: ledger UX-005, design
  pending — this section's BUILD IS BLOCKED on it.**
- **3 — You're set up.** Recap card of the created automation (absent if skipped) → Specialist
  coworkers tip (Show me → gallery) → one quiet session-control line, with **"Start working"
  opening the first session with the session-settings panel (§23) open** — teaching by landing,
  not telling.
- **Deferred (owner):** tab choice seeding which specialists the gallery features — revisit after
  the UI cleanup lands.

## 25. Standing scoped approvals for automations  *(Decided 2026-07-11 by owner; ledger UX-005 — unblocks §24's build)*

Recurring automations using gated tools (e.g. `send_message`) park an approval **every run** —
a weekly summary needing a weekly click isn't an automation. The fix is a remembered,
narrowly-scoped rule: *"this automation may call this tool against this exact target without
asking"* — **tool + target + owner (task id)**, none optional, no wildcards in v1. Rules live on
the `ScheduledTask` record (revocation on the task detail page; deleted with the task).

- **Minted on exactly two human-only surfaces — the model can never mint a rule:**
  1. **The creation consent card** (already exists — `create_scheduled_task` is approval-gated).
     Every automation surfaces its needs at creation: reads render as *disclosure* lines
     (read-only, no gating), writes are the *grants*, pre-set to allow. The agent path proposes
     the set via a new `permissions` field on the create-tool schema; the existing card renders
     it. **Rejected:** the agent writing `config.toml` — model-authored permission expansion in a
     global file, invisible in UI, outlives the automation.
  2. **"Allow every time"** on a recurring run's approval card — persists to the task record
     (unlike the session-scoped Always-allow). In-app only; not offered on Slack-mirrored
     buttons. The retrofit path.
- **Graceful degradation:** no grants → the automation still works; runs park approvals in the
  Inbox as today.
- **Invariants:** never offered for `risk=exec`/destructive tools (shell asks forever); additive
  on top of the run's permission mode — never a silent full-access upgrade; every auto-allowed
  call audits the rule; cards/audit name the account acted on (§21); persona install consent
  stays availability-only — installs never ship pre-approved writes.
- **Infra note:** `always_allowed_tools` + creation gating + run-time wiring already exist
  (`automation/models.py`, `manager.py`); the build is target-shaping those entries, the
  `permissions` create-field, the task-persistent Allow-every-time, and lifting `permissions.py`'s
  connector exclusion **for task-scoped, target-matched rules only**.

## 26. Sidebar bottom: one account row; Connectors renamed; Activity dedup  *(Decided 2026-07-11 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-006-sidebar-account.html`; ledger UX-006)*

Supersedes §12's bottom cluster. §12's rationale ("we have no account, so the bottom menu holds
app destinations") went stale when Phase 3 shipped cloud sign-in two days later; the slot becomes
the **account anchor** and the bottom is **exactly one row**:

- **Account row:** avatar/initials + first name (email lives in the menu header, never on the
  row) + a green dot when signed in to OpenCoworker Cloud; signed out = "Not signed in" and the
  menu leads with the sign-in CTA. No workspace-path header (the path lives in Settings ▸ Files).
  Telemetry toggle moves to Settings. "Settings & more", the Inbox row, and any Connectors row
  are retired.
- **State-driven inbox chip with a sticky unlock** (owner: many users never park an item or use
  Unattended — a permanent Inbox row is dead chrome for them). Absent until the first item ever
  parks or Unattended is first enabled, then permanent: quiet icon when empty, accent + count
  when pending — §12's glanceability requirement, paid only when there's something to glance.
  Auth-independent (Inbox is local). **Two click targets:** the chip → Inbox directly; anywhere
  else on the row → the menu.
- **Menu (fixed):** email header · **Inbox** (with count) · **Connectors** · Settings (⌘,) ·
  Automations · Activity · Sign out. Inbox + Connectors are always listed — the permanent
  discoverable path regardless of chip state. **"Integrations" is renamed "Connectors"**
  everywhere (the findability complaint was the "& more" label; users think "connect Slack";
  MCP servers sit fine under the Connectors roof). "Inbox" keeps its name — "Approvals" was
  considered and rejected: the queue also holds questions, plan reviews, and folder-grant
  requests.
- **Activity dedup:** two unrelated pages were both named "Activity". The Integrations
  dead-letter page dissolves into **Messaging routing** as an "Unrouted" section (badge kept);
  the one remaining Activity = the audit log, in the account menu.
- **Automations** stays in the menu for now (owner: deserves a visible row, deferred). The
  Connectors-page sign-in strip retires — the account row supersedes it; connect-modal inline
  sign-in panes stay (§24's lazy trigger). Designed-not-built: Messaging routing keeps
  shrinking (§19/§21 moved per-connector cuts onto detail pages; the residual global table may
  later dissolve entirely).

> **Revision 2026-07-12 (§28):** Messaging routing did dissolve — the whole page (mirror
> channel, DM route, channel subscriptions, Unrouted with its ⚠ badge) moved to
> **Inbox ▸ Configure**; the Connectors sub-nav is now just Connectors · MCP servers.

## 27. Start screen: template tasks carry their own setup  *(Decided 2026-07-11 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-007-start-tasks.html`; ledger UX-007)*

Extends §22's start-screen half and composes with §23. The fresh-Cowork empty state becomes
**exactly three concrete template tasks** — flat hairline rows (the de-boxed grammar), staggered
~300ms entrance — and nothing else between the greeting and the composer:

1. **Analyze the files in a directory** — action "Pick a folder →": shares a folder (inline
   add-folder form; straight to prefill when one is already shared), then prefills the composer.
2. **Create a report from my HubSpot leads** — gated on HubSpot.
3. **Automate a weekly GitHub progress report to Slack** — gated on GitHub + Slack; funnels
   into §24/§25's recipe + consent machinery.

- **No leading icon tiles** — the title is the row. Connector dots sit on the **sub-line**:
  brand color = connected and enabled for this session, grayscale = not (§23's vocabulary).
- **Sub-line copy is always the task's outcome** ("Sources, stages, and who needs follow-up"),
  never connection state — the dots and the trailing action carry that.
- **Row action contract:** sources ready → "Start →" revealed on hover, click prefills the
  composer with the template stem. Not ready → **"Configure ›" always visible** (for a gated
  row the setup action IS the row's meaning) and it opens the §23 Session settings drawer —
  the start screen adds **no second setup surface**.
- **"Set me up (optional)" is removed** — setup rides the task that needs it.
- Rejected on the way (competitor comparison): boxed category tiles expanding into template
  lists (reintroduces boxes + a navigation level); a specialist-coworker picker line on the
  composer edge and a tip/picker under the tasks (placement + "X is on this session" copy).
  The specialist entry point is **deliberately absent** — owner sketch to come.
- Same day: the ✳ greeting/boot/gate mark (read as a competitor's logo) → **✦** app-wide.

## 28. One page shell; Inbox absorbs Messaging routing  *(Decided 2026-07-12 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-009-inbox-merge.html`; ledger UX-009)*

Owner walkthrough: Automations, Activity, and Inbox each had their own indentation and head
style; and "Messaging routing" hid inbox-delivery config under Connectors while the mirror
channel was editable in TWO places (that page's card + Inbox's inline configurator).

- **One page shell for every top-level page** — the Connectors/Activity pattern: full-bleed
  `main`, centered ≤4xl column, `PanelHead` (18px title, 12.5px muted subtitle BELOW it),
  card-based content. Page-level actions ("+ New automation") right-align with the head.
  Automations drops its icon-in-title and the boxed ⓘ banner (now a one-line muted note);
  Inbox drops its title+subtitle-on-one-line head. No page invents its own indentation again.
- **Inbox = two page-level tabs**, underline style — one visual level above the filter chips:
  - **Pending** (default): approvals/questions exactly as before (kind chips, persona chips,
    resolve-releases-agent). Badge = pending count. The routing status is a read-only line
    ("Also delivered to #ops-alerts — replies there resolve items here. Configure ›") whose
    link switches tabs; the inline editor is deleted — the mirror setting has ONE editor now.
  - **Configure**: the former Messaging-routing page moved whole — Unattended-approvals
    mirror + Direct-messages route (two-card row), Channel subscriptions, Unrouted. The ⚠
    unrouted count rides this tab. Rationale: Unrouted is "messages that never reached you" —
    the user asking "why didn't I get pinged?" looks in Inbox, not Connectors.
  - Tab name: owner offered "Destination"/"Configure" over the drafted "Delivery" —
    **Configure** won (honest umbrella for mixed settings; "Destination" names only the
    mirror card). Mirror target and routing line show the channel **name** when the recent
    list knows it (§24 revision 9's names-over-ids rule).
- **Connectors sub-nav shrinks to Connectors · MCP servers** (amends §26, which had already
  predicted the dissolution).

## 29. Onboarding: model → your tools → go; the recipe becomes the Automations quickstart  *(Decided 2026-07-12 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-010-onboarding-quickstart.html`; ledger UX-010)*

Restructures §24's step 2–3. The recipe step was the heaviest screen in the app — six decisions
and two third-party OAuth flows at minute one; nearly every friend-install failure lived on it —
and it duplicated the Automations page's template system.

- **Onboarding = three fixed-height pages, one decision each:**
  1. **Model** — unchanged (§24 + its revisions).
  2. **Sign in for one-click connections** *(amended 2026-07-16, owner design — resolves the
     copy iteration that was pending here)* — the VALUE is the headline and there is ONE
     primary action: the footer is the flow's standard pair — quiet "Skip for now" left,
     primary **Sign in** right, which becomes **Continue** once signed in. (The old page was
     headlined "Connect your tools" but offered no connecting, and its mid-page sign-in
     button competed with a footer Continue that did something else.) The security story is
     one titled card — **"Secure by design"**: broker framing ("OpenCoworker Cloud brokers
     the OAuth handshake — your tokens never leave this Mac") with the manual-keys path as
     the card's divider-set footnote. Real connector logos kept (ConnectorIcon set, never
     letter stand-ins); sign-in stays genuinely optional (the lazy first-connect sign-in
     remains for skippers); signed-in replay shows the ✓ state.
  3. **You're set up** — two CTAs: "Create your first automation" → the Automations
     quickstart; "Start working with Coworker" → fresh session with the session-settings
     panel open (§24's teach-by-landing kept). The gallery card + scope line stay hidden.
- **The Automations quickstart = ONE template system** (`AutomationQuickstart.tsx`): the role
  recipes (GitHub digest / Pipeline digest / Morning brief) merge into "Start from a template"
  beside the generic cards (news / inbox digest / folder cleanup — Inbox digest now names its
  Gmail dependency). Cards are equal-height and carry §27's connector-dot vocabulary (brand =
  connected, grayscale = not; "No connections needed" cards say so). Picking a card expands
  the configure card: connect rows (lazy cloud sign-in pane, GitHub link-existing escape
  hatch), channel-by-name, day × time, §25 consent for write recipes / read disclosure
  otherwise, and a named-gate Create button ("Connect X to continue" / "Pick a channel…").
  Shown on the empty state and alongside "+ New automation".
- **Decided along the way:** the §27 start-screen GitHub→Slack row keeps its prefill contract
  (the agent path stays first-class); the quickstart is the click-path twin.

## 30. Quickstart connect polish: configure header, honest connect states, branded loopback pages  *(Decided 2026-07-13 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-011-quickstart-connect-polish.html`; ledger UX-011)*

Three fixes from the owner's DMG #10 walkthrough of the §29 quickstart.

- **The configure card opens with a header** — kicker `SET UP` + the template's title + a
  cadence echo ("Connections, delivery & schedule · Weekly") as one bordered row inside the
  card — so it visibly belongs to the picked template instead of starting abruptly after the
  grid. Picking a template also scrolls the card into view.
- **Connect gets honest states, inline (owner offered a modal; inline won** — the connect
  completes out-of-band and the page isn't actually blocked, so the row narrates itself):
  `Connect` → `⟳ Opening browser…` (the broker POST is in flight; this covers the 4–5 s of
  dead air) → `⟳ Waiting for <Tool>…` plus a handoff strip under the row ("Finish connecting
  <Tool> in your browser. Approve it there, then come back — this page updates by itself."
  with **Cancel**). Cancel clears only the local waiting state — the browser tab is the
  user's to close; the existing poll flips the row to ✓. The same states apply to
  "Sign in to OpenCoworker Cloud" in the quickstart's lazy pane and on onboarding page 2.
- **The loopback pages become one branded card** (`_browser_page` in the sidecar): OCW mark,
  ok/fail icon (the connector's badge rides the ✓), Title-cased connector names ("Slack
  connected", never "slack connected"), "You can close this tab and return to OpenCoworker",
  the error detail preserved on failures (it's the debugging breadcrumb), and a "Served
  locally by OpenCoworker on your Mac" footer. Inline CSS, light/dark via
  `prefers-color-scheme`, zero external assets — the page must render offline.
- **Out of scope (broker slice, tracked separately):** the "Already installed on GitHub?
  Link it ›" escape hatch and the GitHub install-page dead-end — properly fixed by
  authorize-first connect + a `GET /me/connections` restore in ocw-connect.

## 31. Slack mention router: @ocw spawns a thread-scoped coworker  *(Decided 2026-07-13 by owner; ledger UX-013)*

Mentions are the PRIMARY Slack entry point ("very few people will do a subscribe inside an
agent" — owner). Before this, an @ocw tag in a channel with no subscribed session was silently
dropped.

- **Tag in an unsubscribed channel → a NEW coworker session per THREAD**, replying into the
  thread (a top-level tag threads on its own message, Slack-style). Follow-up tags in the same
  thread STEER the same session — dedupe keys on the thread target
  (`slack:T…/C…:thread_ts`), persisted in `mention_threads.json`. A deleted session releases
  its threads; the next tag spawns fresh.
- **The thread is pre-approved, nothing else is:** the spawned session carries a standing
  `send_message` grant (§25 shape, exact-target match) for its origin thread only — the
  conversation never stalls on an approval nobody in Slack can see. Any other action asks as
  usual (approvals park to the Inbox, §28). The grant is re-derived from the durable map on
  every engine rebuild, so it survives restarts.
- **A user-connected coworker overrides the router:** a subscribed session gets the tag with
  must-respond-in-thread framing; the router spawns nothing there.
- **Chattiness tiers:** tagged → must respond, in the thread · untagged channel traffic →
  judgement-only, "stay silent unless a reply adds real value" · the allow-list still gates
  everything upstream (unauthorized senders park, never spawn).
- **Sidebar (revised 2026-07-21 by owner; original band decision 2026-07-13):** mention-spawned
  sessions carry `origin`/`origin_label` and list **chronologically in Recent / the persona
  lists like any other session**, wearing the Slack logo right-aligned in the row's indicator
  cluster (`origin_label` as its tooltip). The earlier collapsed cross-persona **"From Slack
  (N)"** band is REMOVED — it hid fresh mentions below week-old sessions and read as clutter.
  **No auto-archive** — deferred to a future global settings page (default vs Slack session
  policies, owner call 2026-07-13).
- `↪ Refinement (owner, 2026-07-14):` **titles put the ASK first** — "{ask, 48 chars} —
  #channel", mention token stripped. The old "#general — <@UBOT> …" prefix made every
  mention session truncate identically in the sidebar; the ask is what varies, so it gets
  the budget, and origin is already told three ways (group, icon, origin_label).

## 32. One session panel: the rail absorbs the Sources drawer  *(Decided 2026-07-13 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-014-session-panel.html`; ledger UX-014)*

Progress (doing) · Artifacts (made) · Access (may touch) are three views of ONE session, so they
share one panel and one entry — the existing panel toggle. Retires §23's rest/hover/click row,
its glance machinery, and the overlay drawer.

- **The Access section** is the former Session-settings drawer recut for rail width: Sources
  with per-session mute toggles (and the two-way connectors' channels drill-down as an inline
  child view), Recommended with connect-in-context, Folders (roots + RO/RW gate + branch), and
  the global-connectors link. No second overlay, ever.
- **The header carries the trust glance permanently** — "Access · Slack, GitHub · 2 folders"
  (first two live source names +N, then the folder fact; project-scoped sessions name the
  folder). Ships collapsed; nudge TEXT still never renders at rest (§23's rule, carried over).
- **The rail renders for every non-chat persona** (code/ops had the drawer but no rail).
  Sections per family — cowork: Progress · Artifacts · Access; code-family: Progress · Access.
  `↪ Agreed follow-up (owner, 2026-07-13, NOT built):` code-family later gets **Files** in the
  middle slot instead of Artifacts — a changed-files view (PR-style ±), repo-native where
  Artifacts is deliverable-native.
- **Deep links:** the intro's "Configure ›" and onboarding's "Start working" open the rail with
  Access expanded (scrolled into view). The topbar nets minus one icon.
- `↪ Addendum (owner ask, same day):` **"+ Add a source…"** — the catalog's long tail,
  in-session. A quiet row under Sources becomes a typeahead over the full connector catalog
  (available + not already connected, capped at 6); picking one enters the SAME
  connect-in-context child view, and a source added from a session is also enabled for that
  session on connect. Search-only, never a browsable list — browsing stays on the global
  Connectors page. The child view states the scope rule once: connecting is account-level,
  the toggle is per-session.
- `↪ Refinement (owner ask, same day):` **Folders mirrors Sources** — the old drawer's card
  wrapper and dashed add-button read too heavy in the rail. Folder rows sit flat under the
  FOLDERS header (same left edge and rhythm as source rows) and the add affordance is a quiet
  "+ Give access to a folder…" link, twin to "+ Add a source…", expanding the same inline
  RO/RW-gated form in place. The whole section scans as one repeated pattern: uppercase
  header → flat rows → quiet "+" line. Collapsed rail sections also equalized (headers share
  a 24px min-height so Artifacts' action buttons no longer make it the tall one).

## 33. Tool calls read as English; the TURN is the group  *(Decided 2026-07-13 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-015-tool-call-lines.html`; ledger UX-015)*

The old step group was raw plumbing: mono tool names + args + JSON result boxes, a separate
teal box per resolved approval, and an approval count advertised on the collapsed line.
Replaced wholesale — three layers, all shipped together:

- **One English line per tool call, synthesized client-side** (`humanize.ts`): the model does
  NOT emit a purpose per call, so per-tool templates turn name+args into a sentence — "Sent a
  Slack message to …", "Updated the plan — '…' → done", "Read runbook.md". `run_shell` is the
  exception and the model DOES speak there: its `description` argument (model-written intent,
  §25 lineage) is preferred — "Ran `git log …` — list yesterday's merges". Unknown/long-tail
  tools fall back to "Used *tool* — *short args*".
- **Approvals fold into their tool's row as a chip** — ✓ approved / auto-allowed (standing
  rule; tooltip names the automation) / ✕ declined. A declined ask has no executed call, so it
  keeps its own row, phrased as intent: "Wanted to run `rm -rf build/` — ✕ declined". No
  separate approval boxes, and no approval count on the collapsed line: only a DECLINE and the
  privacy note ("N hidden by your filters") may surface at rest.
- **Narration + turn-level grouping:** the engine prompt (all personas) asks for ONE short
  status line before each batch of tool calls. Grouping therefore moves up a level — the whole
  user-message → final-answer span collapses as one turn ("N steps"), with quiet narration
  lines (assistant text followed by more activity in the same turn — pure client-side
  classification) interleaved between humanized step rows. The final answer stays a normal
  bubble OUTSIDE the disclosure. A running turn is open and live (spinner on the current
  step, "Running N steps…"), and collapses when the answer lands unless the user pinned it
  open. Non-narrating models degrade to a turn with no quiet lines.
- **Raw is demoted, not deleted:** hover a row → "raw" → verbatim `name + args → result`
  block, for debugging.
- `↪ Refinement (owner report, same day — the intermediate-bubble flicker):` while the session
  is LIVE, the final run's trailing assistant text is still narration, never promoted to an
  answer bubble — each status line was flashing as a full ASSISTANT bubble and then vanishing
  into the group when the next tool call arrived. The answer bubble now appears exactly once,
  when the turn ends (Transcript takes the session's `running` flag). Streamed text mid-turn
  renders as the same quiet line (no ASSISTANT chrome); a stream straight after the user's
  message keeps the bubble (plain reply). PENDING approvals/questions no longer split the run
  (they render in the composer, not the transcript), so waiting on a decision doesn't
  reshuffle the story. A collapsed live turn carries its latest narration on the header —
  "Running 10 steps… · *I'm creating a generator that embeds…*" — so the pulse never hides.
- `↪ Refinement #2 (owner, 2026-07-14 — the stream gate):` turn-START streams are HELD
  (spinner shows) until they either get a tool call — narration; renders complete as the
  quiet line, a bubble never appears — or cross **40 words** with no tool call — the answer;
  the bubble starts streaming from that point. A message that ends under the threshold with
  no tools shows its bubble at completion (~a second late, owner-accepted: "people can wait
  1-2 seconds longer").
- `↪ Refinement #3 (owner, same day — #2 only covered turn start):` mid-turn narration still
  painted as a floating full-size paragraph (a `.md` CSS override compounded it). ONE rule
  now for all streamed text: under 40 words it belongs to the LIVE turn group — on the
  collapsed header ("Running 12 steps… · checking the historical pages…") or as the small
  quiet line when expanded — and 40+ promotes to the streaming answer bubble, start and
  mid-turn alike. **Turns also start COLLAPSED while running** (owner call): the header's
  live line is the pulse; expanding is opt-in. Residual unchanged: 40+ words of narration
  degrades to a bubble.
- `↪ Later (not built):` turn duration in the header ("Worked for 1m 04s") once transcript
  messages carry timestamps; an optional model-written `purpose` on more tools if the
  fallback lines feel dry.

## 34. Artifacts come to the user  *(Decided 2026-07-14 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-016-artifact-delivery.html`; ledger UX-016)*

Finishing a task must not strand the deliverable behind the Artifacts panel — the agent hands
it over, in the GUI and in Slack.

- **`artifact:` links (GUI):** the agent ends a deliverable turn with plain markdown —
  `[Title](artifact:relative/path)` — and the renderer shows an artifact CHIP (icon, title,
  filename, "Open ›") that opens the existing rail viewer in place (un-hiding the rail if
  needed; path resolved against the session's artifact list, one refresh on a miss).
  Rejected: a custom `<ocw:artifact/>` tag (models mangle XML; other surfaces show it raw)
  and a `show_artifact()` tool (extra round-trip, forgotten, imperative-UI-on-replay).
  Prompted in the Cowork instructions; the scheme survives the markdown sanitizer via an
  explicit urlTransform carve-out.
- **`send_file` tool (Slack):** uploads a workspace file into the thread/channel
  (`files_upload_v2`). Slack renders its own previews for pdf/csv/images — send the FILE,
  not a thumbnail. Same target grammar and token resolution as send_message (desktop-direct
  even on the managed relay — the per-team bot token is local), but a DIFFERENT tool name:
  a mention-thread's standing `send_message` grant never covers uploads (task_rules key on
  the tool name; pinned by test). Paths must resolve inside the session's workspace/roots;
  50 MB cap.
- **HTML → screenshot:** the one format Slack can't preview. `send_file(as_screenshot=true)`
  renders the local page headless (the Playwright we already ship, 1280×800 viewport) and
  uploads `<stem>.png` instead. HTML-only by design.
- **Scopes:** managed bot scopes gain `files:write` (broker providers.py — new connects
  request it, existing workspaces re-consent on their next Connect); the manual Socket-Mode
  instructions list it; the hosted app's Bot Token Scopes must match (dashboard).
- `↪ Queued (owner):` artifact SHARING — public / select emails, behind OpenCoworker Cloud
  sign-in. Separate decision; discussion next.

## 35. Approval cards speak the transcript's language  *(Decided 2026-07-14 by owner; mock: `ocw-context/docs/ux-improvements/mocks/UX-018-approval-card.html` (v3); ledger UX-018)*

The card shouted "PERMISSION REQUIRED" over a mono tool chip and a raw args dump while
§33/§34 made everything around it read as English. Replaced:

- **Humanized headline** (`humanizeApprovalTitle`): "Write `fetch_data.py`", "Send a file to
  **#general**", "Run a command — fetch semiconductor stock data" (run_shell leads with the
  model's own description). The tool name demotes to the shield icon's hover.
- **Risk-tiered density:** routine workspace writes (write/edit/patch) render as a compact
  ROW — title, inline `preview ▾`, Allow / Always allow / quiet Deny. Everything else is a
  full card; actions that LEAVE the Mac (send_message/send_file/connector tools) wear a warm
  border and an explicit scope note ("leaves this Mac → Slack") where the "local action"
  badge used to sit. Locals say "stays on this Mac" (+ "overwrites the existing file" when
  true). §25 consent cards (create_scheduled_task grants) always keep the full card.
- **Previews are the PROPOSAL, from the tool call's args** — the file/action doesn't exist
  yet, so no viewer could show it (the code-family "Files" view is post-hoc; §32 follow-up).
  File writes: first 5 lines + "show all N lines". Shell: the command block. Sends: the
  rendered message / file chip. Long-tail tools keep the compact args line.
- **Buttons:** no solid fills — the primary action is a blue border. Workspace-local
  "Always allow" stays short (the honest rule — "always allow X for this session" — on
  hover). run_shell offers ONLY the command-scoped "Always allow this command" (two adjacent
  always-buttons blur scope — §25's own argument). "Allow every time" (run contexts, §25)
  unchanged. Deny is quiet text; red only on hover.
- `↪ Deviation from mock v2 (honesty over label):` the external card's "Always allow sending
  files to #general" is NOT built — outside run contexts the only mintable grant is
  session+tool-wide, and a target-named label would overpromise. External cards show the
  short "Always allow" with the honest hover. Target-scoped standing rules for plain
  sessions = future permissions work if wanted.
- `↪ Addendum (owner catch, same day):` **parked approvals wear the same dress.** A reopened
  session renders its pending approval from the Inbox (InboxItemCard), which still spoke the
  old dialect — "APPROVAL / Run `browser_read_url`? / solid-blue Approve". Parked items now
  always carry `tool` + `arguments` in their data, and the card reuses the §35 pieces
  (humanized title, scope note, content/command preview, blue-border "Allow once", quiet
  Deny). Resolution vocabulary unchanged (works on every approver path); pre-§35 rows
  without tool data keep the legacy treatment.

## 36. Connector reads never gate; channel names just work  *(Decided 2026-07-14 by owner; no mock — behavior, not layout)*

Two trust-model fixes from live use, one principle: connecting a service IS the consent for
reading it; writes are where blast radius lives.

- **Reads never gate.** The tool registry's read/write kind (`tool_defs.py`) is now LAW for
  every registered connector tool: reads (`github_search`, `gmail_search_messages`,
  `browser_read_url`, `browser_open_url`, …) carry `requires_approval=False`; writes
  (`gmail_send_email`, `github_create_issue`, `discord_send_message`, …) always gate. The
  §25 design note ("reads never gate, a rule would be meaningless") had never actually been
  wired — connector tools inherited the conservative-MCP default, so a weekly GitHub digest
  paused THREE times for a human to approve *searching GitHub*, while the quickstart copy
  promised "reading never needs approval". Call-site flags now govern only tools without a
  registry entry (MCP/experimental stay conservative). Debatable-and-decided: registry
  classifies `browser_open_url` and `github_clone/pull` as reads — they ride the rule.
- **"Post Hi to #general" works.** `send_message`/`send_file` accept a Slack channel NAME
  (lowercase or `#`-prefixed — Slack names are strictly lowercase, ids are uppercase
  `C…/T…/…`, so the shapes never collide) and resolve it through the same cached
  `conversations.list` roster the GUI's channel picker uses: exactly one match across
  connected workspaces → team-qualified address → the right per-team token. None/many/
  not-a-member return actionable errors ("invite @ocw to #private-ops in Slack, then
  retry") instead of the misleading "no bot token for slack" the owner hit — that error
  came from the token resolver falling back to the relay's marker profile when a bare name
  carried no team prefix. `not_in_channel` from Slack maps to the invite hint too.
- `↪ Open question (owner, parked):` platform-specific tools (`send_slack_message`) instead
  of the generic `send_message`? Revisit.

## 37. Voice input is installed, checked, and tested in Settings before the composer enables it  *(Decided 2026-07-15 by owner → Built 2026-07-15; mocks: `ui-mocks/voice-input-settings.html`, `ui-mocks/voice-input-composer-states.html`)*

Pressing a microphone is an intent to speak, not consent to begin a 142 MiB download. Voice input
therefore has an explicit setup home and a visible readiness contract before it enters the composer.

- **One setup surface:** Settings gains **Voice input** as a first-class section (not under Models —
  provider/model selection and a local input capability are different mental models). It explains
  that transcription stays on-device, names the installed engine/model, shows disk use, and owns
  Download / Cancel / Repair / Delete / Test again. The composer never downloads a model.
- **Honest state machine:** `unsupported → not installed → downloading → verifying → ready`, with
  `needs microphone permission`, `test required`, and `error` as actionable substates. Download
  progress shows transferred/total bytes; completion verifies the expected size + SHA-256 before
  the partial file is promoted. A failed/interrupted download stays non-ready and can be restarted
  (transfers begin from zero; a resumable Range request is a possible later refinement).
- **Test before enablement:** after installation, Settings asks the user to record one short phrase,
  displays the local transcript, and marks Voice input Ready only after a successful non-empty test.
  The test is also where macOS/Windows microphone consent is requested. Readiness is invalidated if
  the model disappears/fails verification or microphone permission is revoked; users can Test again.
- **Composer contract:** until Ready, the mic is visually muted and exposes **“Configure Voice Input
  in Settings”** on hover/focus. It remains keyboard-focusable (`aria-disabled`, not a dead native
  disabled button); activation deep-links to Settings ▸ Voice input. Once Ready it is a normal
  click-to-record / click-to-stop control and inserts an editable transcript — never auto-sends.
- **Four composer states:** (1) **Ready at rest** — enabled mic in the normal control row; (2)
  **Listening** — clicking the mic replaces the quiet middle controls with a live waveform, elapsed
  time, and an unambiguous square Stop control; attachment stays available; (3) **Transcribing** —
  a deliberately lightweight, often-fleeting state after Stop, with provisional text in the draft,
  a busy mic treatment, and Send protected until finalization; (4) **Draft inserted** — the mic and
  normal controls return, the transcript is ordinary editable composer text, and the user decides
  whether to edit or send. The waveform is decorative (`aria-hidden`); state changes are announced
  through one polite live region for assistive technology.
- **Model control invariant still applies (§17/§22):** the four-state mock depicts an existing
  conversation, so it intentionally has no model name/picker in the composer. The picker appears
  only before the first turn; afterward the fixed model is a header fact, never a composer control.
- **Hard compatibility gate = shipped binaries, not aspirational library support.** Initial support:
  Apple Silicon Mac (M1+) on macOS 12+; x64 PC on Windows 10 22H2 or Windows 11. The native layer
  reports OS + architecture and the Settings page shows the failed requirement in plain language.
  Intel Mac and Windows ARM remain unsupported until the release pipeline produces and tests those
  artifacts. RAM/core count are performance signals, not deterministic blocks: show **8 GB RAM and
  4 CPU cores recommended** plus “transcription may be slower” when below them, then let the test be
  the truth.
- **Recovery and storage:** the card always shows installed size without exposing a noisy filesystem
  path. Delete model reclaims the space and returns to Not installed; Repair re-downloads and
  verifies; app upgrades do not silently replace a working model.
- `↪ Supersedes the first STT cut:` the composer currently downloads `ggml-base.en.bin` on first mic
  click. That behavior is deliberately removed; the reusable local STT library and editable-draft
  behavior stay.

## 38. Pre-connect connector detail page: About, honest Access bullets, tools behind a disclosure  *(Decided 2026-07-18 by owner; no mock — extends §21's grammar)*

Prompted by competitor plugin-gallery pages: before any credentials exist, an **available**
connector's row navigates to a detail subpage (same `‹ Connectors` breadcrumb as §21) so the user
can see what they're granting before the vendor's consent screen does. The list's Connect pill
remains the fast path straight into the add-modal.

- **Content, top to bottom:** header (badge / title / blurb subtitle / Connect pill) → optional
  About paragraph → **Access** group → collapsed **Tools** disclosure. Copy lives server-side
  (`catalog_copy.py`, served on `/v1/connectors`) so all surfaces share it.
- **Access bullets are honest or absent:** short statements of actual behavior — write powers name
  themselves ("Sends email as you"), reads state their boundary ("…your account can see"), negative
  guarantees are explicit ("Never deletes mail"). Every bullet must stay true to the connector's
  real tools and, for managed connectors, the cloud app's scopes — overclaiming here is a product
  bug (test-enforced: every available connector ships curated bullets). A standing footnote carries
  the platform guarantee: "Keys and tokens are stored only on this computer."
- **Tools are a collapsed "N tools this connector adds" disclosure** — advanced-reader detail, no
  enable/disable pre-connect (that lever exists on the connected page, §21); write tools carry the
  "asks first" tag. Owner: listing is enough, toggles are not needed here.
- **No third-party-risk interstitial, deliberately:** competitor consent modals ("apps may introduce
  elevated risk", data-sharing disclosures) exist because their plugins are third-party code
  receiving conversation context. Our catalog is first-party tools with local-only tokens — copying
  that fear-language would import a risk we don't have. The Access section IS our disclosure.
- **Connect completes in place:** the poll flips `connected` and the same route re-renders as the
  connected detail page — no navigation cliff after OAuth returns.

## 39. Onboarding galleries: provider cards, then a two-state tools page  *(Decided 2026-07-18 by owner → Built 2026-07-18; mocks: `ocw-context/docs/ux-improvements/mocks/UX-019v2-onboarding-provider-gallery.html`, `UX-020-onboarding-tools-page.html`; ledger UX-019/UX-020)*

Owner: step 1 was "bland, tasteless and non-informing." Both onboarding steps become card
galleries that carry the product's breadth on their face, under one frame rule: **the header
and footer never move — only the middle region swaps, at a fixed height** (extends the
2026-07-12 no-resize rule to in-step view changes).

- **Step 1 = provider gallery.** All 13 providers as 2-per-row cards with their official brand
  marks (vendored from MIT-licensed lobe-icons into `src/providers/logos/`, always on a light
  chip so multicolor marks survive dark mode). Every card wears its own state — ✓ Connected /
  Not set up / No key needed — so multi-provider state costs zero clicks (closes UX-019's
  question). Recognition-first order; the long tail scrolls.
- **Card → key form, in place.** Breadcrumb + identity row, then the provider's real fields.
  **A passing Test verifies, SAVES, and auto-returns** to the gallery (~0.9 s, after the
  in-field confirmation registers) — the "extra click back" never exists on the happy path.
  State lives IN the field: green border + "✓ Tested & saved" pill (revisits show the same;
  typing clears it); **no status lines below the form**. `base_url` on a keyed provider is a
  quiet "Custom endpoint ⌄" disclosure with **no explainer copy** (its users know what it's
  for). Ollama renders endpoint + **Detect**; a pass counts toward Next (configured-but-
  unproven keyless providers don't).
- **Footer: plain "Next"** (never "Continue with X" — configuring a provider isn't choosing a
  model; §17's per-session choice happens later). Next arms at ≥1 ready provider; from a dirty
  form it auto-verifies+saves first (preserves the 2026-07-12 no-hidden-two-step rule).
  **No accent-blue links anywhere** — crumbs/disclosures/help links are muted ink with quiet
  underlines (owner: blue links are "outdated design").
- **Step 2 = two-state tools page.** Headline "Connect your everyday tools" + the why
  paragraph ("A coworker that can only chat can only advise…"). Pre-sign-in: tool-logo row,
  "One click, keys handled" card, skip renamed **"Skip — I'll use my own API tokens"**, privacy
  line at the account-ask moment. Post-sign-in the SAME region becomes a **mini connector
  gallery** — the five managed connectors with live prod OAuth apps (Outlook · Slack · GitHub ·
  Notion · HubSpot); a card click launches the real one-click consent ("Check your browser…" →
  poll → ✓). This keeps the headline's promise on-page, resolving §29's
  promise-with-no-action concern. **Gmail + Google Calendar ship grayed "Coming soon"**
  (both gated on Google verification/CASA — flip `TOOLS_SOON` when it lands). One pending
  connect at a time (a second click quietly resets the first); failures reset silently — no
  error walls in onboarding; **Next stays armed throughout** (connecting zero tools is fine);
  HubSpot connects read-only here (least privilege; write is a Connectors-page consent).
  AWS Bedrock / Azure deferred: provider-layer auth work, and Azure OpenAI is already
  reachable via OpenAI's custom endpoint.

## 40. Settings: three tabs, and Models is the shared provider gallery  *(Decided 2026-07-19 by owner → Built 2026-07-19; mock: `ocw-context/docs/ux-improvements/mocks/UX-021-settings-redesign.html`; ledger UX-021)*

- **Three tabs — General · Models · Voice input.** "Appearance" was carrying seven cards, most
  of them not appearance; it renames to **General** (the tab *key* stays `appearance` so
  deep-links keep working). **Files folds into General as one card** — a single option doesn't
  earn a nav entry. **Personas is launch-flagged off** (`flags.ts` `showPersonas()`, default
  false; the e2e suite re-enables via localStorage to keep the hidden flows covered).
- **Models = the §39 gallery, shared for real.** The provider gallery ⇄ key form was extracted
  to `providers/ProviderSetup.tsx` (hook + `ProviderCards`/`ProviderForm`); Onboarding step 1
  and Settings ▸ Models render the SAME components with different frames, so the two surfaces
  cannot drift. Settings passes testid prefix `set-`, onboarding keeps `ob-`.
- **Settings-only additions:** configured cards carry "✓ Connected · used Nh ago" (truncating,
  never wrapping); the form of a credentialed provider gains a quiet red **"Remove key…"**
  (confirm → `DELETE /v1/providers/{name}` forgets the stored profile; the card reverts to
  "Not set up"; curated models stay, they just gray out).
- **Below the gallery:** the **"In the composer's picker"** card lists every curated model
  across providers with provider tags + the default badge (untick removes; adding happens from
  a provider's card, whose form view keeps the per-provider ModelChecklist or, unconfigured,
  the read-only "Included models" preview). **Token savings moved here from Appearance** —
  it's model-spend behavior. The gallery⇄form swap happens in place and the page scrolls; the
  fixed-height rule is onboarding-modal-specific.
- The old dropdown provider picker, the API/Local sub-tabs, and the separate Save button are
  retired — Test = verify + save + slide home, exactly as in onboarding.

## 41. Onboarding tools page: benefit rows, a band that never moves  *(Decided 2026-07-19 by owner → Built 2026-07-19; mocks: `ocw-context/docs/ux-improvements/mocks/UX-022b-tools-page-redesign.html` (3 directions), `UX-022c-benefit-rows-connect.html` (chosen); ledger UX-022)*

- **Benefit rows replace the card gallery** (supersedes §39's step-2 body; frame rules stand).
  Six rows — *Stay on top of email (Outlook) · Keep up with Slack · Ship code (GitHub) · Keep
  your notes in reach (Notion) · Keep the CRM current (HubSpot) · Track every relationship
  (Attio)* — benefit first, tool named in the ONE-LINE detail (wrap made row heights jump
  between states). Rows scroll past the fold; accepted ("let users scroll"). The gated Google
  pair is ONE combined grayed row with a static "Coming soon" — hover-only labels read as
  broken cards.
- **Zero layout shift at sign-in.** The band below the rows is pinned outside the scroll area
  and its slot never moves: pre-sign-in it carries the ask ("Sign in for one-click connections —
  OpenWorker handles the OAuth for 20+ tools… Tokens stay on this Mac") with the page's ONE
  black button; after sign-in the same slot turns green-congrats ("🎉 You're signed in as … —
  connect a tool above with one click, or add them anytime later"), and every row grows a quiet
  bordered **Connect** pill in place (Connect → "Check your browser…" → ✓ Connected; one in
  flight, silent resets — §39's connect rules carry over).
- **One footer button, one slot:** quiet bordered "Continue without sign-in" pre-sign-in →
  black "Next" after. The left "Skip" link is gone. The footnote is STATIC across states
  ("30+ more tools on the Connectors page — add or remove anytime. Tokens stay on this Mac"),
  so nothing below the band ever changes.
- **Modal is 560px** (down from 700) across all steps — the §39 fixed-frame rule holds; the
  provider gallery scrolls ~4.5 rows and the form views stop floating over a void. On step 1,
  **"Custom endpoint ⌄" renders below the key-help line** as its own advanced row (both
  surfaces — the disclosure lives in the shared ProviderForm).

## 42. Connectors are backing-agnostic: hosted-MCP connectors with a pinned tool allowlist  *(Decided 2026-07-19 by owner → Built 2026-07-19; no mock — extends §21's grammar; monday.com / Asana / Jira first)*

The user's decision: "leverage MCP+OAuth wherever possible, but still show it in the
Connectors section — from a UX perspective it shouldn't matter whether the connector is an
app or MCP." A connector is a product concept (card, copy, consent, approval posture); the
backing (broker OAuth app, vendor-hosted MCP server, manual token, relay) is plumbing.

- **MCP-backed connector** = descriptor with `mcp_url` + a **pinned allowlist** of tools in
  `tool_defs` (names `mcp__<connector>__<vendor tool>`). The pin is a whitelist that FAILS
  CLOSED: only pinned tools ever reach a session or the Access bullets — the vendor shipping
  new tools cannot silently expand agent capability (the owner's drift worry). Missing pins
  degrade; unknown tools never load (`include_tools` on the seeded server config, re-derived
  from the pin at session build so stale config can't widen it).
- **Connect is one click and FULLY LOCAL**: the sidecar runs the MCP OAuth 2.1 flow (DCR —
  no client secret, no broker, no OpenWorker sign-in). The modal says so ("no OpenWorker
  account needed; sign-in runs entirely on this computer"). Connectors with a manual path
  too (jira, asana) keep the §21 One click | Manual pills; MCP-only connectors (monday)
  render the one-click pane directly. The profile's `mode: "mcp"` decides which tool set is
  live — MCP pin vs the manual REST tools — one at a time, never both.
- **Per-tool approval follows the pin's read/write classification** (reads run free, writes
  ask first), not the MCP layer's server-level flag; unclassified defaults to ask. Session
  gating matches connector tools exactly: effective-connector set (§4.3) + per-tool toggles.
- **Placement**: MCP-backed connectors are cards on the Connectors page with §38 detail
  pages; they are HIDDEN from the Settings ▸ MCP tab (that tab stays for arbitrary/community
  servers). Disconnect forgets tokens + DCR registration + the seeded config.
- First wave: **monday.com** (`mcp.monday.com/mcp`, MCP-only) and **Jira** via the
  Atlassian server (`mcp.atlassian.com/v1/mcp`, + manual API token) — both accept DCR, so
  no vendor app registrations, no review queues, no broker changes. **Asana was pulled
  from the wave on 2026-07-20** (live test): their V2 server rejects DCR and requires a
  pre-registered "MCP app" with an exact-match redirect URI, which the desktop's
  dynamically-chosen sidecar port can't satisfy — one-click returns via a broker-routed
  callback later; its pinned defs sit dormant and the manual PAT path stays.

## Change log (requests, newest first)

- **2026-07-19 (26)** — Owner: "leverage MCP+OAuth wherever possible but still add it in the
  Connectors section — UX-wise it shouldn't matter if the connector is an App or MCP"; and
  "selectively allow only certain tool calls from the MCP connectors" (drift worry: vendors
  changing their exposed functions) → §42 (pinned-allowlist MCP-backed connectors; monday.com
  + Asana + Jira built same day). Tool-def scaling (caching audit, deferred tools + search)
  noted on the launch punchlist for a later discussion.
- **2026-07-19 (25)** — Owner critique of the built §39 flow (from DMG walkthrough screenshots):
  hover-only Coming soon, scattered grayed cards, blue-vs-black primaries, 700px void, key
  state-restore bug, endpoint placement → fixes + full step-2 redesign through three mock
  directions and two refinement rounds (his idea: rows grow Connect buttons; band stays with
  congrats content) → §41 (mocked UX-022b/c; built 2026-07-19).
- **2026-07-19 (24)** — Owner: "Settings is quite a mess visually" (token savings under
  Appearance, one-option Files tab) + "Models should follow the onboarding look" + hide
  Personas behind a flag + collapsed-nav overlap fix → §40 (mocked UX-021; built 2026-07-19).
- **2026-07-18 (23)** — Owner: onboarding step 1 "bland, tasteless and non-informing" →
  provider gallery; three review iterations (fixed frame; in-field saved state + endpoint
  disclosure + "Next"; de-blued links); then step 2's why-paragraph + post-sign-in connector
  gallery ("It's tempting!") → §39 (mocked UX-019v2/UX-020; built 2026-07-18).
- **2026-07-18 (22)** — Owner (from competitor plugin-gallery screenshots): ship a pre-connect
  connector detail page with launch — full details up front, tool list behind a link ("a detail
  that only the advanced user will care" about), no per-tool toggles → §38.
- **2026-07-15 (21)** — Owner: no download at the moment of use; put Voice input setup,
  compatibility requirements, visible model progress, verification, and a microphone transcript
  test in Settings; keep the composer mic muted with a Settings deep-link until ready → §37
  (mocked in `ui-mocks/voice-input-settings.html` and `ui-mocks/voice-input-composer-states.html`;
  built 2026-07-15). Owner follow-up added the four composer states from ready → listening waveform →
  transcribing → editable draft.
- **2026-07-14 (20)** — Owner ("make connector reads free" + the failed "post Hi to
  #all-opencoworker" repro): registry-kind-driven approvals + Slack channel-name resolution
  → §36.
- **2026-07-14 (19)** — Owner: approval box "needs to be more modern" (+ stream-gate ask,
  §33 ref #2) → §35 (drafted UX-018, mock v1→v3 with owner review — no row icon, short
  Always allow, blue-border buttons, inline preview state — graduated same day).
- **2026-07-14 (18)** — Owner: "should we give it a way to bring up the artifact?" + Slack
  thumbnails → §34 (drafted UX-016, built P1/P2/P3 same day; markdown-link scheme over
  tag/tool; post-the-file over thumbnails; HTML screenshot via the shipped Playwright).
- **2026-07-13 (17)** — Owner (DMG screenshots + the Codex-transcript discussion): tool calls
  as English one-liners, approvals folded into the row, no approval count at rest; narration
  folded in after the "do you inject a purpose?" exchange → §33 (drafted UX-015, mock v2,
  graduated same day).
- **2026-07-13 (16)** — Owner: merge the Sources drawer into the Progress+Artifacts rail and
  drop the topbar settings icon → §32 (drafted UX-014, graduated same day; §23 retired with an
  amendment note; code-family rail = Progress · Access, "Files" replacing Artifacts recorded as
  the agreed follow-up).
- **2026-07-13 (15)** — Owner (Slack notes, discussed then planned): @ocw tags spawn
  thread-scoped coworkers replying in-thread; connected coworkers override; silence-default
  chattiness; collapsed "From Slack" sidebar group with a right-aligned platform icon → §31.
- **2026-07-13 (14)** — Owner (DMG #10 walkthrough, five findings on the Automations page):
  configure-card demarcation, connect-wait feedback, and beautiful loopback pages → §30
  (drafted UX-011, inline states over a modal, graduated same day). The two
  already-connected-detection findings became the broker slice (authorize-first +
  `GET /me/connections`), not a GUI spec item.
- **2026-07-12 (13)** — Owner: sign-in as onboarding page 2, CTA-only page 3, recipe out of
  onboarding ("Automations quickstart or onboarding?") → §29 (drafted UX-010, mock v2 with
  owner's manual-keys line + equal-height cards + real-logos rule; graduated same day).
- **2026-07-12 (12)** — Friend-install bug-report doc (9 items, via owner): §24 revisions —
  Continue on the model step verifies AUTOMATICALLY (fills-gated, "Checking…", error stays;
  Test becomes an optional explicit check) and the recipe's gated Create-automation button
  names its missing piece ("Pick a channel to post to first"). §3 revision — the drawer's
  persona why-connect pane is HIDDEN for now (no multiple-persona mentions until personas
  relaunch; `SHOW_PERSONA_PANE` flag restores it). Plus non-spec fixes: agent folder-scope
  guardrail + Info.plist usage strings (macOS permission prompts), the double-ask approval
  flash, artifact Copy-path copies the absolute path. See bugfix-ledger 2026-07-12.
- **2026-07-12 (11)** — Owner (walkthrough, simplification pass on onboarding → §24 revision):
  key fields lose the "Stored locally (0600)" help line (manual territory, removed at the
  provider registry so Settings ▸ Models loses it too); OpenAI's optional endpoint joins the
  "Configure custom endpoint ›" disclosure (ANY base_url on a keyed provider collapses now,
  not just defaulted ones); the success state moves ONTO the Test button ("✓ Connected" —
  the status line keeps only errors); the done step HIDES the Specialist-coworkers gallery
  card (returns later; `finish("gallery")` plumbing kept) and drops the per-session-scope
  line; the modal height is FIXED at 700px across all three steps (sized to the tallest —
  the recipe at ~593px content — action rows pin to the bottom, overflow scrolls inside).
- **2026-07-12 (10)** — Owner (walkthrough, screenshots of three pages): Automations /
  Activity / Inbox must share one look; Messaging routing belongs in Inbox → §28 (one page
  shell everywhere; Inbox tabs Pending / Configure; Connectors sub-nav = Connectors · MCP).
- **2026-07-11 (9)** — Owner (walkthrough, step 2 recipe): the channel box must show the
  channel NAME, not `slack:T…/C…` — ChannelPicker now separates display (#name at rest,
  raw address while editing + in the tooltip) from the stored target, on BOTH its surfaces
  (onboarding + session channel subscriptions); the consent line uses the name too. The
  fixed day+time cadence pairs → a day SelectMenu (Mon–Sun, Weekdays, Every day) × a free
  time field; digest instructions re-worded cadence-neutral ("since the last digest") →
  §24 revision.
- **2026-07-11 (8)** — Owner (Mac-app onboarding walkthrough): step 1 warms up ("Welcome to
  OpenCoworker" + connect-to-get-started sub-line); native selects → SelectMenu (the Settings
  Models control); default-model picker dropped (per-session anyway; never persisted);
  optional endpoints behind "Configure custom endpoint ›"; Test joins the Skip/Continue
  action row → §24 revision.
- **2026-07-11 (7)** — Owner (ledger UX-008, mock approved): the §23 session-settings row
  docks into the topbar's left region — one bar instead of two strips; contract untouched
  (rest = icon · hover/focus = glance · click = drawer). Expanded nav: icon first after the
  panel edge; collapsed: after the §22 cluster → §22/§23 amendments.
- **2026-07-11 (6)** — Owner (visual pass on the new shell): topbar ⋮ conversation menu
  removed (nav row's hover cluster covers it; title kept — sole identifier when the nav is
  collapsed); topbar goes edgeless glass (border dropped, paper-tinted blur); composer Mode
  trigger goes borderless and names the chosen mode → §22 amendments.
- **2026-07-11 (5)** — Owner (competitor new-session comparison; ledger UX-007, mock v3
  approved): start screen → three concrete template tasks that carry their own setup (no icon
  tiles; outcome-voiced sub-lines with connector dots; ready = hover "Start →" + prefill,
  gated = always-visible "Configure ›" → the §23 drawer); "Set me up (optional)" removed;
  specialist entry point deferred to an owner sketch → §27. Also: ✳ mark → ✦ app-wide.
- **2026-07-11 (4)** — Owner (Settings audit; ledger UX-006, mock v2 approved): sidebar bottom
  → one account row (name + cloud status dot + state-driven sticky-unlock inbox chip);
  "Settings & more" retired; Integrations renamed Connectors and lives in the account menu with
  Inbox; the two same-named "Activity" pages deduped (dead-letter → Messaging routing ▸
  Unrouted; audit log keeps the name); "Approvals" rename rejected → §26. Supersedes §12's
  bottom cluster.
- **2026-07-11 (3)** — Owner (UX-005 design discussion): per-automation standing scoped
  approvals — tool+target+task rules on the ScheduledTask record, minted only at the creation
  consent card (agent proposes via a `permissions` field) or a run card's "Allow every time";
  agent-written config.toml rejected → §25. Unblocks §24's build.
- **2026-07-11 (2)** — Owner (boss-flow study: new install → recurring GitHub→Slack digest;
  ledger UX-004, mock v2 approved): onboarding restructured to model → role-tabbed recipe →
  tips → §24. Recipe consent = standing scoped approval (ledger UX-005, design pending; §24
  build blocked on it). Gallery-seeding-by-tab parked until after UI cleanup.
- **2026-07-11** — Owner (hand-drawn sketch → UX ledger `ocw-context/docs/ux-improvements/`,
  entries UX-002/UX-003, mocks reviewed + approved): session-screen cleanup — contextual
  `[sidebar][+][search]` cluster (collapsed-sidebar only), facts subtitle replacing the locked
  pill + persona button, composer reduced to `[+][Mode ⌄][send]` with Unattended folded into the
  Mode menu, fresh-only model chip → §22. SourcesBar → session-settings row (hover glance, click
  manage; gray icon = the nudge; drawer renamed "Session settings" + working directories) → §23,
  superseding §3's bar. Naming (ledger UX-001, partial): in-app nav noun = **"Coworkers"**;
  **"Specialist"** reserved for marketing/gallery voice; internals keep `persona`.
- **2026-07-08** — Owner, reviewing the M3.5 Slack-workspaces tab: wanted connector detail as a
  subpage under Connectors (not a nav item), connected-first Connectors list, Apple-quiet styling,
  one add-connection entry point (header-button modal with One click | Manual pills), collapsed
  Tools everywhere, and enterprise privacy levers (Gmail sender/label exclusions — drop silently,
  no `<truncated>` tombstone; HubSpot hidden fields + read-only connect). Multi-account confirmed
  feasible for Gmail (accounts), HubSpot (portals), Teams (orgs). Teams consent corrected: self-
  serve via user install/team-owner RSC, admin only if org policy blocks apps → §21. Build order:
  Connectors list + subpage nav → Slack page → Gmail multi-account → HubSpot → Teams.
- **2026-07-05 (2)** — Owner aesthetic asks on the left nav: floating/collapsible on demand
  (Claude/Codex-style), a RECENT header with a group+filter control moved off the top bar, and
  auto-collapse when an artifact opens → §20. Chose hover-peek + pin, and auto-collapse with
  auto-restore.
- **2026-07-05** — Owner (Slack-on-PM setup): double-send first-contact flow called clumsy →
  §19 (park + one-step allow-and-deliver, connector card as config surface, gateway
  hot-reload). Root-caused "Slack keeps resetting": pre-Jul-3 pytest runs clobbered real
  tokens with a test stub (`xoxb-1`) — isolation fixture already fixed it; proved with
  hash-compare across the full suite. Sender names showing "unknown" = missing bot scopes
  (users:read, channels:read) — setup instructions updated.
- **2026-07-04 (2)** — Owner (testing pass, persona disable): disabled personas kept their
  sidebar sections because old sessions held them (never-orphan rule). Discussed hide vs grey vs
  time-based liveness; owner picked **archive-all-on-disable** (§18) with an inline confirm at
  the disable click.
- **2026-07-04** — Owner: model-key testing pass. First-class provider entries for
  OpenAI-compatible vendors + Together/Fireworks resellers with a curated model matrix (ids →
  labels → capabilities); custom provider picker (sections, key-set dot, last-used); "chose Opus,
  Kimi replied" → model rides every message and is **fixed per session** (§17).
- **2026-07-03 (2)** — Owner testing pass found 10 issues; while fixing the project-workspace
  cluster, owner challenged the workspace enum ("family:knowledge means scratch; family:code means
  explicit directory — why more?"). Decided §16: collapse workspace into family. Also: session
  delete → archive-first with soft confirm + scratch cleanup; sidebar caps sessions per persona
  (configurable); connect-in-context from the session drawer; Inbox filters + orphan pruning.
- **2026-07-03** — Owner: Gallery UX rethink (§15): delete personas; Gallery behind a link as a
  full-screen **modal** (owner preferred modal since installs finish in Personas); carousel + list;
  brand icons for connectors; default generated hero art. Iframe-HTML idea discussed and dropped
  (trust model). Team publish + persona updates designed, phased later.
- **2026-07-01 (2)** — Owner: "should the Slack-channel feature live in the right-side Sources panel?"
  Decided **yes** (§14): a **child panel with back** off the connected Slack/Telegram row manages the
  session's subscribed channels — not the composer `+` menu. Pure GUI, existing subscription APIs.
- **2026-07-01** — Owner: convert the Settings **modal → page** like Integrations, and re-shell
  **Activity** too. Decided **Option 2** (§13): Settings = Appearance/Files/Models/Personas;
  Integrations keeps Connectors/Messaging/MCP/Activity. Models + Personas re-skinned; modal + dead
  tabs removed; shared bodies → `ManageTabs.tsx`.
- **2026-06-30** — Owner: sidebar "very busy" vs Claude/Codex. Decided: **bottom → Inbox + one ⚙
  "Settings & more" menu** (§12), folding Settings/Integrations/Automations/Activity into a
  click-to-open popup. 5 rows → 2.
- **2026-06-29 (3)** — Owner Q on the session top bar ("why the model name? what is 'Interactive'?").
  Decided: **remove both top-bar chips** (§11) — they duplicated the composer's model + "Ask for
  approval" controls, with the mode chip mislabeled vs. the composer. App + mock updated.
- **2026-06-29 (2)** — Confirmed: §4 hierarchy, §8 split button. New: grouped-nav needs clear
  **boundaries** + a per-persona **gear** (§7); **Persona detail page** (§9). Tabled: **persona
  enablement/onboarding** (§10) — keep it lightweight/progressive, not a gated wizard.
- **2026-06-29** — Req: `⚠ N` badge (not "N recommended"); per-session enable/disable toggle
  (hierarchy §4); richer manifests with `recommends` (§5, built); dual left-nav layouts (§7);
  New-session persona dropdown (§8). Owner endorsed the persona-connection drawer concept (§3).
- **2026-06-28/29** — Initial redesign asks: connector message card (§2a), Sources bar (§3),
  Integrations de-clutter (§6), connector-agnostic design (§1), collapsible steps (§2b).
