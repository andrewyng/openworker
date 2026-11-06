# Install OpenWorker

The release candidate is `0.1.0-beta.1`. These registry commands are the public
installation contract. Until the candidate is published, use the locally prepared
archives described below; a preview of this guide is not a publication announcement.

## TypeScript

```sh
npm install @openworkerai/sdk@0.1.0-beta.1
```

Use Node.js 20 or newer. This is a Node application dependency, not a browser
library. npm installs the matching native runtime package for your OS/CPU. The SDK
starts that child process and communicates over private stdio. There is no Python
dependency, install script or runtime download.

Do not omit optional dependencies unless you provide your own matching executable.
For Electron packaging, the starter copies the executable out of node_modules into
the app's resources. Keep it outside ASAR archives.

## Rust

```sh
cargo add openworker-sdk@0.1.0-beta.1
cargo add tokio --features macros,rt-multi-thread
cargo add futures-util anyhow
```

Use Rust 1.96 or newer. Cargo installs the SDK and its dependencies without a repo
checkout. The default SDK runs the agent loop in your process. Bundled coding tools
and managed subprocess execution also need the helper executable:

```sh
cargo install openworker-sdk --version 0.1.0-beta.1 --bin openworker-harness --locked
```

Set `OPENWORKER_EXECUTABLE` to its absolute path during development, or pass
`.executable(path)` when building the harness. Desktop starters bundle the helper
for you. You can also use a matching prebuilt GitHub Release archive.

## Test a prepared candidate before publication

A prepared release directory contains npm tarballs, the native archive, checksums
and release metadata. Install the client and your platform package together:

```sh
npm install /absolute/release/openworkerai-sdk-0.1.0-beta.1.tgz \
  /absolute/release/openworkerai-harness-runtime-darwin-arm64-0.1.0-beta.1.tgz
```

Replace the platform filename for your machine. Rust consumers can pin the public
feature branch's tested commit until crates.io promotion. The release tools also
verify a separate consumer against the extracted `.crate` archives.

Next: [desktop app](desktop.md) or [server app](server.md).
