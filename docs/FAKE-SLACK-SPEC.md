# FakeSlack — Standalone Slack test double (spec)

**Status:** separate deliverable (assign to a sub-agent). **Build this first** — the messaging
integration tests in [`UI-REFRESH-VERIFICATION.md`](UI-REFRESH-VERIFICATION.md) depend on it.

**Goal:** a controllable, local fake of the slices of Slack we use (Web API + Socket Mode), so the
real `SlackAdapter`/`Gateway` can run end-to-end **without the network, tokens, or the Slack app
console**. Replaces the manual `COWORKER_DEBUG_INJECT` flow with deterministic, scriptable tests.

**Non-goals:** not a faithful Slack (no real auth, no rate limits, no full schema). Implement only what
the adapter calls + what tests need to drive.

---

## How the app points at it (the one required adapter change)

The Slack adapter (`coworker/connectors/adapters.py`) builds `slack_bolt.async_app.AsyncApp` +
`AsyncSocketModeHandler`, which talk to `https://slack.com/api/`. Add a **base-URL override** so it can
target the fake:
- Accept an override (env `SLACK_API_URL`, default `https://slack.com/api/`) and pass it to the
  `AsyncWebClient(base_url=…)` the app uses. Slack's `apps.connections.open` is itself a Web API call,
  so the **socket URL the fake returns** flows from the same base — one override redirects both Web API
  and the Socket Mode connection.
- This is the only production-code change FakeSlack requires; keep it minimal and documented.

Tests/standalone set `SLACK_API_URL=http://127.0.0.1:<port>/api/` and connect the adapter with any
fake `xoxb-`/`xapp-` strings.

---

## Surfaces

### 1. Web API (HTTP) — implement exactly what the adapter calls
Base path `/<port>/api/<method>`; all return `{"ok": true, …}` (or `{"ok": false, "error": …}` when a
test asks for a failure). Methods:

| Method | Request | Response | Notes |
|---|---|---|---|
| `auth.test` | bot token | `{ok, user_id, team, team_id, url}` | identifies the bot; `user_id` = the fake bot's id (loop-guard) |
| `apps.connections.open` | app token | `{ok, url: "ws://127.0.0.1:<port>/socket"}` | hands back the fake's WS URL |
| `users.info` | `user` | `{ok, user:{id, name, real_name, profile:{display_name, real_name}}}` | from the registered user table; unknown id → `{ok:false,error:"user_not_found"}` |
| `conversations.info` | `channel` | `{ok, channel:{id, name, is_im}}` | from the channel table |
| `chat.postMessage` | `channel, text, blocks?, thread_ts?` | `{ok, ts, channel}` | **record** it (test-inspectable); allocate a monotonic `ts` |
| `chat.update` | `channel, ts, text, blocks?` | `{ok, ts, channel}` | **record** it |

Anything else the adapter happens to call → `{ok:true}` no-op (log it so gaps surface).

### 2. Socket Mode (WebSocket) — `/socket`
Mimic Slack's envelope protocol the `AsyncSocketModeHandler` expects:
- On connect, send `{"type":"hello"}`.
- **Inbound events** (a user posts): send an envelope
  `{"envelope_id": <uuid>, "type":"events_api", "payload":{"token":"…","type":"event_callback",
  "event":{"type":"message","channel":<id>,"channel_type":"channel"|"im","user":<id>,"text":<str>,
  "ts":<ts>,"thread_ts"?:<ts>}}}`. Client replies `{"envelope_id":…}` (ack) — the fake may ignore acks
  or record them.
- **Interactions** (button click): send
  `{"envelope_id":<uuid>,"type":"interactive","payload":{"type":"block_actions","user":{"id":<id>,
  "username":<name>},"channel":{"id":<id>},"message":{"ts":<ts>},"actions":[{"action_id":<str>,
  "value":<str>}]}}`. Client acks.
- Match the exact envelope shape `slack_bolt` decodes (verify against the installed `slack_bolt`/
  `slack_sdk` version in the aisuite venv — this is the fiddly part; write a focused test that the
  real handler dispatches a fake-sent envelope).

### 3. Control / admin API (the test-facing surface) — HTTP under `/control/*`
This is how tests + the standalone runner drive scenarios:

| Endpoint | Purpose |
|---|---|
| `POST /control/users` `{id, name, real_name}` | register a user (so `users.info` resolves a name) |
| `POST /control/channels` `{id, name, is_im}` | register a channel/DM |
| `POST /control/inbound` `{channel, user, text, thread_ts?, channel_type}` | push a message over the socket |
| `POST /control/interaction` `{channel, user, username, message_ts, action_id, value}` | push a button click |
| `GET /control/outbound` | the recorded `chat.postMessage`/`chat.update` calls (for assertions) |
| `POST /control/reset` | clear users/channels/recorded calls + drop sockets |
| `GET /control/health` | readiness |

Programmatic API mirrors these (a `FakeSlack` Python object with `add_user/add_channel/
inbound/interaction/outbound/reset`) so pytest can drive without HTTP when embedded.

---

## Packaging & modes
- Location: `platform/coworker/testing/fake_slack/` (a package), with `server.py` (aiohttp app —
  `aiohttp` is already a dep) and `__init__.py` exporting `FakeSlack`.
- **Embedded (pytest):** a fixture `fake_slack` that starts the server on an ephemeral port, sets
  `SLACK_API_URL`, yields the control object, tears down. Used by the integration/e2e tests.
- **Standalone (manual GUI testing):** `python -m coworker.testing.fake_slack --port 8910` runs it;
  print the `SLACK_API_URL` to export + a tiny REPL or curl examples for the control API, so a human
  can drive the live dev app against it (replaces real Slack for visual review).
- No new third-party dependencies (aiohttp only).

---

## Acceptance (the fake's own tests)
1. `SlackAdapter` with `SLACK_API_URL` → fake connects: `connect()` returns True, `auth.test` resolved
   the bot id, Socket Mode `hello` received.
2. `POST /control/inbound` → the gateway's inbound handler fires with a `MessageEvent` whose
   `user_name`/`chat_name` are **resolved** from the registered tables (exercises `users.info` +
   `conversations.info` caching).
3. Agent/test calls `send`/`send_interactive` → recorded under `GET /control/outbound` with the right
   channel/text/blocks.
4. `POST /control/interaction` → the adapter's `ocw_*` action handler fires → reaches
   `Gateway._on_interaction` (resolves the inbox item).
5. `POST /control/reset` returns the fake to a clean slate between tests.
6. A focused test proves the **real** `slack_bolt` handler dispatches a fake-sent events_api **and**
   interactive envelope (guards against envelope-shape drift).

When done, wire `test_ui_refresh_e2e.py` (VERIFICATION "Cross-cutting acceptance") on top of it.
