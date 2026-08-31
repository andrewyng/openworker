# OpenWorker

OpenWorker is an open-source AI coworker desktop app. Python backend (FastAPI) + React/TypeScript frontend (Tauri shell).

## Project structure

- `coworker/` -- Python backend: agent engine, LLM providers, connectors, MCP client, server
- `surfaces/gui/` -- React/TypeScript desktop app (Vite + Tauri)
- `stt/` -- Speech-to-text sidecar (Rust)
- `packaging/` -- Installer builds & dev bootstrap
- `tests/` -- Backend pytest suite
- `docs/` -- Design docs and decision logs

## Commands

- Backend: `.venv/bin/openworker-server --cwd <dir> --port 8765`
- Frontend dev: `cd surfaces/gui && npm run dev`
- Tests (backend): `.venv/bin/pytest`
- Tests (frontend): `cd surfaces/gui && npm test`
- Desktop app: `cd surfaces/gui && npm run tauri dev`

## Code conventions

- Python: type hints everywhere, Google-style docstrings
- TypeScript/React: functional components, hooks, Tailwind CSS
- Prefer no comments in code unless explaining non-obvious decisions
- All user-facing strings in English
