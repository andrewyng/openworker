# Coworker Development Guide

## Overview

The `coworker/` directory contains the core application and backend functionality of OpenWorker.

## Guidelines

- Understand the existing module and its dependencies before making changes.
- Follow the existing architecture and coding patterns.
- Keep changes limited to the requested functionality.
- Avoid unnecessary refactoring.
- Preserve existing public behavior unless the issue explicitly requires a change.
- Reuse existing utilities and abstractions when appropriate instead of introducing duplicate implementations.
- Handle errors consistently with the surrounding code.
- Keep code readable and maintainable.

## Testing

When modifying code in this directory:

1. Run the most relevant tests for the changed functionality.
2. Run additional related tests when the change affects shared functionality.
3. Run the full test suite when practical.

## Change Validation

Before submitting changes:

- Check for linting or formatting issues.
- Verify that imports and dependencies remain correct.
- Confirm that existing functionality is not unintentionally broken.