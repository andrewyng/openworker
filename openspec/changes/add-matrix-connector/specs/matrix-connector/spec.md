## ADDED Requirements

### Requirement: Matrix connector registration

The system SHALL expose a connector named `matrix` in the connector catalog with `two_way=true`, `channels=true`, `managed=false`, and `available=true`. The descriptor SHALL require `homeserver_url`, `access_token`, and `recovery_key` (secret), and SHALL validate credentials via the Matrix Client-Server `/account/whoami` endpoint. The GUI SHALL display Element/Matrix branding via `logo=matrix`.

#### Scenario: Successful connect with valid token

- **WHEN** the user submits a valid homeserver URL, access token, and recovery key
- **THEN** the connector reports connected status and persists a `matrix:default` profile without returning secrets to the client

#### Scenario: Connect fails without recovery key

- **WHEN** the user omits the recovery key on a homeserver with cross-signing enabled
- **THEN** validation or connect SHALL fail with an actionable error explaining the recovery key requirement

### Requirement: E2EE required mode

The Matrix adapter SHALL operate with `e2ee_mode=required`. If `libolm` or crypto initialization fails, connect SHALL fail closed and MUST NOT fall back to an unencrypted client.

#### Scenario: Missing libolm

- **WHEN** the messaging extra is installed but libolm is absent on the system
- **THEN** gateway start for matrix SHALL fail with install instructions

#### Scenario: Encrypted room message round-trip

- **WHEN** an authorized user sends a text message in an E2EE room where the bot is joined
- **THEN** the bot decrypts the event, routes it through the gateway, and can send an encrypted reply visible in Element

### Requirement: Homeserver allowlists

The connector SHALL support `allowed_users` (Matrix user IDs) and `allowed_rooms` (room IDs). When `allowed_rooms` is non-empty, inbound events from other rooms SHALL be ignored except direct-message rooms. When `allowed_users` is empty, the default SHALL remain deny-all for inbound (consistent with other messaging connectors) until users are captured and added.

#### Scenario: Message from disallowed room

- **WHEN** `allowed_rooms` is configured and a message arrives from a room not in the list
- **THEN** the gateway SHALL NOT dispatch the message to the agent

#### Scenario: Message from allowed user in allowed room

- **WHEN** both allowlists pass
- **THEN** the message proceeds to routing

### Requirement: Hermes-aligned mention and thread behavior

The adapter SHALL implement, in v1: `require_mention` (default true for rooms), `free_response_rooms`, and `auto_thread`. Threads where the bot has already participated SHALL NOT require a repeat @mention.

`session_scope`, `dm_mention_threads`, `dm_auto_thread`, and `group_sessions_per_user` are specified for Hermes parity but deferred (task 3.3); v1 routes DMs via `dm_session()`, channel @mentions via `_route_mention`, and passive channel traffic via `subscriptions`.

Implementation uses **`matrix-nio[e2e]`** with custom cross-signing bootstrap in `matrix_crypto_bootstrap.py` (not mautrix).

#### Scenario: Room message without mention

- **WHEN** `require_mention=true` and a room message does not mention the bot and the room is not in `free_response_rooms`
- **THEN** the message SHALL NOT spawn or steer an agent turn (subscription fan-out rules still apply)

#### Scenario: Thread continuation without mention

- **WHEN** the bot previously replied in a Matrix thread and a follow-up arrives in that thread
- **THEN** the message SHALL be routed as a continuation without requiring @mention

### Requirement: Per-user session isolation in shared rooms

When `group_sessions_per_user=true`, two authorized users messaging in the same room SHALL map to distinct agent sessions according to `session_scope`. **Deferred to task 3.3** — not required for v1 ship.

#### Scenario: Two users same room

- **WHEN** Alice and Bob each send messages in the same project room
- **THEN** their conversation context SHALL NOT share a single session transcript unless `group_sessions_per_user=false`

### Requirement: Reaction-based Inbox mirroring

When an Inbox item with discrete choices is mirrored to a Matrix-bound channel, the system SHALL post a text prompt and resolve the item when an authorized user adds a mapped emoji reaction. Approval items SHALL support ✅ (approve once), ♾️ (approve always / standing grant), and ❌ (deny). Question items with options SHALL support numbered emoji reactions (1️⃣, 2️⃣, …).

#### Scenario: Approve via reaction

- **WHEN** a pending approval is mirrored to Matrix and the requester reacts ✅ on the prompt message
- **THEN** the Inbox item resolves as allow and the suspended agent continues

#### Scenario: Deny non-requester when sender-bound

- **WHEN** `approval_require_sender=true` and a different user reacts ✅ on an approval prompt
- **THEN** the reaction SHALL NOT resolve the item

#### Scenario: Approve always via infinity reaction

- **WHEN** the requester reacts ♾️ on an approval prompt
- **THEN** the item resolves and a standing grant equivalent to allow-always is recorded for the tool context

### Requirement: Inbound media handling

The adapter SHALL handle inbound `m.room.message` events with `msgtype` of `m.image`, `m.file`, `m.audio`, or `m.video`. Media content URIs MUST be `mxc://` only. Downloads SHALL respect `max_media_bytes` (default 104857600). Images SHALL be delivered to the agent as multimodal content when the session model supports vision; otherwise a text fallback describing the attachment path SHALL be used. Non-image files SHALL be saved under the active session workspace and referenced in the inbound message text.

#### Scenario: Inbound encrypted image

- **WHEN** a user sends an image in an E2EE room
- **THEN** the bot decrypts and downloads the image and the agent turn includes viewable image content or an explicit fallback description

#### Scenario: Reject oversize media

- **WHEN** an attachment exceeds `max_media_bytes`
- **THEN** the adapter SHALL NOT download the full content and SHALL surface a text notification instead

#### Scenario: Reject HTTP media URL

- **WHEN** an event references a non-mxc media URL
- **THEN** the adapter SHALL ignore the media fetch

### Requirement: Outbound media via send_file

The `send_file` tool SHALL support Matrix targets. Files SHALL be uploaded through the Matrix client (encrypted when required) and sent with the correct `msgtype`, preserving thread context when `auto_thread` is active.

#### Scenario: Send PDF to encrypted room

- **WHEN** the agent calls `send_file` with a valid Matrix target and a workspace file
- **THEN** recipients in Element can download the file from the encrypted room

#### Scenario: Standing send_message grant excludes send_file

- **WHEN** a session has a pre-approved `send_message` grant for a Matrix thread target
- **THEN** `send_file` to the same target SHALL still require approval

### Requirement: Bridge ghost filtering

The adapter SHALL ignore messages from senders matching configured `ignore_user_patterns` (default includes common bridge prefixes such as `^@telegram_`, `^@slack_`, `^@whatsapp_`).

#### Scenario: Bridge ghost message ignored

- **WHEN** a message arrives from `@telegram_123:example.org` and the pattern matches
- **THEN** the gateway SHALL NOT dispatch the message

### Requirement: Auto-join on invite

The bot SHALL automatically accept room invites and join encrypted rooms when invited, initializing encryption sessions as needed.

#### Scenario: Invite to encrypted room

- **WHEN** an authorized user's invite arrives for an E2EE room
- **THEN** the bot joins and can decrypt subsequent messages
