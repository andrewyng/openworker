"""Connector tool catalog and local enablement policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional



@dataclass(frozen=True)
class ConnectorToolDef:
    connector: str
    name: str
    label: str
    kind: str
    description: str
    default_enabled: bool = True
    # Which argument names the external object this tool acts ON (channel, recipient, …).
    # Declaring it makes the tool eligible for a task-scoped standing rule (UX-DECISIONS §25):
    # "this automation may call this tool against this exact target without asking". Only
    # single-argument targets are declarable in v1 (no wildcards, no composite targets), and
    # only write tools should declare one — reads never gate, so a rule would be meaningless.
    target_arg: Optional[str] = None
