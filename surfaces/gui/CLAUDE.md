# surfaces/gui/ -- React/TypeScript desktop app

Vite + React + Tauri + Tailwind CSS.

## Key directories

- `src/components/` -- React components (Composer, Settings, Sidebar, etc.)
- `src/providers/` -- Model provider setup UI
- `src/connectors/` -- Connector configuration UI components

## Key files

- `src/App.tsx` -- Main app shell
- `src/api.ts` -- Backend API client (fetch-based)
- `src/types.ts` -- TypeScript type definitions
- `src/styles.css` -- Tailwind + custom CSS
- `src/theme.ts` -- Theme (light/dark/system) management
- `src/tauri.ts` -- Tauri-specific native APIs

## Conventions

- Functional components with hooks (no classes)
- Tailwind CSS for styling (custom classes prefixed in styles.css)
- Settings/preferences stored server-side via `api.ts` functions
- Import types from `../types` or `../api` as needed
- Test files co-located as `*.test.tsx` next to component
