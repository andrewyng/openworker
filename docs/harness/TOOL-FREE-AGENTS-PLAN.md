# Tool-free agents: implementation and qualification ledger

November 2, 2026. User request: DLAI needs a guide agent with no tools or optional
capabilities, alongside its existing coding/planning agent. Public integration
baseline: `openworker-harness-sdk` at `673e5d62`.

Implementation branch: `fix/harness-tool-free-agents`. Worktree:
`/Users/rohit/fleet/ro4d/ow-harness-tool-free`. Independent qualification snapshot:
`feat/tool-free-agents-20261102`, worktree
`/Users/rohit/fleet/ro4d/ow-tool-free-qualification`.

## Scope

1. Remove the minimum-tool/capability validation requirement in agent and bundle
   loading while retaining maximum counts and name/schema checks.
2. Do not insert implicit question/history tools into a genuinely empty selected
   capability catalog. Retain their existing behavior for coding and other
   tool-bearing agents.
3. Exercise registration and real streamed text turns in Rust embedded/managed
   and TypeScript managed execution. Inspect actual HTTP provider bodies for
   empty tool definitions and reject unsolicited tool calls before any effect.
4. Run existing coding/planning/approval and runtime regressions, document the
   supported shape and provide a tested source pin.

## Progress

- Located an existing uncommitted patch on `fix/harness-tool-free-agents` in a
  separate worktree. Initially left it untouched and reviewed a four-file
  snapshot independently. The user then supplied the originating agent's handoff
  and explicitly authorized takeover, validation, commit and push on that branch.
  Preserve the original four-file patch before carrying the reviewed additions
  back; do not publish from an unrelated review branch by mistake.
- Snapshot removes the two validation floors and conditions helper insertion on
  a nonempty selected catalog. Runtime already rejects tools absent from that
  catalog before authorization. No new approval mode or tool-disabling flag.
- Added six real HTTP/SSE Rust SDK scenarios (three provider protocols × embedded
  and managed) and three TypeScript real-child scenarios. Each checks startup and
  dynamic registration, streamed text before completion, empty provider catalogs,
  and four unsolicited tool names with zero callback/approval/tool events.
- Strengthened empty registered-selection coverage with a populated registry and
  updated the old invalid-agent test to retain the 64-tool maximum check.
- Qualification completed using synthetic loopback providers, not live Anthropic/
  OpenAI accounts. No paid API calls, OpenShell changes or cloud resources.

## Verification results

Passing a synthetic provider fixture does not establish
availability or compatibility of a particular real model; DLAI's final Claude/
OpenAI integration checks should use the tested source commit and their providers.

- macOS Rust HTTP/SSE SDK tool-free matrix: **6 passed**, no failures/ignores.
- macOS TypeScript managed HTTP/SSE tool-free matrix: **3 passed**, none skipped.
- Existing macOS runtime/coding/policy suites: **100 passed**. Existing Rust SDK
  unit/coding/desktop suites: **24 passed**, two PostgreSQL-only desktop cases
  ignored. Linux-only agents/reviewer suites are qualified separately below.
- Existing TypeScript coding/presets/config/transport/desktop/OpenRouter-streaming
  regressions: **30 passed**, none skipped.
- SDK tool-free target strict Clippy: **passed**. TypeScript build, syntax, Git
  whitespace and **23 local documentation links** checked.
- Linux ARM64 SDK unit/agents/reviewer/coding/tool-free suites: **33 passed**, one
  PostgreSQL-only reviewer case ignored. This includes **6 tool-free** scenarios
  and existing coding/planning/approval/team behavior.
- Linux ARM64 TypeScript actual-child matrix: **3 passed**, none skipped. Ran with
  one CPU and no external network. Host TypeScript final rerun: **3 passed**.
- Both platforms retain all three provider protocols and the strict assertion
  that the first text arrives before provider completion. The initial Linux
  TypeScript fixture incorrectly started its streaming fallback timer before cold
  host registration/startup. A timing trace proved the fallback could fire before
  the first provider request. Start that deadline at the provider's first write;
  do not weaken the streaming assertion or change production transport behavior.
- TypeScript Linux ran the cached Node 24 executable in the cached Rust/Debian
  image, which supplies the existing Linux test host's OpenSSL runtime libraries.
  Used a task-owned writable crate-extraction cache after an initial read-only
  registry mount could not extract a Linux-only dependency. No host cache edits,
  package installation or external network were needed.
- The original four-file handoff is preserved in a named Git stash; the complete
  reviewed change is carried back into `fix/harness-tool-free-agents` for commit
  and push. Source behavior was qualified independently from this same snapshot;
  subsequent ledger updates do not change executable code.

[Sanitized evidence](evidence/tool-free-guides-20261102.json). Full logs and
task-owned native/Linux artifacts remain under
`/Users/rohit/fleet/ro4d/ow-tool-free-qualification/.ow-service-tool-free-review/`.
No real-provider or Windows qualification is claimed. The ignored PostgreSQL
cases are unchanged regressions and are not reported as passing.

[Usage and test commands](TOOL-FREE-AGENTS.md).
