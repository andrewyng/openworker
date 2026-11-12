# Desktop app starters

Desktop scaffolder with registry dependencies by default and an explicit source
checkout mode. Candidate: `create-openworker@0.1.0-beta.3`; registry publication is
a separate release step. See the [installation guide](../../docs/harness/guides/installation.md).

```sh
node harness/desktop-starters/create.mjs /absolute/path/arcade \
  --template electron-game-builder --sdk-root /absolute/path/to/harness
# Or: --template tauri-game-builder
cd /absolute/path/arcade
npm install
npm run dev
```

Use Node 22.12+ / npm 11+. The source workflow builds the Rust helper with Cargo;
pass `--runtime /absolute/path/openworker-harness` to bundle a prebuilt helper.
`--sdk-root` selects a different **harness/** source directory. The Rust SDK's
workspace/source dependencies remain outside the generated app; `openworker.json`
records this development dependency. Do not publish machine-specific file paths
as a public package dependency.

Both starters provide model settings, agent chat, approvals/questions, cancellation
and a playable preview. Electron uses TypeScript callbacks over private stdio;
Tauri embeds the Rust SDK. Native helper executables are included for tool-runner
operations. Users install one app, with no Python runtime.

`npm run package` uses Electron Builder or Tauri on the target OS. Supply your
Windows signing / Apple signing and notarization credentials in your own release
pipeline. Test the resulting package on a clean target machine before release.
Windows also needs the matching Visual C++ runtime used by the helper build.
Packaging reads the helper's native executable header to select x64 or ARM64;
it does not assume Node's architecture matches Rust's. Prebuilt Tauri helpers must
match the Rust host target. Cross-compiling or supplying universal binaries needs
an explicit release pipeline, not renaming an incompatible executable.

## Preview boundary

Generated HTML is served through an application-owned custom scheme with its own
restrictive CSP, inside an iframe with only `allow-scripts`. It has no harness/Node
bridge, parent-origin access, network, nested frames, forms or popup capability.
The main UI retains a separate strict script policy. This is not a CPU/memory quota.
The Tauri template deliberately targets Windows/macOS; Linux iframe/native-IPC
origin isolation requires a different multi-webview arrangement before enabling it.

References: [Tauri capabilities](https://v2.tauri.app/security/capabilities/) and
[iframe IPC security advisory](https://github.com/tauri-apps/tauri/security/advisories/GHSA-57fm-592m-34r7).
The app callback itself has host-app privileges and requires ordinary harness
approval. Neither preview isolation nor approval implies an OS sandbox around it.

## Qualification

`node --test harness/desktop-starters/test.mjs` tests both generators and boundaries.
`qualify-electron.mjs <generated-app>` drives real Electron, SDK, Rust runtime and
a loopback model fixture. It verifies approval before execution, playable scripts,
blocked parent/Node access and blocked network requests. Set `OW_PLAYWRIGHT_MODULE`
to a Playwright module path when it is not installed in the ordinary module search
path. This is a protocol/tool integration test, not a live-model quality evaluation.
Set `OW_PACKAGED_APP` to the packaged executable to run the same loop against an
actual app bundle rather than development Electron. Earlier development qualification
covered Mac/Windows Electron, Mac Tauri compilation/bundling, and native WebView
interaction. Those checks predate this public source-layout migration. See the
[Versions and compatibility](../../docs/harness/guides/releases.md) for checks repeated on
this snapshot. Repeat visual recovery/gameplay checks and clean installer tests on
your target platforms, and reassess dependency advisories before publishing signed
installers.

## Restart recovery

Both starters rebuild the visible conversation, pinned model selection and most
recent successful preview from harness history. Pending, denied and failed preview
calls are not rendered during recovery. No model call or tool execution is needed
to restore the view. The app saves only its session reference and non-secret model
preferences; the harness remains the source of execution history. New game clears
the active reference, without deleting the previous durable session.

The preview is a fresh execution of the previously approved HTML: game score and
other browser-only state reset on app restart. Final assistant text does not reload
an already displayed preview. This starter is not a save-game system.

```sh
cd harness
cargo build -p openworker-sdk --features test-support --bin sdk-test-host
cd ..
OW_TS_TEST_HOST="$PWD/harness/target/debug/sdk-test-host" \
  node --test harness/desktop-starters/recovery.test.mjs
```

`live-acceptance.mjs` is an opt-in, billable Responses test. Set
`OW_LIVE_ACCEPT=1`, `OPENWORKER_EXECUTABLE` to the production harness binary, and
provide `OPENAI_API_KEY` through your secret manager. The default model is
`gpt-6-sol`; `OW_LIVE_MODEL` overrides it. Keys use memory-only storage. The test
submits a synthetic game prompt, restarts while approval is pending, changes the
game in a second turn, and checks canonical recovery without replay. It writes
generated games and a report to a fresh temporary directory. It does not count
as a browser playability test. Use a new state directory by default; the optional
existing-agent mode is for attaching a controlled qualification app only.
