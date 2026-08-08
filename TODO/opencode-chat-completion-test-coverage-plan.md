# OpenCode Chat-Completions Test Coverage Plan

Status: completed (targeted tests and direct GUI build/typecheck passed)
Date: 2026-08-01

## Objective

補強目前 PR #110 compatibility slice 的 OpenCode-specific test coverage，並清楚區分：

- OpenCode Zen/Go 直接行為；
- 被 OpenCode 使用但屬於共用 provider infrastructure 的行為；
- 明確 deferred、這一階段不應測試或宣稱支援的 transport。

本計畫只針對目前已實作的 OpenAI-compatible `chat.completions` 路徑，不擴大 production scope。

## Current Scope

### In scope

- `opencode_zen` 與 `opencode_go` 的獨立 profile、key、endpoint、configured state。
- `GET <endpoint>/models` credential verification。
- OpenAI SDK `chat.completions` request construction。
- 目前 strict common paid roster：
  - `grok-4.5`
  - `glm-5.2`
  - `glm-5.1`
  - `kimi-k3`
  - `kimi-k2.7-code`
  - `kimi-k2.6`
  - `deepseek-v4-pro`
  - `deepseek-v4-flash`
- Zen-only Free roster 與 retention labels。
- OpenCode conservative capabilities：tools、parallel tool calls、streaming；vision/PDF 不宣稱支援。
- OpenAI-compatible streaming、reasoning content、tool-call accumulation 與 continuation。
- Provider prefix routing、suggestions、cache invalidation。
- GUI provider card、setup、configured isolation 與錯誤狀態。

### Explicitly out of scope

本計畫不新增或測試下列 transport 的實作支援：

- OpenCode `/responses`。
- Anthropic-compatible `/messages`。
- Gemini-specific endpoints。
- 目前未納入 strict common `/chat/completions` intersection 的 paid model families。
- 任何需要 real API key、credits 或 live OpenCode service 的測試。

## Coverage Interpretation

以下測試檔案是 mixed-scope，不應將整個檔案的 coverage 視為 OpenCode coverage：

| File | Responsibility |
|---|---|
| `tests/test_opencode_contract.py` | OpenCode-specific contract and catalog tests |
| `tests/test_providers.py` | OpenCode cases plus shared provider registry/matrix behavior |
| `tests/test_provider_router.py` | OpenCode lifecycle/routing cases plus shared manager/router behavior |
| `ProviderSetup.test.tsx` | OpenCode GUI cases plus existing provider form cases |
| `coworker/providers/openai_provider.py` | Shared OpenAI-compatible transport exercised by OpenCode IDs |
| `coworker/providers/router.py` | Shared routing/cache infrastructure with OpenCode prefixes |
| `coworker/server/manager.py` | Large shared manager; only selected methods are feature-relevant |

Do not use the whole-module percentages of `manager.py` or `registry.py` as acceptance criteria.
Acceptance must be based on the OpenCode scenarios below.

## Existing Coverage To Preserve

目前已覆蓋、除非 regression 出現不需重寫的區域：

- Zen/Go endpoint constants、Bearer header 與 `/models` status handling。
- Profile key precedence、shared env fallback、sibling key isolation。
- Zen/Go model prefix routing與 bare model extraction。
- Zen Free completeness、Zen-only isolation、labels、capabilities。
- Text delta accumulation、terminal assistant turn、reasoning content。
- Streamed tool-call fragments、tool result submission、multi-round continuation。
- Stream cancellation 與 mid-stream disconnect propagation。
- Provider-specific cache invalidation。
- GUI cards、shared logo、separate help links、independent configured state。

## Work Plan

### Phase 0: Establish Baseline

- [ ] Run the existing backend OpenCode subset and record the result.
- [ ] Run the full GUI suite and record the result。
- [ ] Generate feature-focused coverage for the OpenCode contract, matrix, capabilities,
  registry, router and OpenAI provider modules。
- [ ] Record that no live network call is allowed。
- [ ] Keep generated coverage output under `temp/`; do not add reports or lockfiles to the PR。

Validation baseline:

```powershell
uv run pytest tests/test_opencode_contract.py tests/test_provider_router.py tests/test_providers.py -q
pnpm --dir surfaces/gui vitest run
```

### Phase 1: P0 Provider/Profile Invariants

Files:

- `tests/test_opencode_contract.py`
- `tests/test_provider_router.py`

- [ ] Add a direct configured-state test for the intentional env behavior:
  - set `OPENCODE_API_KEY`;
  - leave both persisted profiles empty;
  - assert both OpenCode cards remain `configured == False`;
  - persist only Zen and assert Zen is true while Go remains false;
  - ensure the environment key is not returned in provider `values`.
