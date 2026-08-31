# OpenCode Provider Blockers Plan

## Goal

Resolve the remaining High findings in the OpenCode provider and GUI setup flow:

- Establish one authoritative OpenCode model catalog.
- Test the real `useProviderSetup` verify/save/refresh flow.
- Prevent stale ProviderSetup timers from causing incorrect navigation.

## Scope

Allowed files:

- `coworker/providers/opencode_contract.py`
- `coworker/providers/matrix.py`
- `coworker/providers/registry.py`
- `coworker/server/manager.py`
- `tests/test_opencode_contract.py`
- `tests/test_provider_router.py`
- `tests/test_providers.py`
- `surfaces/gui/src/providers/ProviderSetup.tsx`
- `surfaces/gui/src/providers/ProviderSetup.test.tsx`

Do not modify unrelated files, generated artifacts, secrets, or Git history.

## Work Items

### 1. Centralize the OpenCode catalog

- Define a structured catalog in `opencode_contract.py` containing, for every Zen
  and Go model:
  - provider tier;
  - model ID;
  - display label;
  - transport/profile metadata;
  - free-model/data-retention metadata where applicable;
  - recommendation priority.
- Derive all of the following from that catalog:
  - Zen and Go model rosters;
  - transport maps;
  - chat-completion model maps;
  - matrix `ModelEntry` rows;
  - registry recommended models;
  - manager suggested models.
- Remove duplicate model-ID literals and separately maintained recommendation maps
  from consumers.
- Preserve every currently supported Zen/Go model ID. Add a baseline preservation
  test so future catalog edits cannot silently remove one.
- Keep provider-specific labels and the Zen free-model retention caveat unchanged.

### 2. Exercise the production GUI flow

- Add a small test harness that renders `useProviderSetup` and the real
  `ProviderCards`/`ProviderForm` components.
- Mock only API boundaries such as `getProviders`, `verifyProvider`,
  `setProvider`, and `removeProvider`.
- Test the successful production sequence:
  1. select a provider;
  2. edit fields;
  3. click the real Test control;
  4. verify the request;
  5. save the provider;
  6. refresh providers;
  7. show success and invoke `onSaved`/navigation only after refresh succeeds.
- Test failure behavior for verify, save, refresh, remove, and blur-save:
  - visible error is rendered;
  - success state is not shown;
  - `onSaved` is not called;
  - the form remains open;
  - navigation does not occur.

### 3. Make ProviderSetup timers race-safe

- Centralize cancellation for `backTimer` and `fieldSavedTimer`.
- Clear timers and set refs to `null` on unmount.
- Cancel existing timers before opening another provider, returning to the gallery,
  or scheduling a replacement timer.
- Guard timer callbacks with mounted state and a provider/operation token so an old
  save cannot close a newly selected provider.
- Add fake-timer tests covering unmount, provider switching, gallery navigation,
  and replacement of an existing timer.

## Acceptance Criteria

- No OpenCode model ID or recommendation is duplicated across production catalog
  consumers.
- The complete current Zen/Go roster is preserved by a regression test.
- GUI tests execute the real hook and production controls, not fake state callbacks.
- Successful setup requires successful verify, save, and refresh.
- Any mutation or refresh failure is visible and leaves the form open.
- Stale timers cannot navigate away from a newly selected provider.
- Targeted tests and build pass independently.
- Final Git diff contains only in-scope source/tests/docs and no generated files.

## Validation

From the repository root:

```powershell
python -m pytest tests/test_opencode_contract.py tests/test_provider_router.py tests/test_providers.py -q
git diff --check
git status --short
```

From `surfaces/gui`:

```powershell
npm test -- --run src/providers/ProviderSetup.test.tsx
npm run build
```

Before completion, inspect the relevant diff hunks and confirm all acceptance
criteria against the implementation rather than relying only on test output.
