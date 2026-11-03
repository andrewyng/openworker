# SDK release contract

The first registry candidate is `0.1.0-beta.1`. This is a public-beta SDK release,
separate from the Python desktop application's versions. Preparing artifacts does
not publish packages or announce a release.

## Distribution

- Rust: `openworker-sdk` on crates.io, plus its public dependency crates. Applications
  use Cargo normally; no repository checkout is required. Internal path dependencies
  also carry exact registry versions. All SDK crates in a release share one version.
- TypeScript/Node: `@openworker/harness` on npm. Exact-version optional platform
  packages contain the native host. npm selects OS/CPU; no install scripts, startup
  downloads, or lookup of an arbitrary binary on PATH.
- Desktop scaffolding: `create-openworker` on npm. Generated Electron/Tauri projects
  use registry dependencies by default; explicit `--sdk-root` retains source development.
- Native runtime: versioned GitHub Release archives with SHA-256 checksums, also
  packaged in the platform npm packages. Rust applications can install the helper
  using `cargo install openworker-sdk --bin openworker-harness --version 0.1.0-beta.1
  --locked`, or bundle a prebuilt archive. An in-process Rust agent loop can still
  need this executable for ToolRunner and bundled coding tools.

## Compatibility

SDK and packaged host versions must match exactly during beta. The wire protocol
stays separately versioned (`0.1`) and capabilities are checked at startup. Explicit
partner-built executables remain supported; capability negotiation is required,
and a reported SDK version mismatch rejects. Legacy hosts without release metadata
remain usable only through explicit executable/bundled configuration, never through
an automatically selected registry runtime.

Beta fixes increment the prerelease version; artifacts are immutable. Storage
format changes require documented migration/recovery instructions. Do not claim
rolling compatibility with old workers or downgrade a storage version marker.
Changes to defaults, error behavior, persistence, permissions and tool schemas
must appear in release notes. No automatic update of a running application's host.

## Artifact targets

Release build targets: macOS ARM64/x64, Windows x64/ARM64, Linux glibc x64/ARM64.
A target becomes supported for a published candidate only after its native CI and
clean-machine acceptance pass. Linux musl, mobile and embedded-specific profiles
are not silently substituted. Windows sandboxing and OpenShell availability retain
their documented platform constraints.

## Promotion

1. Build/test from one immutable commit with a locked dependency graph.
2. Package and verify the Rust dependency closure, npm client/starter and each host.
3. Install the produced archives into fresh consumer projects; test examples.
4. Collect target-platform/partner acceptance and review migration notes.
5. Confirm ownership of crates.io names and the npm `@openworker` scope; configure
   registry publisher identity. Prefer npm trusted publishing rather than a
   long-lived token. Never embed registry credentials in artifacts.
6. Publish Rust dependencies before the facade, platform npm packages before the
   client, then the starter; use npm's `beta` dist-tag, not `latest`.
7. Publish matching GitHub assets, versioned documentation and release notes.

The packaging workflow uploads CI artifacts only. Registry publication, release
promotion, signing and website deployment remain explicit operator actions.

References: [Cargo multiple locations](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#multiple-locations),
[Cargo package](https://doc.rust-lang.org/cargo/commands/cargo-package.html),
[npm optional dependencies](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/#optionaldependencies),
[npm trusted publishers](https://docs.npmjs.com/trusted-publishers/).
