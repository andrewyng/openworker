# GUI Development Guide

## Overview

The `surfaces/gui/` directory contains the graphical user interface components of OpenWorker.

## Guidelines

- Understand the existing GUI structure before making changes.
- Follow the existing component organization and conventions.
- Keep UI changes focused on the requested issue.
- Avoid unrelated changes to the GUI.
- Reuse existing components and utilities where possible.
- Preserve existing user workflows and behavior unless the issue requires a change.
- Keep UI and application logic separated according to the existing project architecture.

## Testing

When modifying GUI functionality:

- Test the affected functionality directly.
- Run relevant automated tests when available.
- Check that existing GUI behavior remains functional.
- For changes affecting user interactions, verify the relevant user flow before submitting the change.

## Change Validation

Before submitting GUI changes:

- Check for formatting or linting issues.
- Verify that imports and dependencies are correct.
- Ensure the change does not introduce unrelated UI regressions.
- Keep the pull request focused on the requested GUI change.