## 1. Infrastructure and target format

- [x] 1.1 Add `encode_matrix_target` / `decode_matrix_target` and extend `parse_target` with `matrix/` branch in `coworker/connectors/base.py`
- [x] 1.2 Add unit tests for matrix target round-trip, invalid input, and Slack/Telegram regression
- [x] 1.3 Add `matrix` to `PLATFORMS` in `coworker/connectors/config.py` with user allowlist loading from profile
- [x] 1.4 Add `ConnectorDescriptor` for `matrix` in `descriptors.py` (fields, validate via whoami, profile schema)
- [x] 1.5 Add `ABOUT` / `ACCESS` copy in `catalog_copy.py`
- [x] 1.6 Add `matrix-nio[e2e]>=0.25` to `pyproject.toml` `[project.optional-dependencies] messaging` and document libolm prerequisite

## 2. E2EE adapter core

- [x] 2.1 Create `coworker/connectors/matrix_adapter.py` using matrix-nio AsyncClient with required E2EE
- [x] 2.2 Implement crypto store under state dir, recovery key import via `matrix_crypto_bootstrap.py`, stale OTM key detection with fail-closed connect
- [x] 2.3 Implement `matrix_event_to_event()` mapper for text messages with mention detection and room/DM chat_type
- [x] 2.4 Wire `make_adapter("matrix", …)` and gateway registration in `manager._build_and_start_gateway`
- [x] 2.5 Implement encrypted outbound `send()` on the adapter (not httpx-only sender)
- [x] 2.6 Implement auto-join on invite for encrypted rooms

## 3. Hermes session and routing behavior

- [ ] 3.1 ~~Create `MatrixSessionStore`~~ deferred — use `dm_session`, `_route_mention`, `subscriptions` until per-user room scoping ships
- [x] 3.2 Implement `require_mention`, `free_response_rooms`, thread continuation without re-mention
- [ ] 3.3 Implement `session_scope`, `dm_mention_threads`, `group_sessions_per_user` routing (+ optional `MatrixSessionStore`)
- [x] 3.4 Integrate with `_route_mention`, `dm_session`, and `subscriptions` paths using matrix-encoded thread targets
- [x] 3.5 Apply `ignore_user_patterns` at ingress (`process_notices` deferred)

## 4. Reaction-based Inbox interactions

- [x] 4.1 Extend `InteractionEvent` with `interaction_kind` and `reaction_key` (backward compatible for Slack)
- [x] 4.2 Add `PendingReactionStore` keyed by (room_id, prompt_event_id)
- [x] 4.3 Implement `MatrixAdapter.send_interactive()` — post prompt text and register emoji map
- [x] 4.4 Handle inbound `m.reaction` → `InteractionEvent` with encoded resolution (✅ / ♾️ / ❌ / numbers); `approval_require_sender` in adapter when `allowed_reactor` set
- [x] 4.5 Extend `manager.mirror_inbox_item` for matrix channel targets
- [x] 4.6 Wire ♾️ reaction to standing grant API (resolution `always` via inbox)
- [ ] 4.7 Add optional lifecycle reactions (👀/✅/❌) separate from approval emoji map

## 5. Media inbound and outbound

- [x] 5.1 Implement inbound mxc download with E2EE decrypt and `max_media_bytes` guard
- [x] 5.2 Deliver inbound images as multimodal `image_url` parts with vision-capability fallback
- [x] 5.3 Save inbound file/audio/video under `{crypto_store}/inbound/` and reference in tagged text
- [x] 5.4 Reject non-mxc media URLs in events
- [x] 5.5 Implement `_send_matrix_file` and register in `DEFAULT_FILE_SENDERS`
- [x] 5.6 Update `send_message` / `send_file` tool descriptions to include Matrix target format

## 6. GUI and packaging

- [x] 6.1 Add Matrix/Element logo to `surfaces/gui/src/connectors/registry.tsx`
- [ ] 6.2 Add Matrix connector detail view (credentials + advanced Hermes flags)
- [x] 6.3 Document libolm install for macOS/Linux in connector instructions
- [ ] 6.4 Track DMG libolm bundling as release follow-up (document in design if not implemented in this change)

## 7. Test harness and verification

- [ ] 7.1 Create integration mock homeserver harness (sync + send + reaction + mxc stub) — not started
- [x] 7.2 Tests: target encoding, mapper, allowlist room/user, mention gating
- [x] 7.3 Tests: reaction approve/deny/sender-bound/approve-always
- [ ] 7.4 Tests: inbound image multimodal + outbound send_file (mock crypto layer for CI)
- [ ] 7.5 Tests: `group_sessions_per_user` session isolation (blocked on 3.3)
- [ ] 7.6 Run messaging test job with libolm available; document skip behavior when absent