- [ ] Add a symmetric lifecycle matrix covering:
  - Zen only;
  - Go only;
  - both configured with different keys;
  - remove Zen while preserving Go;
  - remove Go while preserving Zen.
- [ ] Assert the persisted keys remain under exactly `provider:opencode_zen` and
  `provider:opencode_go`; no `provider:opencode` profile is created.
- [ ] Assert client invalidation is limited to the changed provider and does not rebuild or
  remove the sibling client.
- [ ] Assert explicit form key wins over stored key, stored key wins over env fallback, and
  env fallback is available independently to both clients.

Acceptance:

- No test can pass if Zen and Go share a stored key or configured state。
- No test can pass if removing one provider deletes the sibling profile。
- No test can pass if the canonical `provider:opencode` profile reappears。

### Phase 2: P0 Chat-Completions Wire Contract

Files:

- `tests/test_opencode_contract.py`
- If needed, a narrowly scoped shared transport test near the existing OpenAI provider tests。

- [ ] Keep the existing SDK-boundary tests for `base_url`, `api_key`, bare model ID, messages,
  tools, and stream options。
- [ ] Add a transport-level mock test for the final HTTP request URL:
  - Zen must issue `.../zen/v1/chat/completions`;
  - Go must issue `.../zen/go/v1/chat/completions`;
  - the request must carry `Authorization: Bearer <key>`;
  - no real HTTP request is allowed.
- [ ] Parameterize provider error propagation for HTTP/SDK failures covering at least 400, 401,
  429 and 500; assert no blind retry is introduced.
- [ ] Add a malformed/empty completion response case and document the expected failure behavior
  rather than silently accepting an invalid assistant turn.
- [ ] Confirm non-streaming requests do not accidentally include `stream=True` and streaming
  requests include the usage option required by the current implementation。

Acceptance:

- Both endpoints are proven at the final chat-completions boundary, not only by checking the
  SDK constructor's `base_url`.
- The tests remain SDK/http mock based and require no API key or credits。

### Phase 3: P0 Catalog and Transport Boundaries

Files:

- `tests/test_opencode_contract.py`
- `tests/test_providers.py`

- [ ] Keep an exact equality guard for the eight paid common IDs on both Zen and Go。
- [ ] Keep an exact equality guard for the seven Zen Free IDs。
- [ ] Assert every exposed OpenCode catalog entry has `transport == "openai"` and belongs to
  the implemented `chat.completions` profile。
- [ ] Assert Free IDs are present under `opencode_zen:` only, absent under `opencode_go:`, and
  carry both `Free` and `data may be retained` labels。
- [ ] Assert unsupported families are not exposed by either picker or suggestion path:
  `/responses`, `/messages`, Gemini-specific IDs, and the explicitly omitted paid families。
- [ ] Assert custom IDs under either OpenCode prefix use conservative OpenCode capabilities even
  when the model name contains `gpt`, `gemini`, or another generic vendor token。
- [ ] Keep recommendations in the exposed catalog and verify Zen/Go recommendations are distinct
  and selectable without introducing unsupported IDs。

Acceptance:

- Adding a deferred transport or an unverified model to the picker must fail the tests。
- A Free model appearing in Go must fail the tests。
- Generic model-name heuristics must not override OpenCode capabilities。

### Phase 4: P1 Agent-Path Edge Cases

Files:

- `tests/test_opencode_contract.py`

Only add these cases if the shared OpenAI provider behavior changes or a regression is found:

- [ ] Empty content-only chunks before the terminal turn。
- [ ] Tool-call fragment with missing optional `id`/name fields after the first fragment。
- [ ] Invalid tool-call JSON and its explicit error behavior。
- [ ] Stream ending without a finish reason, if the provider implementation supports that case。
- [ ] Cancellation after the first tool fragment without consuming later fragments。

Do not duplicate already-covered normal text, reasoning, tool accumulation, continuation,
cancellation, and disconnect tests merely to increase the test count。

### Phase 5: P1 GUI OpenCode Failure Matrix

Files:

- `surfaces/gui/src/providers/ProviderSetup.test.tsx`

- [ ] Keep the real `useProviderSetup` + `ProviderCards` + `ProviderForm` success-flow test。
- [ ] Add verify failure for Zen and Go:
  - error is visible;
  - save is not called;
  - `onSaved` is not called;
  - the form remains open。
- [ ] Add save failure after successful verify:
  - error is visible;
  - refresh and navigation do not occur。
- [ ] Add refresh failure after successful verify/save:
  - error is visible;
  - success callback and delayed navigation do not occur。
