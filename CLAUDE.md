# OpenWorker Development Guide

## Repository Overview

OpenWorker is a Python-based project with multiple application surfaces and supporting components.

## Repository Structure

- `coworker/` - Core application and backend functionality.
- `surfaces/gui/` - GUI application and related frontend components.
- `tests/` - Automated tests.
- `stt/` - Speech-to-text functionality.
- `ui-mocks/` - UI mock resources and components.
- `packaging/` - Packaging and distribution configuration.
- `docs/` - Project documentation.
- `.github/` - GitHub workflows and repository configuration.

## General Development Guidelines

- Read the relevant existing code before making changes.
- Keep changes focused on the requested issue.
- Follow existing project structure and coding conventions.
- Avoid unrelated refactoring or modifications.
- Prefer small, focused changes that are easy to review.
- Do not modify generated files unless the issue specifically requires it.
- Preserve existing behavior outside the scope of the change.

## Testing

- Run relevant tests for the area being modified.
- Run the full test suite when practical after making changes.
- Do not consider a change complete until the relevant tests pass.

## Pull Requests

- Keep pull requests focused on one issue or feature.
- Clearly describe what was changed and why.
- Include the tests or validation performed.
- Avoid unrelated changes in the same pull request.