"""Headless one-shot runs: `openworker run` (coworker/headless/runner.py).

Give it a task, a folder and a model; it builds the same engine the desktop app uses,
runs the task to the end with nobody present, and leaves a record behind: every event,
the final conversation, a summary with tokens and cost, the audit log, and a trajectory
in the ATIF interchange format (coworker/headless/trajectory.py). Questions, folder
requests, pinned installs and approval cards are answered by the engine's attendance
rules (coworker/unattended.py), never by a person and never by a policy file.
"""
