# UI Refresh — Implementation Spec

**Status:** ready to implement. **Audience:** the implementing agent (has full repo access).
**Scope:** full-stack (data model → stores → connector contract → gateway → REST/WS → React).
**Backward compatibility:** **none required** — pre-launch, no users. Redesign cleanly; migrate
on-disk state by deletion if needed.

**Companion docs**
- UX rationale + decisions: [`UX-DECISIONS.md`](UX-DECISIONS.md) (the *why*; don't violate it).
- Visual spec: [`../ui-mocks/redesign.html`](../ui-mocks/redesign.html) (the *look*).
- Tests + acceptance: [`UI-REFRESH-VERIFICATION.md`](UI-REFRESH-VERIFICATION.md).
- Test harness: [`FAKE-SLACK-SPEC.md`](FAKE-SLACK-SPEC.md) (separate deliverable — build first; the
  verification suite depends on it).

Throughout, `→` marks a file to change. Verify signatures against current code before editing.

---

## 0. Principles

1. **One connector contract, Slack is the reference.** No Slack special-casing in the core. Every
   connector adapter implements the same interface (§2); Slack-specific mechanics (Socket Mode, Web
   API, Block Kit, name resolution) live behind it. New connectors (Salesforce, HubSpot, …) implement
   the contract and "just work" in the UI via the registry (§1).
2. **Structured, not stringly-typed.** Connector inbound messages become structured records carrying
   identities + display names (§3), so the GUI renders rich cards and the model still gets framed
   text. No parsing of `"💬 New message on slack:C…"` in the frontend.
3. **Connection hierarchy** (UX §4, Decided): `account-connected` → `persona-default-enabled` →
   `session-override`. Effective = connected AND persona-default(unless session override) AND not
   session-disabled (§4).
4. **Names everywhere, ids on hover.** Resolve + cache `id → display name` for users *and* channels
   at the connector boundary (§2.3); persist names with messages.

---

## 1. Connector registry metadata (brand color + logo)

Goal: the UI renders any connector by `logo` id + `brand_color`, with a neutral fallback.

**Backend**
- → `coworker/connectors/descriptors.py`: add to `ConnectorDescriptor`:
  - `brand_color: str = "#6b7280"` (hex; the connector's brand, fallback gray)
  - `logo: str = ""` (a stable logo id, e.g. `"slack"`, `"github"`; empty → fallback)
  - Populate for slack/telegram and the not-yet-shipped ones referenced by personas
    (github, datadog, salesforce, hubspot, pagerduty) — descriptors may exist as **available:false**
    placeholders so recommendations can render. (A placeholder descriptor = id, title, icon, blurb,
    brand_color, logo, `available=False`, `two_way` as appropriate, no `connect`.)
- → `coworker/connectors/setup.py::connector_list`: include `brand_color`, `logo` per connector.

**Frontend**
- → `surfaces/gui/src/connectors/registry.ts` (new): `CONNECTORS: Record<string, {label; logo}>`
  mapping `logo` id → an imported SVG component, plus `FALLBACK`. A `<ConnectorIcon connector=…/>`
  and `<ConnectorBadge/>` component reads `brand_color` from API data and the SVG from the registry;
  unknown id → fallback plug glyph + the provided/neutral color. (Mirror the mock's `CONNECTORS`
  object + `data-brand` tinting.)
- Logos are bundled frontend SVG assets keyed by id; brand color comes from the API so it's one
  source of truth.

---

## 2. Connector contract (the abstraction)

Define the interface every adapter satisfies. Today `BasePlatformAdapter` + `SlackAdapter`/
`TelegramAdapter` (`coworker/connectors/adapters.py`) and `Gateway` (`gateway.py`) already cover most
of this; this section formalizes + extends it.

### 2.1 Interface (adapter)
```
class PlatformAdapter:
    platform: str
    brand: ConnectorBrand            # {color, logo} (mirror descriptor)
    async def connect() -> bool
    async def disconnect() -> None
    async def send(chat_id, text, *, thread_id=None) -> SendResult
    async def send_interactive(chat_id, text, buttons: list[Button]) -> str   # returns message id
    async def update_message(chat_id, message_id, text, *, buttons=None) -> None
    async def resolve_user_name(user_id) -> str | None      # cached (§2.3)
    async def resolve_channel_name(chat_id) -> str | None    # cached (§2.3)
    # inbound: adapter calls gateway handlers with MessageEvent / InteractionEvent
```
Slack already implements `connect/disconnect/send/_display_name`; **add** `send_interactive`,
`update_message` (currently on senders/gateway — keep, but route through the adapter), and
`resolve_channel_name`. Telegram implements the subset it supports (no buttons → `send_interactive`
falls back to text + numbered replies, already partially present).

### 2.2 Inbound event shape
- → `coworker/connectors/base.py`: extend `SessionSource` with `chat_name: Optional[str] = None`
  (channel/DM display name; user_name already exists). `MessageEvent` already carries `text`,
  `source`, `message_id` (the ts). Adapters MUST populate `user_name` + `chat_name` (resolved, §2.3)
  before handing the event to the gateway.
- `_dispatch_inbound` (§3) builds the structured `MessageSource` from these.

### 2.3 Name resolution + cache
- Slack adapter has `_display_name` (users.info, cached). **Add** `_channel_name` (conversations.info,
  cached) with the same pattern; both keyed by id, in-memory `dict`, best-effort (None on failure,
  caller falls back to id). A short TTL is optional (names rarely change) — start with no expiry,
  re-resolved on process restart.
- Cache lives on the adapter instance. The gateway's recent-senders map (`gateway._record_recent`)
  already stores `user_name`; ensure it's the resolved name.

---

## 3. Structured connector inbound messages (rich card)

Today a connector message reaches a session as a framed text blob. Make it structured so the GUI
renders a card (logo + channel/person names + hover ids + ts) while the model still sees framed text.

### 3.1 Data
- → `coworker/connectors/base.py` (or a new `messages.py`): `MessageSource` dataclass:
  ```
  connector: str         # platform id, e.g. "slack"
  kind: str              # "channel" | "dm"
  channel_id: str        # e.g. "C0BD7KZ1AH5"
  channel_name: str      # e.g. "#ocw-test"  (resolved; falls back to id)
  sender_id: str
  sender_name: str       # resolved; falls back to id
  ts: float              # epoch seconds
  text: str              # the RAW message (what the card shows)
  ```
- The persisted user message gains an optional sidecar. Decide the storage seam by reading
  `coworker/engine.py` + the conversation/session store:
  - The model-facing `content` stays the **framed** text (so replay keeps the agent's instructions:
    "💬 New message on {channel} from {sender}: {text}\n(You're subscribed… reply with send_message…)").
  - Attach `source` (the `MessageSource`) to that message record for **display only**. Persist it;
    **strip it before sending to the provider** (providers must not receive unknown keys). The
    cleanest approach: store transcript messages as the existing dicts plus a parallel
    `message_meta[index]` or an `ocw_source` key that the provider serializer drops. Pick whichever
    matches the current message model with least friction; document the choice in the PR.

### 3.2 Plumbing
- → `coworker/server/manager.py`:
  - `deliver_to_session(session_id, message, *, source: MessageSource | None = None)`. When `source`
    is set, the engine records the user message with the framed text **and** the source sidecar.
  - `_dispatch_inbound`: build `MessageSource` (resolved names from the event) and pass it through for
    both the channel-subscription path and the DM path.
- → `coworker/engine.py`: `run(...)` / the user-message append path accepts the optional source and
  stores it on the recorded user message; `TURN_START` event `data` includes `source` (so the live
  render shows the card immediately — see §1 event bus already broadcasts turn_start).
- → `coworker/server/app.py`:
  - `GET /v1/sessions/{id}/messages`: each message includes `source` when present.
  - WS `turn_start`: include `source` in the event `data` (already forwards `input`).

### 3.3 Frontend
- → `surfaces/gui/src/components/ConnectorMessageCard.tsx` (new): renders header (ConnectorBadge +
  `channel_name` + sender_name + relative time + "via {label}") with an **id-on-hover** swap
  (`channel_id · sender_id`), body = `source.text`. Brand-tinted header + left edge (mock parity).
- → `surfaces/gui/src/App.tsx`: the conversation item model gains an optional `source`. In
  `itemsFromMessages` and the `turn_start` handler, when a user message has `source.connector`, push a
  `{ kind: "connector", source }` item rendered by `ConnectorMessageCard` instead of the plain user
  bubble. (Generalizes to Salesforce/HubSpot via the registry.)

---

## 4. Connection hierarchy (data model)  — UX §4

Three layers. **No backward compat:** replace the current global per-connector `enabled` semantics.

### 4.1 Layers
1. **account-connected** — a connector profile with valid creds exists (`secrets[<name>:default]`).
   Unchanged (`connector_list[].connected`).
2. **persona-default-enabled** — per persona, which connected connectors are on by default for its
   sessions. **User-editable** (persona detail page), seeded from the manifest.
3. **session-override** — per session, explicit on/off overriding the persona default.

**Effective(session, connector)** = `connected` AND `session_override[c]` if present
**else** `persona_default[persona, c]`.

### 4.2 Stores (JSON, mirror `SubscriptionStore`/`UnroutedStore`)
- → `coworker/connections.py` (new):
  - `PersonaConnectionStore(path)` — `{persona_id: {connector: bool}}`. `defaults_for(persona_id,
    manifest)`: if no stored row, seed from the manifest — a `recommends` item with `tier: core` whose
    connector is **connected** → default **on**; everything else off. `set(persona_id, connector,
    bool)`, `get(persona_id)`.
  - `SessionConnectionStore(path)` — `{session_id: {connector: bool}}` (overrides only; absence =
    inherit). `set/clear(session_id, connector, bool|None)`, `get(session_id)`. `remove_session(id)`
    on delete (like subscriptions).
  - `effective(session_id, persona_id, *, connected: set[str]) -> dict[str, bool]` — the resolver.
- Manager owns both: `self.persona_connections`, `self.session_connections`.

### 4.3 Runtime gating
The effective set must actually change behavior:
- **Inbound** (`_dispatch_inbound`): a channel subscription belongs to a session; deliver only if the
  source connector is effective-enabled for that session. A muted connector → skip delivery (still
  buffer for catch-up).
- **Outbound/tools**: when building a session's engine, expose a connector's tools only if
  effective-enabled for that session (extend the existing connector-tool gating that already checks
  global enabled/experimental — read `agent.py` / `catalog` / `_enabled_connector_tools`).
- **DM routing**: a DM to the designated session is delivered only if that session has the connector
  effective-enabled.

---

## 5. Persona surface (recommends + detail page)

Manifest `recommends` already exists (`coworker/personas/manifest.py`, `Recommendation`). Surface it
+ the persona-default connections + identity to the GUI.

**Backend** → `coworker/server/app.py` + `coworker/personas/registry.py` + manager:
- `GET /v1/personas/{id}` → identity (id, name, icon, tagline, description), `tools`,
  `recommended_models`, `default_permission_mode`, `workspace`, `enabled` (persona on/off),
  `recommends: [{kind, ref, reason, tier, connected: bool}]` (annotate `connected` from
  `connector_list`), and `default_connections: [{connector, enabled, connected}]` (from
  `PersonaConnectionStore.defaults_for`).
- `POST /v1/personas/{id}/connections` `{connector, enabled}` → set persona default.
- `POST /v1/personas/{id}/enable` `{enabled}` → enable/disable the persona (persona registry already
  tracks enabled/surfaced; reuse it).
- `GET /v1/personas` (list) already exists for the picker — ensure it returns enabled personas with
  icon + name + tagline.

**Frontend** → `surfaces/gui/src/components/PersonaView.tsx` (new): the detail page (mock parity) —
identity + Enable toggle, About, capabilities (tools), "Connections for full benefit" (recommends,
core/optional + reason + connect state), "New sessions get by default" (default_connections toggles),
defaults. Opened from the grouped-nav gear and the New-session menu's "Manage personas…" (UX §9).

---

## 6. Per-session connections (Sources bar + drawer)

**Backend** → `coworker/server/app.py` + manager:
- `GET /v1/sessions/{id}/connections` →
  - `connected: [{connector, enabled, detail}]` — effective-enabled connectors for the session, with a
    short detail (e.g. Slack: "#ocw-test · DMs"). `enabled` reflects the session override/persona
    default so the drawer toggle shows correct state.
  - `recommended: [{connector, reason, tier, connected}]` — the persona's `recommends` **not yet
    connected** (drives the `⚠ N` count = `len(recommended where not connected)`).
  - `attention: int` — the `⚠ N`.
- `POST /v1/sessions/{id}/connections` `{connector, enabled}` → set the session override
  (mute/unmute). Clearing back to inherit = send `enabled` matching the persona default, or add a
  `clear` flag.

**Frontend** → `surfaces/gui/src/components/SourcesBar.tsx` + `SourcesDrawer.tsx` (new):
- Sources bar: icons-only avatar stack + `⚠ N` badge (UX §3); click opens the drawer.
- Drawer: persona why-connect blurb (from `GET /v1/personas/{id}`), Connected (with enable toggles →
  `POST …/connections`), Recommended connectors + MCP (Connect/Add), link to global Integrations.

---

## 7. Frontend — views, components, state

- **Session view**: add the Sources bar (§6) under the title; render `ConnectorMessageCard` (§3.3);
  wrap tool/approval items in a collapsed `<details>`-style **StepGroup** ("N actions · M approvals")
  — pure frontend grouping of existing `tool_*`/`permission_*`/approval items (UX §2b).
- **Per-session drawer** (§6) + **Persona detail page** (§5) as new routed surfaces; generalize the
  view switch (mock uses session/integrations/persona).
- **Integrations** → `surfaces/gui/src/components/IntegrationsView.tsx`: restructure into a left
  sub-nav: **Connectors · Messaging routing · Activity · MCP** (UX §6). Move the existing
  DM-route/subscriptions/approvals-routing controls under "Messaging routing"; Unrouted under
  "Activity"; allow-list stays inline on the Slack connector card.
- **Sidebar** → `Sidebar.tsx`: dual layout (flat ↔ grouped-by-persona) toggled by an icon by the
  wordmark; grouped = bounded per-persona cards with a gear → PersonaView (UX §7). Persist the choice
  in prefs (`GET/POST` the existing settings/prefs; add `nav_layout`).
- **New session**: split button — primary = last/default persona; ▾ = enabled personas (from
  `/v1/personas`) + "Manage personas…" (UX §8).
- **api.ts**: add typed clients + interfaces for every endpoint in §§3,5,6 and the connector
  `brand_color`/`logo` fields; add `MessageSource`, `PersonaDetail`, `SessionConnections`.

---

## 8. REST/WS contract (net-new / changed)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/connectors` | + `brand_color`, `logo` per connector (§1) |
| GET | `/v1/sessions/{id}/messages` | + `source` on connector messages (§3) |
| WS  | `turn_start` | + `source` in event data (§3) |
| GET | `/v1/personas/{id}` | identity + recommends(+connected) + default_connections (§5) |
| POST | `/v1/personas/{id}/connections` | set persona-default connection (§5) |
| POST | `/v1/personas/{id}/enable` | enable/disable persona (§5) |
| GET | `/v1/sessions/{id}/connections` | connected + recommended + ⚠ count (§6) |
| POST | `/v1/sessions/{id}/connections` | set session override (§6) |
| GET/POST | settings `nav_layout` | persist flat/grouped nav (§7) |

---

## 9. Build phases (suggested)

1. **Foundation**: connector registry metadata (§1) + connector contract tidy-up (§2, incl.
   `resolve_channel_name`). Land FakeSlack (separate deliverable) so later phases test deterministically.
2. **Structured messages** (§3) — backend + `ConnectorMessageCard`. High user-visible value.
3. **Connection hierarchy** (§4) — stores + resolver + runtime gating. The core data-model change.
4. **Persona + session surfaces** (§5, §6) — APIs + PersonaView + SourcesBar/Drawer.
5. **Frontend polish** (§7) — Integrations sub-nav, dual nav, split button, StepGroup.

Each phase: code + tests (see VERIFICATION) green before the next. Commit per phase.
