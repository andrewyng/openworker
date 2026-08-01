"""External Chrome extension bridge public API."""

from .bridge import (
    AuthenticationError,
    BridgeResult,
    CommandNotFound,
    CommandTicket,
    ExternalBrowserBridge,
    ExternalBrowserBridgeError,
    PairedClient,
    PairingChallenge,
    PairingError,
    SessionNotFound,
    TabNotClaimed,
)
from .protocol import (
    PROTOCOL_VERSION,
    SUPPORTED_COMMANDS,
    SUPPORTED_EVENTS,
    ProtocolValidationError,
)

__all__ = [
    "AuthenticationError",
    "BridgeResult",
    "CommandNotFound",
    "CommandTicket",
    "ExternalBrowserBridge",
    "ExternalBrowserBridgeError",
    "PROTOCOL_VERSION",
    "PairedClient",
    "PairingChallenge",
    "PairingError",
    "ProtocolValidationError",
    "SUPPORTED_COMMANDS",
    "SUPPORTED_EVENTS",
    "SessionNotFound",
    "TabNotClaimed",
]
