# SDK packaging and documentation ledger

Branch: `feat/sdk-release-docs-20261103`, based on `caf461a9`.

1. **Release contract and packages:** registry-ready Rust dependency closure,
   exact-version npm native packages, runtime discovery/compatibility, artifacts,
   registry-mode starters and CI packaging without publication.
2. **Markdown documentation and site:** reuse the existing Astro/Starlight approach;
   plain Markdown remains readable on GitHub, generated site gets navigation,
   links, search, code tabs/progressive disclosure and the existing clean visual style.
3. **Executable examples and local preview:** compile/run exact guide examples,
   install packed artifacts outside the checkout, build/visually inspect the site,
   provide a local preview for review. Partner clean-machine acceptance follows.

## November 3 — start

- User approved packaging, docs/site and tested examples. Registry publication and
  hosted deployment are not performed during preparation.
- Found `ocw-context/site-docs`: Astro/Starlight, plain Markdown ingestion, separate
  navigation metadata, custom light/dark styling. Reuse in the public repository;
  do not modify or publish unrelated ocw-context work.
- Found unpublished path-only Rust dependencies, npm `private` packages, source-only
  starters and runtime compaction fixtures outside the crate boundary. Address
  these before claiming a clean registry installation.
- Candidate version: `0.1.0-beta.1`; npm platform dependencies avoid postinstall or
  runtime downloads. Preserve explicit custom-runtime and source-starter workflows.
- Disk: 28 GiB free after isolated worktree creation. Reuse task-owned build caches.

## November 3 — packaging and site qualification

- Rust: nine publishable crates now have versioned path+registry dependencies,
  package metadata and licenses. Runtime compaction contracts moved inside the
  crate boundary; independent Python contract/layout tests reference the new home.
  All nine actual archives were extracted into a separate consumer and built
  without source-checkout dependencies, including the exact Rust QuickStart.
- Cargo 1.96.0's multi-crate verification hit a temporary-registry checksum error.
  The isolated archive consumer is the documented local workaround; normal registry
  verification/publication remains a promotion gate. No crate was uploaded.
- Registry resolution exposed a yanked yoke-derive lock entry and newer Tokio's
  deprecated generic AsyncFd constructor. Updated to cached yoke-derive 0.8.4 and
  Tokio 1.54.1; use explicit registration with documented owned-descriptor lifetime
  invariants. SDK strict Clippy passes; 19 SDK unit tests, five desktop lifecycle
  cases and six provider/tool-free fixtures pass (two external desktop cases skipped).
- TypeScript: versioned platform-package discovery, checksum/platform checks and
  host release negotiation. Existing configuration/transport suite: 18 passed;
  native package mismatch/corruption test passed. Actual client/native tarballs
  installed offline in a separate npm consumer and started without an executable
  override. Source npm manifests remain private; staging creates publishable
  manifests with the optional native-package matrix.
- Starters support registry dependencies by default and explicit source mode.
  Nine release/starter/layout/link tests pass. A manual artifact-only CI workflow
  defines native Mac/Windows/Linux ARM64/x64 jobs, Rust archives and static docs.
  YAML parsed locally. Other platform jobs have not been run in this qualification.
- Docs: adapted the existing Astro/Starlight pipeline into public `docs-site`;
  12 source Markdown pages, search, dark/light theme, Rust disclosure panels and
  generated links. Production build passed. Default viewport and 390×844 layout
  were inspected; search returned seven guardrail results; normal viewport restored.
- Both exact Markdown programs compiled and streamed a completed response from
  a loopback Responses fixture: Rust from extracted archives, TypeScript from
  installed npm tarballs. No live provider key or paid model used.
- Local preview: http://127.0.0.1:4322/docs/sdk/ (4321 was already occupied and was
  left untouched). Captures: `/tmp/openworker-sdk-docs-desktop.jpg` and
  `/tmp/openworker-sdk-docs-mobile.jpg`.
- Disk: approximately 25 GiB free. Shared Rust/npm caches reused; the initial
  isolated consumer made a 459 MiB cache that can be removed after qualification.
- Remaining before promotion: confirm npm scope/crate-name ownership, run the
  six target artifact jobs and partner clean-machine acceptance, signing review,
  actual registry dry-run/publication and post-publication install verification.
  The current local artifacts are a rehearsal, not a registry release.

## Completion checkpoint

- Implementation and docs committed as `e9aa2373`; clean-commit macOS ARM64
  candidate prepared in `.sdk-release-macos-final/archives`. Release metadata says
  `dirty: false`, identifies that commit and explicitly says `not-published`.
- Final npm smoke installs the client, platform host and starter tarballs into a
  new directory outside the checkout, compiles the exact TypeScript guide, creates
  a registry-mode app and streams a completed fixture response. Passed.
- Final nine `.crate` archives were extracted into a separate consumer; the exact
  Rust guide compiled and ran. Both final Rust/TypeScript guide programs streamed
  completed responses against the local fixture. No repo path dependencies used.
- Documentation pipeline test passed for both `/docs/sdk` and a versioned base;
  source Markdown is unchanged by generation. Architecture HTML is served as a
  local reference instead of opening source code on GitHub. Production build and
  nine release/starter/layout tests passed. Manual CI now includes package-consumer
  smoke and documentation checks; it does not publish.
- Registry-source manifests are intentionally private on the npm side; the release
  preparer creates publishable tarball manifests. crates.io manifests are ready
  with exact internal versions. Publishing either remains an explicit owner action.
- Native artifacts for other OS/CPU targets and partner clean-machine acceptance
  remain the next release gates. No registry accounts, packages, tags or GitHub
  Releases were created. No hosted website was deployed.
