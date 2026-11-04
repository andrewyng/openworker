# Desktop track

Read these files in the matching OpenWorker checkout:

- `docs/harness/guides/desktop.md` — the teaching sequence and Rust/TypeScript lifecycle snippets.
- `harness/examples/quickstart/README.md` — setup, runtime resolution and packaging.
- `harness/examples/quickstart/desktop/main.mjs` and `desktop/preload.cjs` — trusted main process, narrow IPC, directory dialog, cancellation and shutdown.
- `harness/examples/quickstart/src/directory.ts` — bounded source snapshot and exclusions.
- `harness/examples/quickstart/public/desktop.html`, `desktop.js` and `style.css` — complete Electron UI.
- `harness/examples/quickstart/rust/src/main.rs` — runnable in-process Rust agent layer; it is not a complete Rust GUI.

## Product to build

For Code Atlas, let the user choose a directory, review selected files, request a summary and see streamed text. Create a new session for each summary so unrelated directories do not share context. Explain structure and entry points with file references, without adding shell execution or write access.

The source reader excludes hidden/dependency directories, symlinks and obvious credential filenames, and limits file count and bytes. It is not a secret scanner or hostile-filesystem sandbox. Preserve disclosure of what is sent to the configured model. If the user requests a different product, adapt input collection and instructions accordingly.

## Run and package

From `harness/examples/quickstart`, follow the README environment setup, then use `npm run setup` and `npm run desktop`. `npm test` uses the real Rust helper when `OPENWORKER_EXECUTABLE` is configured, with a synthetic model endpoint.

`npm run package:desktop` creates an unpacked Electron app. The matching helper is bundled outside ASAR. Package on the target OS; signing is an explicit release configuration. For a Rust-native UI, embed the demonstrated lifecycle in the chosen UI framework and supply its platform packaging; do not claim the Rust CLI is already a desktop window.

Verify directory selection, streamed output, cancellation/error handling and the packaged helper path. Check which target platforms were actually exercised before reporting support.
