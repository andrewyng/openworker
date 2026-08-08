# coworker/ -- Python backend

FastAPI server with agent engine, LLM providers, connectors, and tools.

## Key directories

- `server/` -- FastAPI app, manager, CLI entry point
- `providers/` -- LLM providers (OpenAI, Anthropic, Gemini, Bedrock, Vertex, Ollama)
- `connectors/` -- External service integrations (Slack, Telegram, GitHub, email, browser, etc.)
- `agents/` -- Agent implementations (chat, code, cowork)
- `tools/` -- Built-in agent tools (shell, files, git, search, web, etc.)
- `mcp/` -- Model Context Protocol client
- `memory/` -- SQLite-backed conversation memory
- `automation/` -- Cron-based scheduled tasks
- `web/` -- Web fetching and browsing utilities
- `personas/` -- Persona system (YAML-defined agent profiles)
- `skills/` -- Agent skill framework
- `testing/` -- Test utilities (fake Slack server)

## Conventions

- Type hints required on all public APIs
- Google-style docstrings: `"""Summary. (blank line) Details."""`
- Async where possible (asyncio)
- Prefs stored in `prefs.json` via `_prefs` dict in `manager.py`
- Secrets stored via `SecretStore` (encrypted at rest)
- New settings follow: `manager.py` getter/setter -> `app.py` route -> `api.ts` frontend API
