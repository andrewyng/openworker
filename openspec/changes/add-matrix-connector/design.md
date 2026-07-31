## Context

OpenWorker connectors split **inbound** (gateway adapters + allowlist) from **outbound** (stateless `send_message` / `send_file` tools). Slack and Telegram implement `BasePlatformAdapter`; Slack additionally supports Block Kit buttons for Inbox mirroring via `InteractionEvent`.

Matrix differs materially:

- **E2EE** requires a long-lived crypto-aware client for both sync and send — outbound cannot be a one-shot httpx POST.
- **Room IDs** contain colons (`!abc:example.org`), incompatible with the existing `platform:chat_id[:thread]` parser.
- **Interactive prompts** use **emoji reactions** on a posted message (Hermes pattern), not clickable buttons.
- **Media** in encrypted rooms must be downloaded/uploaded through the Matrix client with decrypted `mxc://` content.

The codebase explicitly borrowed gateway patterns from Hermes. This design targets **behavioral parity** with Hermes Matrix docs for mention/thread/session and reaction approvals, scoped to OpenWorker's existing primitives (`mention_sessions`, `subscriptions`, `dm_session`, `InboxStore`).

**Constraints (locked):**

| Decision | Choice |
|----------|--------|
| Connector id | `matrix` |
| Homeserver | Self-hosted Synapse / ESS |
| E2EE | Required on day one |
| Cloud relay | No |
| SDK | `matrix-nio[e2e]` (PyPI; requires system `libolm`) |
| Reactions + media | Day one |

## Goals / Non-Goals

**Goals:**

- Connect a bot account to a self-hosted homeserver with access token + recovery key; validate via `/account/whoami`.
- Run a persistent sync loop; decrypt inbound / encrypt outbound in E2EE rooms.
- Route inbound text and media through gateway allowlists and Hermes-aligned mention/thread rules (via existing `mention_sessions`, `dm_session`, `subscriptions`).
- Mirror Inbox approvals and discrete-choice questions to Matrix rooms; resolve via emoji reactions with optional sender binding in the adapter.
- Send text (`send_message`) and files (`send_file`) to Matrix targets using the new encoding.
- Persist crypto state across server restarts.

**Non-Goals:**

- OpenWorker Cloud managed OAuth / relay for Matrix.
- Matrix admin agent tools (`matrix_create_room`, `matrix_invite_user`, etc.) — future work.
- Sliding Sync / MSC3575.
- VoIP / livekit.
- Public matrix.org-specific assumptions (works with any Synapse-compatible HS URL).
- Replacing Slack/Telegram target format.
- Dedicated `MatrixSessionStore` until per-user room session routing ships (task 3.3).

## Decisions

### 1. SDK: `matrix-nio[e2e]`

**Rationale:** Ships on PyPI under the existing `messaging` extra. Hermes uses mautrix for reference, but mautrix is not a practical PyPI dep here. Cross-signing bootstrap is implemented in `matrix_crypto_bootstrap.py` (matrix-nio has no SSSS support).

**Alternative:** `mautrix[encryption]` — closer to Hermes source, but heavier packaging and not chosen for v1.

### 2. Target format: `matrix/<urlsafe_b64(room_id)>[/thread/<urlsafe_b64(thread_root_event_id)>]`

**Rationale:** Room IDs and event IDs contain `:`. A dedicated prefix branch in `parse_target` keeps Slack/Telegram unchanged. Base64url without padding is stable for standing grants and mention thread maps.

**Alternative:** Pipe separator (`matrix|room|thread`) — human-readable but breaks consistency with existing colon grammar.

### 3. Session routing: existing OpenWorker primitives (no separate store in v1)

**Rationale:** DM → `dm_session()`; `@mention` in rooms → `_route_mention` with matrix-encoded thread targets; passive fan-out → `subscriptions`. A persisted `MatrixSessionStore` for `group_sessions_per_user` / `session_scope` is deferred to task 3.3.

### 4. Reactions as Inbox interactions

**Rationale:** Matrix has no Block Kit. Hermes maps ✅ / ♾️ / ❌ / number emojis to approval resolutions.

**Approach:**

- `send_interactive()` posts explanatory text and registers `(room_id, message_event_id) → {emoji_map, allowed_reactor?}` in `PendingReactionStore`.
- Inbound `m.reaction` events produce `InteractionEvent` with `interaction_kind="reaction"`; reuse `interactions.encode/decode` for `(item_id, resolution)`.
- ♾️ maps to standing grant (`allow_always`) via existing permissions API.
- `approval_require_sender` (default true) is enforced in `MatrixAdapter._handle_reaction` when `allowed_reactor` is set on the pending prompt.

**Lifecycle reactions** (👀/✅/❌ on inbound processing) — future work (task 4.7).

### 5. Media pipeline

**Inbound:**

| msgtype | Agent delivery |
|---------|----------------|
| `m.text` | Existing `MessageEvent` → `tagged_text` |
| `m.image` | Download decrypted bytes → `image_url` data URL part + text caption (if model supports vision; else text-only fallback) |
| `m.file` / `m.audio` / `m.video` | Save under `{crypto_store}/inbound/`; tagged text references path |

**Outbound (`send_file`):**

- Upload via matrix-nio AsyncClient (encrypted when room requires).
- `room_send` with appropriate `msgtype` and `mxc://` URL; preserve thread relation.

**Security:** Reject non-`mxc://` media URLs in events. Enforce `max_media_bytes` before download.

### 6. Configuration storage

Fields on the `matrix:default` SecretStore profile (plus standard `allowed_users`):

```
homeserver_url, access_token, user_id, recovery_key (secret)
allowed_rooms[], free_response_rooms[], ignore_user_patterns[]
require_mention, auto_thread, e2ee_mode (=required)
max_message_length, max_media_bytes, approval_require_sender
```

Room allowlists are read only by `MatrixSettings` (not duplicated on `ConnectorSettings`). Advanced Hermes flags (`session_scope`, `group_sessions_per_user`, lifecycle reactions, etc.) ship when task 3.3 / 4.7 land.

Connect wizard minimum: HS URL + token + recovery key + allowed users.

### 7. Crypto store location

`{openworker_state}/matrix/store/` — matrix-nio sqlite crypto DB + sync tokens. Never log recovery keys. On stale OTM key detection, fail connect with actionable error (new access token / device).

### 8. Testing

Unit tests for target encoding, mapper, reactions, and crypto bootstrap (no network). Integration harness with libolm in messaging CI is future work (task 7.1).

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| libolm missing on user machine | `e2ee_mode=required` → connect fails with install instructions; no silent fallback |
| DMG packaging libolm | Separate release task; document brew/apt deps for dev |
| Reaction race (multiple users react) | First resolved wins; clear pending registry |
| Non-vision models receive images | Detect capability; fall back to text description + saved path |
| Federation abuse via media URLs | mxc-only; size cap; room allowlist for private deploys |
| matrix-nio vs Hermes mautrix drift | Document in design; cross-signing bootstrap is custom |

## Migration Plan

- **New connector** — no migration from existing profiles.
- **Rollback:** Disconnect matrix in GUI; disable gateway adapter; crypto store remains on disk for reconnect.
- **Upgrade note:** Deleting crypto store requires new access token (document in connector instructions).

## Open Questions

- _(none blocking — decisions locked in explore session)_
