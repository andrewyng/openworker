# Practical SDK tracks — plan and progress

Scope: product-builder overview; inline installation; working code-directory summarizer and PDF Q&A; actual example source in a docs drawer.

1. Bounded app-side input readers, tool-free SDK agents, Electron and browser/server UIs.
2. Rust agent lifecycle equivalent, packaging/deployment instructions, fixture-backed integration tests.
3. Markdown track rewrite and generated source explorer; remove standalone Installation navigation.
4. Validate examples with the actual Rust host, inspect UI/docs, merge verified work.

Constraints: no cloud provisioning or package publication. PDF accepts text-based files; no OCR. Public multi-user deployments need application authentication and per-user ownership controls. Free disk ~13 GiB: reuse existing runtime/build caches.

## Ledger

- 2026-11-04: worktrees clean at e6907c23; started feat/sdk-example-tracks-20261104. Confirmed empty-tool agents and assistant_delta events in SDK tests. Existing release host available.
- 2026-11-04: implemented Code Atlas (Electron directory picker, bounded snapshot, review list, streamed summary/cancellation) and PDF Notes (upload, bounded PDF worker, page-numbered context, per-document sessions, streamed answers). Both use explicit tool-free agents and app-owned input collection. Added the equivalent native Rust agent layer.
- 2026-11-04: applied overview annotations, moved the diagram after the desktop/web explanation and track choices, removed Installation from public navigation, and rewrote both tracks around creation and packaging/deployment. Full source lives under `harness/examples/quickstart/`.
- 2026-11-04: added a click/keyboard source drawer to Markdown pages. The build copies only explicitly allowlisted example files into static JSON; no filesystem access at runtime. Source tabs, copying, Escape and file switching were inspected in the local site.
- 2026-11-04: 3 example tests passed with no skips using Node 24 and the actual packaged Rust host: directory exclusions/limits; PDF extraction/rejections; streaming, separate PDF contexts, tool-free model requests and application-token/origin enforcement. Production npm audit reported no advisories. The model endpoint was a deterministic local fixture, not a paid/live provider.
- 2026-11-04: Rust example compiled and completed an in-process turn. Exact Markdown TypeScript program ran against the packed npm SDK/native helper; exact Markdown Rust program compiled and completed. Four docs tests passed (tabs, malformed markup, normal/versioned links, exact source manifests), and the production docs build passed.
- 2026-11-04: visually qualified directory selection and streamed summary in Electron, then built and launched the unpacked macOS arm64 app and repeated the turn using its bundled helper outside ASAR. Default macOS signing is explicitly disabled; product signing remains an app release configuration. Browser PDF upload and incremental answer/page reference were visually verified. Screenshots: `/tmp/ow-code-atlas-packaged.png`, `/tmp/ow-pdf-notes.png`, `/tmp/ow-sdk-source-explorer.png`.

## Qualification boundaries

- Complete UI examples use TypeScript (Electron and Node/browser); Rust supplies a compiled/runnable embedded agent layer, not another complete UI implementation.
- The PDF example is one user per instance. Public multi-user use requires application login, document ownership, quotas and retention. PDF text extraction has no OCR. Session history retention is independent of the app's one-hour in-memory document expiry.
- macOS arm64 packaging was exercised. Windows packaging and a fresh Linux Docker deployment have not been exercised in this change. The Docker recipe requires a matching Linux helper; no cloud resources were provisioned.
- Both examples support OpenAI Responses or an Ollama-compatible chat-completions endpoint. This change qualifies transport/lifecycle with a synthetic Responses model; it does not evaluate model answer quality or qualify a live Ollama model.
- Disk remained around 18 GiB free during verification; existing native artifacts and shared Rust targets were reused.

## Follow-up: learning tracks and app-building skill

- 2026-11-04: both guides now start with the app outcome and a real UI screenshot, explain Harness/Agent/Session/Run, then offer a copyable coding-agent prompt and collapsed installation commands before the existing SDK walkthrough. Added small adaptation ideas at the end. The previously executed TypeScript/Rust program fences remain byte-for-byte unchanged.
- Added `skills/openworker-apps/SKILL.md`, track-specific references and Codex UI metadata. The portable skill directs the coding agent to matching repository source and release availability rather than embedding another copy of the apps. It distinguishes TypeScript UI implementations from the native Rust agent layer, and fixture qualification from live-model/platform qualification.
- Added a Node project-local installer for Codex and Claude Code. It copies the same skill to each product's documented project location, records a checkout locator, refuses existing installations and linked destination directories, and does not modify global configuration. Installer tests use temporary projects; no real user project/global skill settings were changed.
- Validation: skill-creator validator passed; installer tests passed for both targets and refusal cases; docs build and default/versioned route, screenshot and source-manifest checks passed. Inspected the guided-build section and collapsed installation UI in the browser. No model calls, cloud resources or application-runtime changes were needed.
- Installation references checked against official Codex (`https://learn.chatgpt.com/docs/build-skills`) and Claude Code (`https://code.claude.com/docs/en/skills`) docs on 2026-11-04. This targets local coding sessions, not Claude web/Cowork uploads.
- 2026-11-04: replaced the two decorative installation terminals with one compact Codex/Claude Code selector and a neutral command surface on both tracks. Selection is independent of the TypeScript/Rust preference. Fixed nested code-frame corner/shadow styling. Verified switching and copying in the browser, light/dark contrast, Markdown fallback preservation, and the production build.
