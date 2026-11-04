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

Adapted from the existing OpenWorker Astro/Starlight docs pipeline in
`ocw-context/site-docs`. It does not require that private repository to build.

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
