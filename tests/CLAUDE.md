# tests/ Claude Code Guide

## Overview
This directory contains the backend test suite for the OpenWorker Python backend. Tests use pytest and cover:
- Unit tests for individual components
- Integration tests for subsystem interactions
- Mock-based tests for external services
- Property-based testing where appropriate

## Test Organization

Tests are organized by subsystem, mirroring the main code structure:
- `test_*.py`: Individual test modules
- `conftest.py`: Shared pytest fixtures and configuration

## Key Test Areas

### Core Functionality
- `test_agent.py`: Agent lifecycle and decision making
- `test_engine.py`: Main turn processing loop
- `test_permissions_*`: Permission engine and risk assessment
- `test_memory.py`: Conversation and context storage
- `test_skills.py`: Skill system functionality

### Providers and Models
- `test_providers.py`: Base provider interface
- `test_*_provider.py`: Specific provider tests (openai, anthropic, gemini, etc.)
- `test_provider_router.py`: Multi-provider routing and caching
- `test_capabilities.py`: Model capability detection

### Connectors and Integrations
- `test_connectors.py`: Core connector functionality
- `test_*_connectors.py`: Specific service integrations (slack, github, etc.)
- `test_mcp.py`: Model Context Protocol implementation
- `test_inbox.py`: Message handling and routing

### Tools and Automation
- `test_tools_*`: Individual tool functionality
- `test_automation.py`: Scheduling and background tasks
- `test_shell.py`: Command execution and permissions
- `test_todo_tool.py`: Todo management tool

### Server and API
- `test_server.py`: FastAPI server endpoints
- `test_cloud_server.py`: Cloud relay functionality
- `test_session*`: Session management

## Testing Guidelines

### Writing Tests
1. **Follow Arrange-Act-Assert pattern**: Clear setup, execution, and verification
2. **Use descriptive test names**: `test_what_when_then_expected_result`
3. **Keep tests focused**: Each test should verify one specific behavior
4. **Mock external dependencies**: Don't make real network calls in unit tests
5. **Use fixtures effectively**: Share common setup in `conftest.py`
6. **Test edge cases**: Boundary conditions, error states, invalid inputs
7. **Maintain test isolation**: Each test should be able to run independently

### Mocking External Services
- Use `unittest.mock` or `pytest-mock` for patching
- Create realistic mock responses that match actual service formats
- For HTTP services, consider using `responses` or `httpx-mock` if needed
- Verify that your code handles both success and error responses from mocks

### Database and State Testing
- Use temporary directories for file-based storage tests
- Clean up test state after each test when necessary
- Consider using transactions or rollbacks for database tests
- Factory boy or similar for test data generation when appropriate

### Async Testing
- Use `pytest-asyncio` for asynchronous tests
- Follow the existing patterns in the codebase for async test structure
- Properly await async operations and handle exceptions

### Performance and Load Testing
- Generally avoid in unit tests; save for dedicated performance tests
- If necessary, use appropriate profiling and benchmarking tools
- Focus on correctness first, performance optimization second

### Test Data and Fixtures
- Keep test data minimal and focused on what's being tested
- Use factories or builders for complex test objects
- Share common fixtures in `conftest.py` when used across multiple test files
- Consider using fixtures for mock services or temporary resources

## Running Tests

### Backend Tests
```bash
# Run all tests
.venv/bin/pytest

# Run specific test module
.venv/bin/pytest tests/test_engine.py

# Run tests matching a pattern
.venv/bin/pytest -k "test_something"

# Run with coverage
.venv/bin/pytest --cov=coworker tests/

# Run specific test function
.venv/bin/pytest tests/test_engine.py::test_specific_function
```

### Frontend Tests
```bash
# From surfaces/gui/ directory
npm test              # Unit tests
npm run e2e           # End-to-end tests (hermetic)
npm run e2e-live      # End-to-end tests (requires live services)
```

## Best Practices

1. **Don't test implementation details**: Focus on behavior and outcomes
2. **Make tests deterministic**: Avoid timing-dependent tests when possible
3. **Keep tests fast**: Unit tests should run in milliseconds
4. **Test failure paths**: Ensure error handling works correctly
5. **Update tests when fixing bugs**: Add regression tests for fixes
6. **Remove commented-out code**: Keep test files clean
7. **Follow existing patterns**: Consistency makes the test suite maintainable
8. **Write tests before fixing bugs** (TDD approach) when feasible