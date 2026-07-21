# UI Refresh — Verification & Test Plan

Companion to [`UI-REFRESH-SPEC.md`](UI-REFRESH-SPEC.md). Defines the test cases, the manual checks,
and the acceptance bar for each phase. The implementing agent must land these (green) alongside the
code. Several integration tests depend on the **FakeSlack** harness ([`FAKE-SLACK-SPEC.md`](FAKE-SLACK-SPEC.md)) —
build that first.

## How to run
- Python: `./.runtests.sh platform/tests/<file> -q` (uses the agent-platform venv). Whole suite:
  `./.runtests.sh platform/tests/ -q`. Baseline is green **except 3 pre-existing SDK-import failures**
  (`test_anthropic_provider`, `test_gemini_provider`, `test_provider_router`) — unrelated; don't
  "fix" by mocking the SDK.
- Frontend: `cd platform/surfaces/gui && npx tsc --noEmit` (must be clean) and `npm run build`.
- Server under the aisuite venv: `PYTHONPATH=platform /Users/rohit/fleet/ro4d/aisuite/platform/.venv/bin/python -m coworker.server.run --port 8765`.
  Dev GUI on :1420 defaults to backend 8765.

## Conventions
- Backend unit tests use `ScriptedProvider` + `SessionManager(workspace=tmp_path, provider=…)` and
  `TestClient(create_app(mgr))` (see `test_session_events.py`, `test_subscriptions.py`).
- Messaging integration tests use **FakeSlack** instead of the network: point the Slack adapter at the
  fake, drive inbound/button events through the fake's control API, assert on outbound calls it
  recorded. No `COWORKER_DEBUG_INJECT`, no real tokens.

---

## Phase 1 — connector registry + contract

**Unit**
- `test_descriptor_brand_logo`: every descriptor exposes `brand_color` (hex) + `logo`; `/v1/connectors`
  surfaces them; an unknown/placeholder connector still returns a color (fallback gray) + `logo`.
- `test_placeholder_connectors_listed`: github/datadog/salesforce/hubspot/pagerduty appear as
  `available:false` so personas can recommend them; they have no `connect`.
- `test_slack_resolve_channel_name`: `_channel_name(id)` returns the resolved name, **caches** (one
  `conversations.info` call for repeats), returns `None` on failure. (Pattern mirrors the existing
  `test_slack_resolves_and_caches_display_name`.)

**Frontend**
- `ConnectorIcon`/`ConnectorBadge` renders the registry SVG for a known id and the fallback for an
  unknown id; brand color applied from prop. (Component test or a Storybook-style render assertion.)

**Manual:** Integrations → Connectors shows correct logos/colors incl. a placeholder (e.g. Datadog)
with the fallback-or-brand badge.

---

## Phase 2 — structured connector messages

**Unit**
- `test_inbound_builds_message_source`: `_dispatch_inbound` of a channel `MessageEvent` (with
  `user_name`, `chat_name` set) delivers to the subscribed session with a `MessageSource`
  (`connector/kind/channel_id/channel_name/sender_id/sender_name/ts/text`).
