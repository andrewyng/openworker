# Desktop guide design review — 2026-11-04

Branch: `feat/sdk-docs-design-previews-20261104`.

Two separate previews retain the current Desktop guide Markdown and all SDK
snippets. No redesign has been applied to the other documentation routes.

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

Next: user chooses a direction before any wider application of the design.
