# Desktop guide design review — 2026-11-04

Branch: `feat/sdk-docs-design-previews-20261104`.

Two separate previews retain the current Desktop guide Markdown and all SDK
snippets. These were initially isolated from the other documentation routes.

- **Studio / Apple-inspired:** softly tinted introduction, clear type hierarchy,
  light syntax-highlighted examples, collapsible navigation and a quiet contents rail.
- **Editorial / Notion-inspired:** white page, black text, restrained controls,
  denser navigation and flatter code/disclosure surfaces.

Both support TypeScript/Rust selection, independent Codex/Claude selection,
copying, navigation filtering, section links and the actual example source drawer.

Validation: production build (21 pages) and existing docs tests passed. Browser
checks exercised both designs, synchronized language tabs, copy feedback and
clipboard content, guide filtering, installation selection and source-file browsing.
The screenshots and example files remain served by the existing preparation
pipeline. No runtime/model behavior changed, and no live model calls were made.

## Selected direction and rollout

The user selected Studio and requested a restrained article/sidebar plus soft
disclosures without divider lines. Implemented across all 18 public SDK guides
on `feat/sdk-studio-docs-20261104`, retaining Markdown and the original URLs.
The old Starlight presentation layer is replaced by shared Astro components;
Pagefind search, source manifests, syntax highlighting and language tabs remain.
The comparison previews stay available but are excluded from search/sitemaps.

Qualification: default/versioned builds, all-guide internal links and fenced-code
counts, renderer edge cases and the existing docs tests. Browser review covers
the Desktop disclosure, OpenShell page, cloud deployment/table content, guardrails
code, full-text search/navigation, tabs and source browsing. No SDK runtime or
example application code changes.
