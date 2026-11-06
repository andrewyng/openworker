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

## Studio rollout — 2026-11-04

- Applied the selected Studio layout to all 18 public SDK guides. Articles are bounded to 720 px; sidebar width is 232 px (246 px on wide displays), with the current navigation group expanded. Disclosure sections use soft gray surfaces without the rejected divider lines.
- Kept Markdown and fenced programs unchanged. Replaced the old Starlight presentation components with shared Astro layouts/rendering, while preserving original routes, generated source manifests, language/installer tabs, source browsing, sitemap and full-text Pagefind search. Retained both design previews outside search/navigation/sitemaps.
- Verification: six docs/renderer tests passed; default and `/docs/sdk/0.1.0-beta.1` builds each passed checks for all 18 routes, code-block counts, internal links, image paths, anchors and search assets. The search index contains 18 guides, excluding previews and errors.
- Visually checked Desktop disclosures, Web/source explorer, OpenShell, deployment tables and guardrails code. Verified Rust/TypeScript switching, clipboard copying and search results navigating to the right guide. No SDK runtime, application example or model-call changes.

## Source workspace upgrade — 2026-11-04

- Replaced the flat file list/plain text area with a modern source workspace: actual nested folders, file icons, filtering, keyboard tree navigation, breadcrumb, and a read-only CodeMirror 6 editor with syntax highlighting, line numbers, code folding, in-file search and wrapping control.
- Viewer core is lazy-loaded on drawer open (about 102 KB gzip in the build); language packages load separately on demand. All assets are bundled locally. No editor dependency is added to the harness/SDK binary. Copy retains exact source content. Closing destroys the view and invalidates outstanding loads. Plain source is available if a viewer module fails to load.
- Ran tree/filter/path-validation/language-routing tests along with the existing docs suite; TypeScript checks and the static build passed. Formatting-only adjustments improve the Rust example and its JSON build configuration in the viewer.
- Dependency audit: none of the added CodeMirror packages were flagged. npm reported pre-existing Astro/build-chain advisories in the existing major version (Astro, Vite, sharp and related dependencies). Track a separate framework update/qualification before release; no forced major upgrade was folded into this visual change. The docs output remains static and the preview remains loopback-only.
- Browser qualification passed for TypeScript, Rust, TOML and Dockerfile views; nested folder expansion/collapse; filtered ancestor paths; arrow-key navigation; find-in-file matches; wrapping; exact-source copy; read-only typing rejection; close/reopen cleanup; and both Desktop/Web manifests. Nine docs tests, viewer TypeScript checking and all-guide artifact verification pass.

## Public URL for review — 2026-11-06

- User authorized publishing at `https://openworker.com/docs/sdk/`, without adding marketing links. Existing hosting is S3/CloudFront in `opencoworker-cloud/infra/site.tf`; homepage source remains `ocw-context/site/consumer.html`.
- Added a separate hosting packager that preserves the normal local docs build, removes preview pages/sitemaps, adds `noindex, nofollow` to all published HTML, and creates a checksummed release manifest plus an allowlisted CloudFront router. Deployment writes only `docs/sdk/`, with no root/marketing asset writes or deletes.
- Ten docs tests and all-18-guide artifact checks passed. Routing regression covers slash/no-slash guides, assets, queries, unpublished/missing paths, traversal-shaped paths and unchanged non-doc paths. Static hosting deploys no Astro/Node server or image optimizer. Existing build-chain advisory follow-up remains separate; the packaged content comes only from repository-controlled Markdown/components, without user-generated HTML or server islands.
- Hosting code and runbook live in the infrastructure repository; deployment verification and release revision will be recorded there after activation.
