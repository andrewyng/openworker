# Arcade desktop starter

Use Node.js 22.12+ and npm 11+. Source builds also need Rust 1.96+ and the native
platform build tools; supply a prebuilt runtime to avoid rebuilding it in Electron.

Run `npm install`, then `npm run dev`. Open **Model settings**, supply your provider
API, endpoint and exact model ID, and enter an API key if required. Use Ollama with
Chat Completions and `http://127.0.0.1:11434/v1` for a running local model.

Run `npm run package` on Windows or macOS to build the installer for that OS.
Unsigned development packages are not signed/notarized releases. Production
distribution needs your signing identity and platform-specific release testing.

This source starter builds the Rust helper with Cargo unless `--runtime` supplied
a prebuilt executable during scaffolding. It uses the SDK checkout in
`openworker.json`; runtime execution does not need Cargo or Python.

The generated UI is React. Electron runs the TypeScript SDK in its main process;
Tauri runs the Rust SDK in process. Both deliver one desktop app. The runtime helper
is bundled for external tool execution. The app owns presentation, model settings
and project metadata; the harness owns the agent loop, durable sessions and approvals.

## Trust boundary

The starter explicitly selects sandbox **off** for its app-owned preview callback.
Human approval remains on. The callback has app privileges; it is not an isolated
ToolRunner. Generated HTML runs in an opaque-origin iframe, with scripts enabled
but no Node/native IPC, same-origin access, network, frames, forms or popups.
Do not add `allow-same-origin` or a generic native-command bridge to that iframe.

MCP integrations are separate: explicitly register a server and select qualified
tools on the agent. OpenShell requires a supported provisioned execution environment;
an unavailable sandbox never silently becomes sandbox off.

Changing an agent's instructions/tools requires a new versioned agent name; existing
sessions preserve their original identity. Keys use the platform secret store,
not project metadata, renderer persistence or model prompts. Re-register callbacks
when your application restarts. Lost actions are not replayed automatically.
