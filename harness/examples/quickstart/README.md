# Two small apps built with OpenWorker

- **Code Atlas:** Electron UI selects a code directory, previews the file list and streams a summary.
- **PDF Notes:** browser UI attaches a PDF to its own session and streams answers with requested page citations.
- **Rust agent layer:** native in-process equivalent accepting an application-prepared context file. The complete UI/server examples use TypeScript; the Rust example is a CLI demonstrating the same agent lifecycle.

## Run from this checkout

Node 22.12+ is required (qualified here with Node 24). From this directory:

```sh
npm run setup
```

The example pins published SDK beta.4, which installs native runtime beta.3 automatically. Set `OPENWORKER_MODEL` to an exact model ID. Set `OPENAI_API_KEY` privately. Optional `OPENWORKER_MODEL_URL` selects a compatible OpenAI Responses endpoint. For Ollama, set `OPENWORKER_PROVIDER=Ollama`, an installed model ID, and optionally its chat-completions base URL; no cloud key is used. For PDF Notes with a text-only local model, also set `OPENWORKER_PDF_MODE=text`.

```sh
npm run desktop
npm run web
```

Run one command per terminal. The web app opens at http://127.0.0.1:4352/ . The model credentials stay in Electron's main process or the web server. They are never forwarded to the renderer/browser.

No Rust build or executable override is needed. An explicit `OPENWORKER_EXECUTABLE`
is supported for testing a compatible custom host; unset it for the npm runtime.

## Desktop packaging

```sh
npm run package:desktop
```

This produces an unpacked app in `release/` for local validation, with macOS signing disabled. Run it on the target OS. The runtime is copied into the application's resources outside ASAR. To produce distributable installers, invoke electron-builder with your desired Windows/macOS target and explicit signing/notarization configuration (override `mac.identity` for a signed macOS release). Those credentials belong to your application.

The directory reader takes a bounded snapshot of up to 60 files / 180 KB. It skips hidden files, common build/dependency directories, symlinks, binary files, large files and obvious credential filenames. This is not a secret detector or an OS sandbox. Users review the selected files before sending them to their configured model. Other agents/processes should not mutate the selected tree during capture; do not use this reader as a hostile multi-tenant filesystem boundary.

## Web deployment

This is deliberately a **single-user application instance**. Configure `APP_ACCESS_TOKEN` to expose it beyond loopback; enter that application token in the browser's expandable settings. Use HTTPS at your ingress. Each upload receives a separate agent session and random document ID. Native mode sends the original PDF to a compatible model. Accepted bytes are stored durably by the harness; app document handles expire from memory after one hour. Session storage has separate retention. Restarting loses document handles, so upload again. Concurrent requests for one document are rejected.

For a cloud VM or container service, deploy this server with the npm-selected Linux runtime, a writable state volume and secrets supplied by the platform. It runs as a managed subprocess in the same container. This is not a Harness service-cluster integration.

```sh
# From this example directory. Keep keys in a private env file outside the repo.
docker build -t pdf-notes .
docker run --rm -p 8080:8080 --env-file /private/path/pdf-notes.env -v pdf-notes-state:/state pdf-notes
```

The env file needs `APP_ACCESS_TOKEN`, `OPENAI_API_KEY`, and `OPENWORKER_MODEL`. Optional model endpoint/provider variables work as above. For a shared multi-user product, replace the single application token with your login system, persist user-owned document/session mappings, enforce owner checks and quotas, and configure retention. Do not share this one token among unrelated tenants.

Native mode accepts PDFs up to 8 MiB; provider context/page limits still apply. It does not parse or OCR the file. For local/text-only models, set `OPENWORKER_PDF_MODE=text`. That mode's PDF worker accepts at most 8 MB / 80 pages / 180,000 text characters, with a 15-second timeout and a worker heap limit. It does not execute document JavaScript or render pages. These are resource controls, not a kernel sandbox. PDFs with no extractable text are rejected; OCR and image understanding are outside this example. Citations are model-generated references to supplied page numbers, not independently verified answers.

## Rust equivalent

Prepare content with the same application readers, then send it through the native Rust SDK:

```sh
npm run build
node scripts/export-context.mjs code /path/to/project /tmp/context.json
# Or: node scripts/export-context.mjs pdf /path/to/file.pdf /tmp/context.json
cargo run --manifest-path rust/Cargo.toml -- /tmp/context.json 'Explain the main components.'
```

The Rust crate uses a local path for this source checkout. For dependencies and native-helper setup, follow the [Rust quickstart](../../harness-sdk/README.md). A UI can embed this lifecycle and render its event stream.

## Verification

```sh
npm test
```

Input-reader tests run without a provider. Set `OPENWORKER_EXECUTABLE` to also run the actual Rust host integration suite against an in-process synthetic Responses server. It verifies streaming, no advertised tools, independent PDF sessions and application access controls. This is deterministic SDK qualification, not an LLM-quality evaluation.

For visual QA, `node scripts/fixture-preview.mjs` starts a local PDF app and a synthetic model endpoint. Its generated `.state/sample.pdf` contains fixture data. Never expose this fixture server publicly.
