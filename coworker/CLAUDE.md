# coworker/ Claude Code Guide

## Overview
This directory contains the Python backend for OpenWorker - the core agent engine that handles:
- Agent reasoning and decision making
- Tool execution and permission checking
- Memory and conversation storage
- Integration with external services via connectors
- Model provider management and routing
- Skill system and automation framework
- WebSocket server for GUI communication

## Key Subsystems

### Agent System (`agents/`)
- `base.py`: Base agent class defining the interface
- `chat.py`: Conversational agent for general assistance
- `code.py`: Coding assistant with development tool expertise
- `cowork.py`: General-purpose coworker agent
- `myhelper.py`: Personal assistant for individual tasks
- `registry.py`: Agent discovery and instantiation

### Core Engine (`engine.py`)
- Main turn processing loop
- Agent interaction and tool execution coordination
- Permission checking and user approval workflows
- Action planning and execution tracking

### Memory System (`memory/`)
- `sqlite_store.py`: Persistent conversation and context storage
- `base.py`: Memory interface definition
- `tools.py`: Memory-related agent tools

### Connectors System (`connectors/`)
- **Adapters**: Real-time messaging platforms (Slack, Telegram)
- **Senders**: Stateless message sending to various services
- **Gateway**: Inbound webhook handling and routing
- **Relay Clients**: Cloud relay connections for managed services
- **Tool Definitions**: MCP-compatible tool specifications
- **Adapters**: Third-party service integrations (GitHub, Jira, etc.)

### Model Providers (`providers/`)
- **Base Interface**: Standardized provider contract
- **Specific Providers**: OpenAI, Anthropic, Google Gemini, Ollama, etc.
- **Router**: Model routing, caching, and capabilities detection
- **Registry**: Provider discovery and configuration
- **Matrix**: Curated model recommendations and labels

### Tools System (`tools/`)
- Individual tool implementations (file operations, web search, etc.)
- Tool permissions and risk assessment
- Tool composition and chaining capabilities
- Custom tool development framework

### Skills System (`skills/`)
- Progressive capability disclosure mechanism
- Skill categorization and dependency tracking
- Skill activation and deactivation workflows
- Built-in skills for common agent capabilities

### Automation System (`automation/`)
- Scheduled task management (`scheduler.py`)
- Persistent storage for automation rules (`store.py`)
- Automation-specific tools (`tools.py`)
- Data models for scheduled work (`models.py`)

### Guardrails (`guard/`)
- `ruleset.py`: GuardRuleSet — load YAML rules, evaluate tool calls with command match patterns (contains/prefix/regex), argument matching, and fan-out concurrency limits
- `logger.py`: GuardLogger — dedicated audit log with timestamped ALLOW/DENY entries per tool call
- `config_loader.py`: GuardConfigLoader — load/cache YAML config with file-change detection for hot reload
- `middleware.py`: GuardMiddleware — layered authorization combining PermissionEngine, GuardRuleSet, and GuardLogger; fail-safe on exceptions (deny + needs_user)

### Security and Permissions
- `permissions.py`: Main permission evaluation engine
- `risk.py`: Risk classification system for tools
- `secrets.py`: Secure credential storage and management
- `config.py`: Allowed command lists and security policies

## Development Guidelines

### Adding New Agents
1. Inherit from `agents.base.BaseAgent`
2. Implement required methods: `initialize()`, `step()`, `shutdown()`
3. Register in `agents.registry.AGENT_REGISTRY`
4. Follow existing patterns for prompt construction and tool usage
5. Add unit tests in `tests/test_*agent*.py`

### Adding New Connectors
1. Choose the appropriate integration pattern:
   - Real-time: Implement `BasePlatformAdapter` (like Slack/Telegram)
   - Stateless: Extend `connectors.senders` 
   - Polling: Implement periodic checkers in `connectors.integration_tools`
2. Follow security patterns for credential handling
3. Add to `connectors.make_adapter()` factory function
4. Create corresponding tests in `tests/test_*connectors*.py`

