# 1. Provider on/off toggle: keep credentials, split the model list in two

Date: 2026-07-30

## Status

Accepted

## Context

LLM providers (`coworker/providers/registry.py`) had exactly one status: **configured**
— does the provider have a usable key/credentials, stored in a `provider:<name>` profile in
the file-backed `SecretStore` (`coworker/secrets.py`; no SQL database in this app). There was
no way to temporarily stop using a configured provider without deleting its stored key via
`remove_provider`.

We added an explicit on/off toggle in Settings ▸ Models, reusing the `enabled` flag shape
already established for connectors (`ConnectorSettings.enabled` in
`coworker/connectors/config.py:35`, sourced as `profile.get("enabled", True)`).

## Decision

1. **Storage**: `enabled: bool` lives in the same `provider:<name>` SecretStore profile as
   `api_key`/`base_url`. Absent key = `True`, so every already-configured install stays
   enabled with no migration. `remove_provider` still wipes the whole profile (key +
   `enabled`), so a removed-then-reconfigured provider starts enabled again.

2. **No auto-switching**: disabling a provider never touches `default_model`. The existing
   "no model connected" composer state (`model_ready` in `get_settings()`) is the fallback
   when the default model's own provider gets disabled — deliberately the same state a
   fresh install already shows, not a new one.

3. **Defense in depth in the router**: `ProviderRouter._client_for`
   (`coworker/providers/router.py`) independently refuses to build a client for a disabled
   provider (raises `ProviderDisabledError`), checked ahead of its cache on every call — not
   just at build time. This covers paths that don't go through the composer's own filtering,
   e.g. resuming a session that still references a model from a since-disabled provider.

4. **Two model lists, not one**: `get_settings()` now returns both `models` (selectable —
   filtered by `configured` AND `enabled`; feeds the composer's live model picker) and
   `curated_models` (unfiltered — every provider's persisted curated-list entries; feeds
   Settings ▸ Models' per-provider checklist, `ModelChecklist.tsx`).

   Before this split there was only `models`, and both UI surfaces read it. That was safe
   while the only filter was `configured`, because the checklist is only ever mounted for a
   *configured* provider (`ManageTabs.tsx` renders a read-only preview instead when
   unconfigured) — so for every provider the checklist could actually show, all of its
   curated entries were guaranteed present in `models`. Adding `enabled` as a second filter
   broke that invariant: a provider can now be configured-but-disabled, the checklist still
   mounts (correctly — the user is managing its curated list, not choosing it for a chat), but
   `models` no longer contains that provider's entries. The checklist's checkboxes rendered
   already-added models as unticked, and toggling one appeared to silently remove it from the
   curated list — it hadn't actually moved. Caught by manual browser testing, not by the
   automated suite (see Consequences).

## Consequences

- A provider's key/base_url/custom fields are never touched by the toggle; disabling and
  re-enabling requires no re-entry.
- `_selectable` (`coworker/server/manager.py`) is the single choke point for "is this model
  usable right now," shared by the composer picker and `model_ready` — any future filter on
  usability belongs there, not duplicated elsewhere.
- **Two lists must stay two lists.** Anything that needs "what's usable right now" (the
  composer picker, `model_ready`) must read `models`. Anything that manages the user's
  curated add/remove list (the Settings ▸ Models checklist) must read `curated_models`.
  Collapsing them back into one "for simplicity" reproduces the bug in point 4 the moment a
  second selectability filter (like `enabled`) exists alongside `configured`.
- Regression coverage for the whole feature — REST round-trip, manager state, the
  `models`/`curated_models` split, `model_ready`, and the router's own enforcement — is
  consolidated in `tests/test_provider_enabled.py` rather than scattered across
  `test_server.py`/`test_settings.py`/`test_provider_router.py`, so it can be read and run as
  one unit. The equivalent frontend round-trip (toggle off → composer picker shrinks →
  toggle on → restored) lives in `surfaces/gui/e2e/provider-keys.spec.ts`.