- `test_message_source_persisted_and_stripped`: after the turn, `GET /v1/sessions/{id}/messages`
  returns the user message **with** `source`; the provider received the **framed text** and **no**
  `source`/unknown keys (assert against the `ScriptedProvider`'s captured `messages`).
- `test_turn_start_carries_source`: the WS `turn_start` event for a delivered connector message
  includes `source` (register a fake session client, like `test_session_events.py`).
- `test_dm_message_source_kind_dm`: a DM yields `kind="dm"`.

**Frontend**
- `itemsFromMessages` maps a message with `source.connector` to a `connector` item; `turn_start` with
  `source` renders `ConnectorMessageCard` (not a plain bubble). Hover swaps names→ids.

**Manual (FakeSlack):** post a channel message via FakeSlack → the session's open view shows the
connector card live (names, hover→ids, ts); reselect the session → the card persists (re-rendered
from `/messages`).

---

## Phase 3 — connection hierarchy

**Unit** (`test_connections.py`, new)
- `test_persona_defaults_seeded_from_manifest`: `PersonaConnectionStore.defaults_for(ops, manifest)`
  → core+connected recommends on, others off; persisted after first read.
- `test_effective_resolution`: with `connected={slack,github}`, persona-default `{slack:on,
  github:on, datadog:off}`, session override `{slack:off}` → effective `{github:on}` (slack muted,
  datadog not connected).
- `test_session_override_clear_inherits`: clearing a session override returns to persona default.
- `test_remove_session_clears_overrides`: deleting a session drops its overrides.

**Runtime gating**
- `test_muted_connector_not_delivered`: a session subscribed to a Slack channel but with Slack muted
  (session override off) does **not** receive `_dispatch_inbound` for that channel (still buffered).
- `test_muted_connector_tools_absent`: building that session's engine omits the connector's tools.
- `test_dm_muted_session_not_delivered`: DM routed to a session with the connector muted is parked/
  skipped, not delivered.

**Manual:** in a session's Sources drawer, toggle Slack **off** → posting in the subscribed channel
no longer wakes the session; toggle on → it resumes. Confirm the persona-level default is unchanged.

---

## Phase 4 — persona + session connection surfaces

**Unit (TestClient)**
- `test_persona_detail_endpoint`: `GET /v1/personas/ops` returns identity, tools, `recommends` with
  `connected` annotated, and `default_connections`.
- `test_persona_set_default_connection`: `POST /v1/personas/ops/connections {github,false}` flips the
  persona default; reflected in the next GET and in new sessions' effective set.
- `test_persona_enable_toggle`: `POST /v1/personas/ops/enable {false}` removes it from the
  `/v1/personas` picker list.
- `test_session_connections_endpoint`: `GET /v1/sessions/{id}/connections` returns connected +
  recommended + `attention` = count of not-yet-connected recommends.
- `test_session_set_override`: `POST /v1/sessions/{id}/connections {slack,false}` sets the override;
  effective resolution + `GET` reflect it.

**Frontend**
- PersonaView renders detail from the endpoint; toggling a default connection POSTs and re-reads.
- SourcesBar shows `⚠ N` = attention; opening the drawer lists connected (with working toggles) +
  recommended (Connect/Add).

**Manual:** open a persona's gear → detail page shows recommends with reasons + connect state + the
"new sessions get by default" toggles; flip one and start a new session of that persona → the default
is reflected in its Sources drawer.

---

## Phase 5 — frontend polish

**Frontend / manual**
- Integrations sub-nav (Connectors/Messaging/Activity/MCP); the three messaging controls live under
  "Messaging routing"; Unrouted under "Activity".
- Sidebar layout toggle persists across reload (prefs `nav_layout`); grouped view shows bounded
  per-persona cards with a working gear.
- New-session split button: primary starts last/default persona; ▾ lists enabled personas + opens
  PersonaView via "Manage personas…".
- StepGroup: tool/approval items render collapsed ("N actions · M approvals ✓"), expandable.
- `npx tsc --noEmit` clean; `npm run build` succeeds.

---

## Cross-cutting acceptance (end-to-end, via FakeSlack)

A single scripted scenario the FakeSlack harness can drive, asserting the whole refresh:
1. Connect Slack (against FakeSlack); allow a user; subscribe the Ops "incident" session to a channel.
2. Post a channel message → session shows the **connector card** live (resolved names).
3. Agent proposes a tool needing approval; session is Unattended with approvals routed to a channel →
   FakeSlack receives a **Block Kit** card; inject the **Approve** click → turn resumes, reply posts
   back to the origin channel (FakeSlack records the outbound).
4. Mute Slack for the session in the drawer → a further channel post does **not** wake it.
5. `GET /v1/sessions/{id}/connections` `attention` matches the persona's unconnected recommends.

This scenario lives as `test_ui_refresh_e2e.py` against FakeSlack and is the merge gate.

## Regression bar
- Whole suite green (minus the 3 known SDK-import failures).
- No real-network calls in tests (grep the diff for `slack.com`, real tokens — there should be none
  outside FakeSlack config).
- `tsc --noEmit` clean; `npm run build` passes.