### Adding New Model Providers
1. Implement `ProviderClient` interface from `providers.base`
2. Add descriptor to `providers.registry.DESCRIPTORS`
3. Handle API key resolution following existing patterns
4. Implement both `complete()` and `stream()` methods
5. Add model capability detection via `providers.capabilities`
6. Add to `providers.matrix.MATRIX` if it meets curated criteria
7. Add comprehensive tests in `tests/test_*provider*.py`

### Adding New Tools
1. Implement function following tool signature conventions
2. Add appropriate risk classification in `risk.py`
3. Register in the appropriate tool category
4. Add schema documentation for agent consumption
5. Write tests in `tests/test_tools_*.py`
6. Consider permission implications and test accordingly

### Modifying Permissions System
1. Understand the `RiskClass` system in `risk.py`
2. Follow the principle of least privilege
3. Test both allowed and denied scenarios
4. Consider impact on different modes (PLAN, INTERACTIVE, AUTO)
5. Update corresponding tests in `tests/test_permissions_*.py`

### Working with the Agent Loop
1. Understand the flow in `engine.py`: perceive → think → act → learn
2. Respect the permission system - never bypass user approval for consequential actions
3. Handle `needs_user` decisions properly by pausing for input
4. Manage state properly across turns using the memory system
5. Follow the tool result format for consistent agent consumption

## Code Quality Standards

### Type Hints
- Use Python 3.8+ type hints consistently
- Import typing constructs only when needed
- Use `from __future__ import annotations` for forward references
- Be explicit about Optional types and default values

### Error Handling
- Catch specific exceptions rather than bare `except:`
- Provide meaningful error messages to users
- Log unexpected errors for debugging
- Fail gracefully and maintain system stability
- Use custom exception types when appropriate

### Logging
- Use the standard `logging` module
- Follow existing logger naming conventions (`logging.getLogger("coworker.subsystem")`)
- Log at appropriate levels (DEBUG, INFO, WARNING, ERROR)
- Avoid logging sensitive information (tokens, passwords, etc.)

### Performance Considerations
- Avoid blocking operations in async contexts
- Use efficient data structures and algorithms
- Consider caching for expensive operations
- Profile before optimizing - measure first
- Be mindful of memory usage in long-running processes

### Testing Practices
- Write tests before implementing new features (TDD when possible)
- Achieve meaningful test coverage (>80% for new code)
- Test both happy paths and error conditions
- Use fixtures to reduce test setup duplication
- Mock external dependencies appropriately
- Test edge cases and boundary conditions

## Common Patterns to Follow

### Dependency Injection
- Prefer constructor injection over global state
- Make dependencies explicit in function signatures
- Use interfaces/abstract classes for pluggable components
- Keep classes focused and loosely coupled

### Configuration Handling
- Use dataclasses for configuration objects
- Provide sensible defaults
- Validate configuration at startup
- Allow override via environment variables or user settings

### Resource Management
- Properly initialize and clean up resources
- Use context managers (`with` statements) when applicable
- Handle async resource cleanup in `__aexit__` or similar
- Prevent resource leaks in long-running processes

### Event Handling
- Follow observer/pub-sub patterns for loose coupling
- Use asyncio primitives for event coordination
- Handle event ordering and race conditions appropriately
- Provide clear unsubscription/disposal mechanisms

## Debugging and Troubleshooting

### Logging Strategies
- Enable debug logging for specific subsystems when needed
- Trace request/response flows through the system
- Monitor performance metrics and resource usage
- Correlate logs across components using request IDs

### Common Issues
- Circular imports: Refactor to break dependencies
- Async/await confusion: Ensure proper coroutine handling
- Permission denied errors: Review risk classifications and allowlists
- Model provider failures: Check API keys and network connectivity
- Memory leaks: Monitor object retention and cleanup

### Diagnostic Tools
- Use Python's built-in `debugger` or IDE debugging tools
- Profile performance with `cProfile` or similar
- Inspect state through available introspection methods
- Check logs in the application data directory