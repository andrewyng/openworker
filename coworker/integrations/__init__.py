"""Local integrations that are deliberately outside the model-facing tool catalog."""

from .kordoc import (
    KORDOC_MCP_TOOL_ALLOWLIST,
    KORDOC_VERSION,
    KordocRuntime,
    KordocRuntimeStatus,
    detect_kordoc_runtime,
)

__all__ = [
    "KORDOC_VERSION",
    "KORDOC_MCP_TOOL_ALLOWLIST",
    "KordocRuntime",
    "KordocRuntimeStatus",
    "detect_kordoc_runtime",
]
