# Testing Guide

## Overview

The `tests/` directory contains automated tests for the OpenWorker project.

## Guidelines

- Add or update tests when changing application behavior.
- Keep tests focused on the behavior being changed.
- Follow the existing test organization and conventions.
- Prefer clear and deterministic tests.
- Avoid tests that depend unnecessarily on external services or unstable conditions.
- Reuse existing test helpers and fixtures when appropriate.

## Running Tests

Run the tests relevant to the code being modified first.

After making broader changes, run the complete test suite when practical.

## Test Changes

When adding a new feature:

1. Add tests covering the expected behavior.
2. Include important edge cases where appropriate.
3. Verify that existing tests continue to pass.

When fixing a bug:

1. Add a regression test that reproduces the problem when practical.
2. Make the smallest appropriate code change.
3. Verify that the regression test passes.
4. Run related tests to ensure the fix does not break existing behavior.