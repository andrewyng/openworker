# surfaces/gui/ Claude Code Guide

## Overview
This directory contains the desktop application frontend - a React/TypeScript application packaged with Tauri for cross-platform desktop deployment.

## Key Components

### src/
React/TypeScript frontend code:
- `App.tsx`: Root application component
- `components/`: Reusable UI components
  - `InboxConfigure.tsx`: Connector configuration UI
  - `SlackDetail.tsx`: Slack workspace configuration
  - Transcript, chat, settings, and other UI components
- `api.ts`: Backend communication layer (WebSocket and REST)
- `tauri.ts`: Tauri bridge for system integration
- `hooks/`: Custom React hooks
- `utils/`: Utility functions

### src-tauri/
Rust-powered Tauri shell:
- `src/lib.rs`: Main Tauri application logic
- `Cargo.toml`: Rust dependencies
- Handles window management, system tray, and native integrations

### e2e/
Playwright end-to-end tests (hermetic, using mocks):
- `inbox.spec.ts`: Connector setup and management
- `slack-workspaces.spec.ts`: Slack integration tests
- `ui-refill-*.spec.ts`: UI refresh and update tests
- `fixtures.ts`: Test data and mock servers

### e2e-live/
Live end-to-end tests (require actual services):
- `api-smoke.spec.ts`: Basic API connectivity tests
- Other tests requiring real backend services

## Development Guidelines

### Component Development
- Follow existing React component patterns in the `components/` directory
- Use TypeScript strictly - no `any` types unless absolutely necessary
- Keep components small and focused on a single responsibility
- Use the existing styling patterns (Tailwind CSS classes)
- Reuse existing components when possible
- Add comprehensive PropTypes or TypeScript interfaces for component props

### State Management
- Follow existing patterns for state and props
- Use React hooks appropriately (useState, useEffect, etc.)
- For complex state, consider if existing patterns can be extended
- Avoid prop drilling - use context or state management solutions when needed

### Backend Communication
- Use the existing `api.ts` patterns for WebSocket and REST communication
- Handle connection states and reconnections appropriately
- Parse and handle server responses consistently
- Show appropriate loading and error states

### Tauri Integration
- Follow the patterns in `src-tauri/src/lib.rs`
- Use the existing Tauri command handlers for backend communication
- Keep frontend and backend communication contracts clear
- Handle permissions and security considerations properly

### Testing
- Write unit tests for components and utilities
- Add e2e tests for critical user flows
- Mock external dependencies appropriately
- Keep tests focused and maintainable

### Styling and UI
- Follow existing Tailwind CSS patterns in the codebase
- Use the existing component library for consistency
- Ensure responsive design works across window sizes
- Follow accessibility best practices
- Use existing icon and image patterns

## Common Tasks

### Adding a New UI Feature
1. Create or modify components in `src/components/`
2. Update routing or app structure if needed in `App.tsx`
3. Add necessary API calls in `api.ts` if backend communication is required
4. Add styles following existing patterns
5. Write tests for the new functionality
6. Update any related documentation

### Modifying Existing Components
1. Understand the existing props and state
2. Make minimal, focused changes
3. Ensure backward compatibility where possible
4. Update tests to reflect changes
5. Test thoroughly in different states

### Working with Tauri
1. Understand the bridge between frontend (TypeScript) and backend (Rust)
2. Follow existing patterns for invoking Tauri commands
3. Handle asynchronous operations properly
4. Consider performance implications of frequent bridge crossings