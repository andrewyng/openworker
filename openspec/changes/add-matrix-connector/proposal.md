## Why

Teams running self-hosted Synapse or Element Server Suite (ESS) use Element as their primary chat surface, often with end-to-end encryption (E2EE) enabled by default. OpenWorker today supports two-way messaging only on Telegram and Slack; there is no path for a coworker agent to join encrypted Matrix rooms, receive @mentions and media, or resolve Inbox approvals from Element. Adding a first-class `matrix` connector closes that gap for private deployments without relying on OpenWorker Cloud relay.

## What Changes

- Add a **`matrix` connector** (descriptor, GUI branding, SecretStore profile) for self-hosted homeservers.
- Implement **`MatrixAdapter`** using **`matrix-nio[e2e]`** with **`e2ee_mode=required`** (fail closed — no silent downgrade).
- Implement **cross-signing bootstrap** in `matrix_crypto_bootstrap.py` (recovery key → self-signing device signature; matrix-nio has no built-in SSSS).
- Introduce a **Matrix-specific target address format** (`matrix/<b64(room_id)>[/thread/<b64(event_id)>]`) for `send_message` and `send_file`.
- Align inbound behavior with **Hermes Matrix semantics** where implemented: mention gating, threads, room/user allowlists, bridge ghost filtering; DM via `dm_session`, mentions via `_route_mention`.
- Extend **Inbox mirroring** to Matrix via **emoji reactions** (✅ / ♾️ / ❌ / numbered options) instead of Slack Block Kit buttons.
- Support **inbound and outbound media** (image, file, audio, video) through Matrix `mxc://` URIs with size limits and E2EE encrypt/decrypt.
- Register `matrix` in gateway **`PLATFORMS`**, `DEFAULT_SENDERS`, and `DEFAULT_FILE_SENDERS`.
- Add **`matrix-nio[e2e]>=0.25`** under the existing `messaging` optional extra; document **libolm** as a system prerequisite.
- **No managed cloud relay** for Matrix (manual token connect only at launch).

## Capabilities

### New Capabilities

- `matrix-connector`: E2EE Matrix messaging connector — connect/auth, sync loop, Hermes-aligned routing, reaction-based Inbox interactions, and encrypted media in/out.
- `messaging-target-format`: Matrix target token encoding and parsing used by outbound tools and standing grants.

### Modified Capabilities

_(none — no existing `openspec/specs/` baseline in this repo)_

## Impact

- **Backend**: `coworker/connectors/` (new adapter, senders, config, descriptors, catalog_copy, tools), `coworker/server/manager.py` (inbound routing, Inbox mirror), `coworker/connectors/base.py` (`InteractionEvent`, target parsing).
- **Frontend**: `surfaces/gui/src/connectors/registry.tsx`, new or extended connector detail UI for Matrix credentials (task 6.2).
- **Dependencies**: `pyproject.toml` `[messaging]` extra → `matrix-nio[e2e]`; system `libolm` 3.x (dev docs + release packaging follow-up).
- **Tests**: unit tests for target encoding, reactions, crypto bootstrap, mapper; integration harness deferred (task 7.1).
- **Security**: encrypted crypto store on disk, recovery key in SecretStore, federated/untrusted input treated as hostile (`mxc://` only, room allowlists encouraged).
