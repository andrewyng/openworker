---
id: service-coding
name: Sandboxed coding tools
requires_folder: true
planning: true
---
Work only in the assigned project. Inspect available programs before choosing a
build command. Use the coding tools for bounded file operations and managed
processes. File paths are relative to the workspace. Save important work there.
Process handles belong to the current tool-runner process. A service may replace
that process between turns; check a saved handle before using it, and check the
actual endpoint before restarting a server. Do not assume external network access
is enabled or that a preview server survives a turn.
