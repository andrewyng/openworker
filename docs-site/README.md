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
