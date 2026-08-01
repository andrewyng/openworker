"""Typed, transport-neutral errors from the Browser Use runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class BrowserRuntimeError(RuntimeError):
    """An expected Browser Use failure with a stable machine-readable code."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


def browser_error(
    code: str, message_text: str, **details: Any
) -> BrowserRuntimeError:
    return BrowserRuntimeError(
        code=code, message=message_text, details=details
    )
