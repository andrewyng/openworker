# OpenWorker Claude Code Guide

## Overview

OpenWorker is a desktop AI assistant application with a Python backend (coworker/) and a React/Tauri frontend (surfaces/). It uses a multi-agent system with persistent memory, tool integrations via MCP, and a skill system for progressive capability disclosure.

## Key Components

### Backend (coworker/)
- `agent.py`: Main engine assembly
- `agents/`: Different agent personalities (Code, Chat, Cowork, MyHelper)
- `connectors/`: 25+ integrations (GitHub, Slack, Jira, Notion, HubSpot, etc.)
- `engine.py`: Turn engine processing agent interactions
- `mcp/`: Model Context Protocol client for tool integrations
- `memory/`: Persistent SQLite-based memory storage
- `providers/`: LLM providers (OpenAI, Anthropic, Gemini, Ollama, etc.)
- `server/`: FastAPI server exposing agent functionality
- `skills/`: Progressive disclosure skill system
- `tools/`: Agent-available tools (file ops, shell, web search, etc.)

### Frontend (surfaces/gui/)
- `src/`: React/TypeScript frontend
  - `App.tsx`: Root application component
  - `components/`: Reusable UI cards, views, modals
  - `api.ts`: Backend communication layer
  - `tauri.ts`: Tauri bridge code
- `src-tauri/`: Rust-based Tauri shell supervising the frontend
- `e2e/`: Playwright end-to-end tests
- `e2e-live/`: Live tests requiring actual services

### Other Components
- `stt/`: Rust-based speech-to-text engine
- `packaging/`: Installer scripts and dev environment bootstrap
- `docs/`: Design specs and decision logs
- `ui-mocks/`: UI mockups/design assets
- `tests/`: Backend test suite

## Development Guidelines

### Code Style
- Follow existing code patterns in the codebase
- Use type annotations in Python (PEP 484) and TypeScript
- Keep functions focused and single-responsibility
- Write descriptive variable and function names
- Add comments for complex logic

### Python Backend
- Use type hints consistently
- Follow existing error handling patterns
- Use the agent system patterns in `coworker/agents/`
- Follow MCP patterns in `coworker/mcp/` for tool integrations
- Write unit tests in the `tests/` directory

### Frontend (React/Tauri)
- Follow existing React component patterns
- Use TypeScript strictly
- Follow existing state management patterns
- Keep components small and reusable
- Follow existing styling patterns in components

### Making Changes
1. Create a branch for your changes
2. Make your changes following the existing patterns
3. Ensure tests pass (both backend and frontend)
4. Update documentation if needed
5. Submit a pull request with clear description
6. Include screenshots for UI changes
7. Reference any related issues

### Running the Application
See `packaging/setup_dev_env.sh` for development environment setup.
The application uses a client-server architecture where the React/Tauri frontend communicates with the Python backend via WebSocket.

## Issue Reporting
When reporting issues:
1. Check if similar issues already exist
2. Include steps to reproduce
3. Include screenshots if applicable
4. Include relevant logs from both frontend and backend
5. Specify OS and version information
6. Specify which agent/system is involved

## Pull Request Process
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Ensure all tests pass
5. Update documentation as needed
6. Submit pull request with clear description
7. Reference any related issues
8. Await review from maintainers

## License
MIT - see LICENSE file.