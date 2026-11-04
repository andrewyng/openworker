# OpenWorker SDK documentation site

Plain Markdown in `docs/harness/` is authoritative and readable on GitHub.
`pages.json` chooses the public navigation; `scripts/prepare.mjs` adds site-only
frontmatter and rewrites links. Other technical references remain accessible on
GitHub. Never edit generated `src/content/docs/`.

```sh
npm ci
npm run dev -- --host 127.0.0.1 --port 4321
# http://127.0.0.1:4321/docs/sdk/
npm run build
npm run preview -- --host 127.0.0.1 --port 4321
```

`dist/` is static HTML/CSS/JavaScript, ready for a static host under `/docs/sdk/`.
Search is generated during the production build. The page content is identical
in local preview and deployment; publication is a separate action.

The original Markdown preparation pipeline is retained. Studio now uses a
shared custom Astro layout instead of Starlight presentation components. Pagefind
indexes the 18 public guides after the static build; preview and error pages are
excluded. The site does not require a private repository to build.

## Language tabs in plain Markdown

Wrap alternatives with HTML-comment markers and ordinary headings:

~~~~markdown
<!-- sdk-tabs -->

### TypeScript

```typescript
const session = await agent.newSession();
```

### Rust

```rust
let session = agent.new_session().await?;
```

<!-- /sdk-tabs -->
~~~~

GitHub displays both labelled examples. The site turns them into accessible tabs,
with a shared TypeScript/Rust selection across the page and keyboard navigation
(arrow keys, Home and End). Without JavaScript both examples remain readable.
Use `TypeScript / Electron` and `Rust / Tauri` for starter-command alternatives.
The build rejects missing end markers, duplicate languages and unsupported labels.
Fenced example content is preserved, including `example=desktop` test markers.

## Example source drawer

`<!-- sdk-source: desktop -->` or `<!-- sdk-source: web -->` adds a source button
to a Markdown guide. An accessible dialog opens on click or keyboard activation,
with file navigation, copying and Escape to close. It does not open on hover.

The build reads the explicit allowlist in `scripts/source-examples.mjs` from
`harness/examples/quickstart/` and generates static JSON. There is no second copy
of the example source to maintain, directory scanning, or runtime file access.
New source files must be added deliberately to the manifest. Generated JSON and
content are ignored by Git. Links to the complete examples remain usable on GitHub
and with JavaScript disabled.

For skill-install commands, use `<!-- sdk-install-tabs -->` with `### Codex` and
`### Claude Code` sections, closed by `<!-- /sdk-install-tabs -->`. These render
as a compact, neutral command panel. Set `frame="none"` on the shell fences to
omit decorative terminal title bars. The coding-agent selection is independent
of the TypeScript/Rust selection; both alternatives stay readable on GitHub.

## Studio layout

`src/layouts/StudioDocs.astro` is the shared layout for every public guide. It has
a bounded article column, a compact sidebar that opens the active section,
light syntax-highlighted code, soft gray disclosures and full-text search.
Markdown in `docs/harness/` remains authoritative. The renderer promotes the
first prose sentence into the page introduction and preserves everything else;
table-first guides retain their full content in the article.

- `src/lib/render-guide.mjs`: common Markdown rendering and headings.
- `src/lib/navigation.mjs`: grouped navigation and next-page order.
- `src/styles/studio.css`: shared visual tokens and layout.
- `src/scripts/guide.ts`: copying and section tracking.
- `src/scripts/docs-search.ts`: Pagefind search dialog and compact-screen navigation.
- `scripts/verify-built.py`: all-guide route, internal-link and content checks.

Search is available in the production preview after `npm run build` creates its
index. Run `python3 scripts/verify-built.py dist /docs/sdk` to verify built routes.
Use `OW_DOCS_BASE` for a versioned deployment; all assets, links and search URLs
follow that base. Deployment does not need a Node server.

## Retained design previews

The original comparison routes remain outside navigation and the search/sitemap:
`/docs/sdk/previews/desktop/apple/` and `/docs/sdk/previews/desktop/notion/`.
Studio was selected and applied to all SDK guides; Editorial remains a reference.
The previews share the same content, tabs and source explorer.

## Read-only source workspace

The source drawer uses a collapsible, keyboard-navigable folder tree and a
CodeMirror 6 viewer. The editor is dynamically imported only when the drawer
opens; language support is loaded on demand. It provides syntax highlighting,
line numbers, folding, find-in-file, selectable text and a wrapping toggle.
Copying uses the exact manifest content, not the rendered/virtualized DOM.

`src/lib/source-tree.mjs` owns hierarchy/filtering/language selection;
`src/scripts/source-editor.ts` owns read-only editor configuration;
`src/scripts/source-explorer.ts` owns interaction/lifecycle. Editor views are
destroyed when the dialog closes. A plain-text fallback remains if a viewer chunk
cannot load. The source allowlist remains authoritative; no repository scanning
or server-side file access is added. CodeMirror is a docs dependency, not a Rust
SDK or runtime dependency.
