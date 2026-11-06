# Prepare an SDK release

`manifest.json` is the release version, public Rust dependency closure and native
platform matrix. This directory prepares artifacts; it never publishes them.
See [versions and compatibility](../../docs/harness/guides/releases.md).

## Local candidate

```sh
npm --prefix harness/typescript-sdk ci
npm --prefix harness/typescript-sdk run build
cargo build --manifest-path harness/Cargo.toml --locked --release \
  -p openworker-sdk --bin openworker-harness --features security-adapters,compliance-export
node harness/release/prepare.mjs \
  /absolute/target/release/openworker-harness aarch64-apple-darwin /absolute/new-candidate
```

Choose the actual Rust target and executable extension. The output directory must
not exist. The preparer checks architecture and (on the matching native host)
version, creates the platform npm package, client/starter tarballs, a standalone
runtime archive, SHA256SUMS and `release.json`. It refuses to overwrite a candidate.
Native compilation and clean-machine testing are required on every claimed target.

The npm client is `@openworkerai/harness-sdk`; matching runtime packages are
`@openworkerai/harness-runtime-<platform>-<arch>`. The same candidate includes
`@openworkerai/build-with-sdk-skills`, which installs the shared Desktop/Web
skill into a Codex or Claude Code project. The skill package has no runtime or
SDK dependency. To prepare just that archive without compiling Rust:

```sh
node skills/prepare.mjs /absolute/new-skills-candidate
```

Run this command from the repository root. See [skill installation](../../skills/README.md).

Source npm manifests remain `private` so a plain `npm publish` in the checkout
cannot accidentally publish an incomplete client. Staging removes that marker and
adds the exact-version platform dependencies. This also keeps `npm ci` usable before
the first platform packages exist in the registry. No package uses a postinstall
hook or fetches an executable during startup.

## Rust archives

Package the nine crates listed in `manifest.json` together, in dependency order.
Cargo rewrites path+version dependencies into registry dependencies. For example:

```sh
cargo package --manifest-path harness/Cargo.toml --no-verify \
  -p openworker-artifacts -p openworker-compliance -p openworker-guardrails \
  -p openworker-telemetry -p openworker-tools -p openworker-runtime \
  -p openworker-security-adapters -p openworker-store-postgres -p openworker-sdk
node harness/release/check-examples.mjs /absolute/new-examples
python3 harness/release/verify-crates.py \
  --packages /absolute/target/package --out /absolute/new-consumer \
  --example /absolute/new-examples/desktop.rs
```

The isolated verifier extracts real `.crate` archives and supplies registry patches
pointing only to those extracted crates. It never uses source-checkout dependencies.
Use a shared `CARGO_TARGET_DIR` to avoid duplicate build caches. Before first
publication, Cargo 1.96.0's multi-package verification encountered an internal
“no hash listed” temporary-registry error locally; this separate consumer check
covers archive completeness. Normal `cargo publish --dry-run`/verification against
registry dependencies remains a promotion gate, not a claimed completed upload.

## CI and promotion

`.github/workflows/harness-packages.yml` is manually triggered and only uploads
artifacts. It builds on native macOS/Windows/Linux ARM64/x64 runners and produces
Rust crates and static documentation. It does not grant registry publish permissions.

Before publishing, confirm name ownership and publisher identity, run target-machine
and partner acceptance, confirm checksums/signing, and ensure `release.json` identifies
a clean immutable commit. Do not publish this local dirty-tree rehearsal unchanged.
Publish Rust dependency crates before the SDK facade; publish all platform npm
packages before the client and starter. Use `beta`, not `latest`. Test installation
from the actual registries after promotion. First-package creation and npm trusted
publisher configuration require the organization's registry owner.