- [ ] Add removal failure and successful removal tests verifying only the selected provider changes。
- [ ] Assert both cards use separate provider names and that the Go help link remains
  `https://opencode.ai/go` while Zen uses `https://opencode.ai/zen`。
- [ ] Keep the shared-logo identity assertion so the two cards cannot silently register different
  logo assets。

### Phase 6: P1 Optional API-Level Coverage

Only implement this phase if provider setup endpoints are part of the PR acceptance boundary and
existing server tests can exercise them without adding a new test framework:

- [ ] Add a small server integration test for OpenCode verify success/failure through the existing
  API route。
- [ ] Add set/remove API tests proving the response does not expose API keys。
- [ ] Reuse existing test client and state-directory fixtures。

This phase is lower priority because manager-level tests already cover the provider lifecycle and
the server layer is a thin delegation wrapper。

## Test Organization Rules

- Keep OpenCode-specific assertions in `test_opencode_contract.py` unless they require an existing
  router/manager fixture from another test file。
- Do not label shared `openai_provider.py` coverage as OpenCode-only coverage。
- Do not add `respx` or another HTTP mocking dependency solely for this plan; use the repository's
  existing monkeypatch/fake-client patterns unless a final URL test cannot be implemented safely
  without it。
- Never use live OpenCode endpoints, real API keys, credits, or persisted private fixtures。
- Do not add generated coverage files, `temp/` contents, lockfiles, or unrelated provider tests。

## Acceptance Criteria

- [ ] All current OpenCode backend tests pass。
- [ ] All GUI tests pass and the GUI build/typecheck passes。
- [ ] The configured-state env fallback invariant has a direct Python test。
- [ ] The final `/chat/completions` URL and Bearer header are verified for both Zen and Go with mocks。
- [ ] The exact paid and Free catalogs are guarded by equality tests。
- [ ] Deferred transports and unsupported model families remain absent from matrix and suggestions。
- [ ] Profile save/remove/cache behavior is independently verified for both providers。
- [ ] No test requires a live key, credits, network access, or private fixture。
- [ ] No unrelated full-module coverage target is used as a release gate。

## Validation Commands

From the repository root:

```powershell
uv run pytest tests/test_opencode_contract.py tests/test_provider_router.py tests/test_providers.py -q
uv run pytest tests/test_opencode_contract.py tests/test_provider_router.py tests/test_providers.py tests/test_provider_verify.py -q
git diff --check
git status --short
```

From `surfaces/gui`:

```powershell
pnpm vitest run
pnpm run build
```

Before completion, inspect the diff and confirm that only OpenCode chat-completions behavior,
its independent provider lifecycle, and the corresponding tests were changed. Deferred transport
support must remain explicitly documented as deferred rather than inferred from generic provider
tests.

## Execution Record

Executed by OMO on 2026-08-01:

- [x] Added direct OpenCode profile/configured-state and env-fallback coverage。
- [x] Added/strengthened chat-completions contract, catalog, transport and capability tests。
- [x] Added GUI verify/save/refresh/remove/blur-save failure and timer-lifetime coverage。
- [x] Backend targeted suite: 116 passed。
- [x] Backend suite including `tests/test_provider_verify.py`: 132 passed。
- [x] `git diff --check` passed。
- [ ] GUI build/typecheck: blocked by pnpm ignored `esbuild` build script。
- [x] No commit, push, live API call, generated lockfile, or new dependency was retained。

At the initial execution point, the plan remained in progress because the pnpm wrapper could not
run under the existing `esbuild` build-script policy; direct local binaries later supplied the
equivalent test, typecheck and build validation recorded below.

Supplemental review fixes executed on 2026-08-01:

- [x] Added the direct empty-profile/env configured-state invariant test。
- [x] Added mocked final Zen/Go `/chat/completions` URL and Bearer-header tests。
- [x] Expanded provider error coverage to real mocked SDK HTTP responses for 400, 401, 429 and
  500, with retry isolation。
- [x] Added explicit malformed empty-completion behavior coverage。
- [x] Added manager save/remove invalidation isolation coverage。
- [x] Replaced the GUI fake isolation test with real-hook Zen/Go save flows。
- [x] Expanded GUI verify/save failure coverage to both providers and added successful removal with
  sibling preservation。
- [x] Backend OpenCode suite after supplementation: 137 passed。
- [x] GUI ProviderSetup tests after supplementation: 26 passed。
- [x] GUI full suite after supplementation: 104 passed。
- [x] Direct GUI `tsc --noEmit` and `vite build` passed。

The repository `pnpm vitest`/`pnpm run build` wrappers still trigger the existing ignored
`esbuild` build-script policy; the direct local binaries were used for equivalent test, typecheck
and build validation.
