"""Consequential-action classification and single-use browser approvals.

This policy is intentionally separate from OpenWorker's ordinary permission modes.
Call it immediately before every browser action, even under Full Access:

    decision = policy.classify(request)
    if decision.requires_confirmation:
        approval = policy.issue_approval(request)  # only after explicit user consent
        ...
        policy.consume_approval(approval.token, freshly_revalidated_request)

The request passed to ``consume_approval`` must be rebuilt after revalidating the tab,
origin, snapshot, ref, target metadata, and action arguments.  A token is short-lived
and succeeds exactly once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from .destination import DestinationPolicyError, canonical_origin


_CONSEQUENTIAL_TERMS = re.compile(
    r"\b("
    r"send|submit|publish|post|share|purchase|buy|pay|place\s+order|checkout|"
    r"book|reserve|transfer|wire|subscribe|sign\s*up|accept|agree|"
    r"delete|remove|erase|cancel|close\s+account|deactivate|"
    r"change\s+password|reset\s+password|security|privacy|permission|"
    r"authorize|allow|consent|log\s*in|login|sign\s*in|signin|"
    r"log\s*out|logout|sign\s*out|signout|oauth"
    r")\b",
    re.IGNORECASE,
)
_AMBIGUOUS_TERMS = re.compile(
    r"^\s*(continue|next|confirm|ok(?:ay)?|done|finish|proceed)\s*[.!…]?\s*$",
    re.IGNORECASE,
)
_SUBMIT_TYPES = frozenset({"submit", "image"})
_SENSITIVE_CLASSIFICATIONS = frozenset(
    {
        "secret",
        "credential",
        "authentication",
        "personal",
        "personal_data",
        "connector",
        "connector_data",
        "local_file",
        "local_file_content",
        "financial",
        "health",
    }
)
_SAFE_ACTIONS = frozenset(
    {
        "browser_open_url",
        "browser_history",
        "browser_snapshot",
        "browser_snapshot_scope",
        "browser_snapshot_more",
        "browser_screenshot",
        "browser_tabs",
        "browser_select_tab",
        "browser_close_tab",
        "browser_console",
        "browser_hover",
        "browser_scroll",
        "browser_close",
    }
)


class BrowserActionPolicyError(RuntimeError):
    pass


class ApprovalRequired(BrowserActionPolicyError):
    pass


class ApprovalInvalid(BrowserActionPolicyError):
    pass


class ApprovalExpired(BrowserActionPolicyError):
    pass


@dataclass(frozen=True)
class TargetMetadata:
    """Trusted metadata derived from the current Playwright locator."""

    role: str = ""
    accessible_name: str = ""
    element_type: str = ""
    inside_form: bool = False
    submits_form: bool = False
    consequence_known_safe: bool = False
    page_risk_hints: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, Any]]) -> "TargetMetadata":
        if value is None:
            return cls()
        hints = value.get("page_risk_hints") or ()
        if isinstance(hints, str):
            hints = (hints,)
        return cls(
            role=str(value.get("role") or ""),
            accessible_name=str(value.get("accessible_name") or value.get("name") or ""),
            element_type=str(value.get("element_type") or value.get("type") or ""),
            inside_form=bool(value.get("inside_form")),
            submits_form=bool(value.get("submits_form")),
            consequence_known_safe=bool(value.get("consequence_known_safe")),
            page_risk_hints=tuple(str(item) for item in hints),
        )


@dataclass(frozen=True)
class BrowserActionRequest:
    session_id: str
    tab_id: str
    snapshot_id: str
    ref: str
    origin: str
    action: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    target: TargetMetadata = field(default_factory=TargetMetadata)
    data_classification: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        origin: str,
        action: str,
        arguments: Optional[Mapping[str, Any]] = None,
        target: Optional[Mapping[str, Any] | TargetMetadata] = None,
        data_classification: Sequence[str] = (),
    ) -> "BrowserActionRequest":
        normalized_target = (
            target
            if isinstance(target, TargetMetadata)
            else TargetMetadata.from_mapping(target)
        )
        return cls(
            session_id=str(session_id),
            tab_id=str(tab_id),
            snapshot_id=str(snapshot_id),
            ref=str(ref),
            origin=canonical_origin(origin).value,
            action=str(action),
            arguments=dict(arguments or {}),
            target=normalized_target,
            data_classification=tuple(
                sorted(
                    {
                        _normalize_label(item)
                        for item in data_classification
                        if str(item).strip()
                    }
                )
            ),
        )


@dataclass(frozen=True)
class ActionPolicyDecision:
    requires_confirmation: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BrowserActionApproval:
    token: str
    expires_at: float
    session_id: str
    tab_id: str
    snapshot_id: str
    ref: str
    origin: str
    action: str
    parameter_hash: str
    data_classification: tuple[str, ...]


class BrowserActionPolicy:
    """Conservative, mode-independent consequential-action gate."""

    def __init__(
        self,
        *,
        approval_ttl_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        binding_key: Optional[bytes] = None,
    ) -> None:
        if approval_ttl_seconds <= 0:
            raise ValueError("approval_ttl_seconds must be positive")
        self._ttl = float(approval_ttl_seconds)
        self._clock = clock
        self._binding_key = bytes(binding_key or os.urandom(32))
        if len(self._binding_key) < 16:
            raise ValueError("binding_key must contain at least 16 bytes")
        self._approvals: dict[str, BrowserActionApproval] = {}
        self._lock = threading.Lock()

    def classify(self, request: BrowserActionRequest) -> ActionPolicyDecision:
        request = self._validated_request(request)
        reasons: list[str] = []
        action = request.action.lower()
        target = request.target
        label = _normalize_text(
            " ".join(
                [
                    target.accessible_name,
                    target.element_type,
                    target.role,
                    *target.page_risk_hints,
                ]
            )
        )
        classifications = set(request.data_classification)

        if classifications & _SENSITIVE_CLASSIFICATIONS:
            reasons.append("sensitive_data_disclosure")
        if target.element_type.lower() == "password":
            reasons.append("credential_disclosure")

        if action in {"browser_fill", "browser_type"} and (
            classifications & _SENSITIVE_CLASSIFICATIONS
            or target.element_type.lower() == "password"
        ):
            reasons.append("sensitive_input")

        if action == "browser_press":
            key = _normalize_text(str(request.arguments.get("key") or ""))
            if key in {"enter", "return"} and target.inside_form:
                reasons.append("form_submission")

        if action == "browser_click":
            if (
                target.submits_form
                or (
                    target.inside_form
                    and target.element_type.lower() in _SUBMIT_TYPES
                )
            ):
                reasons.append("form_submission")
            if _CONSEQUENTIAL_TERMS.search(label):
                reasons.append("consequential_control")
            if (
                _AMBIGUOUS_TERMS.match(target.accessible_name)
                and not target.consequence_known_safe
            ):
                reasons.append("ambiguous_control")

        # The caller may provide trusted semantic hints from the current DOM.  Page
        # content alone never marks an action safe; risk hints only add confirmation.
        if any(
            _CONSEQUENTIAL_TERMS.search(_normalize_text(hint))
            for hint in target.page_risk_hints
        ):
            reasons.append("page_risk_hint")

        if action not in _SAFE_ACTIONS and action not in {
            "browser_click",
            "browser_fill",
            "browser_type",
            "browser_press",
            "browser_select",
        }:
            # Unknown future actions fail toward confirmation, not auto-execution.
            reasons.append("unknown_browser_action")

        return ActionPolicyDecision(bool(reasons), tuple(dict.fromkeys(reasons)))

    def issue_approval(
        self, request: BrowserActionRequest, *, ttl_seconds: Optional[float] = None
    ) -> BrowserActionApproval:
        """Create a capability only after the UI records explicit user consent."""

        request = self._validated_request(request)
        decision = self.classify(request)
        if not decision.requires_confirmation:
            raise ApprovalRequired(
                "Approval tokens are issued only for actions requiring confirmation"
            )
        ttl = self._ttl if ttl_seconds is None else float(ttl_seconds)
        if ttl <= 0 or ttl > self._ttl:
            raise ValueError("Approval lifetime must be positive and no longer than policy TTL")
        token = os.urandom(32).hex()
        approval = BrowserActionApproval(
            token=token,
            expires_at=self._clock() + ttl,
            session_id=request.session_id,
            tab_id=request.tab_id,
            snapshot_id=request.snapshot_id,
            ref=request.ref,
            origin=request.origin,
            action=request.action,
            parameter_hash=self._parameter_hash(request),
            data_classification=request.data_classification,
        )
        with self._lock:
            self._approvals[token] = approval
        return approval

    def consume_approval(
        self, token: str, current_request: BrowserActionRequest
    ) -> BrowserActionApproval:
        """Validate a freshly rebuilt request and consume its approval exactly once."""

        current_request = self._validated_request(current_request)
        with self._lock:
            approval = self._approvals.get(str(token))
            if approval is None:
                raise ApprovalInvalid("Browser action approval is unknown or already used")
            if self._clock() >= approval.expires_at:
                self._approvals.pop(str(token), None)
                raise ApprovalExpired("Browser action approval has expired")
            expected = self._binding_payload(approval)
            actual = self._binding_payload(
                BrowserActionApproval(
                    token=approval.token,
                    expires_at=approval.expires_at,
                    session_id=current_request.session_id,
                    tab_id=current_request.tab_id,
                    snapshot_id=current_request.snapshot_id,
                    ref=current_request.ref,
                    origin=current_request.origin,
                    action=current_request.action,
                    parameter_hash=self._parameter_hash(current_request),
                    data_classification=current_request.data_classification,
                )
            )
            if not hmac.compare_digest(expected, actual):
                raise ApprovalInvalid(
                    "Browser state or action parameters changed after approval"
                )
            self._approvals.pop(str(token), None)
            return approval

    def revoke_session(self, session_id: str) -> int:
        """Invalidate pending browser approvals when a task closes or takes over."""

        with self._lock:
            tokens = [
                token
                for token, approval in self._approvals.items()
                if hmac.compare_digest(approval.session_id, str(session_id))
            ]
            for token in tokens:
                self._approvals.pop(token, None)
            return len(tokens)

    def prune_expired(self) -> int:
        now = self._clock()
        with self._lock:
            tokens = [
                token
                for token, approval in self._approvals.items()
                if now > approval.expires_at
            ]
            for token in tokens:
                self._approvals.pop(token, None)
            return len(tokens)

    def _validated_request(
        self, request: BrowserActionRequest
    ) -> BrowserActionRequest:
        if not isinstance(request, BrowserActionRequest):
            raise TypeError("BrowserActionRequest is required")
        required = {
            "session_id": request.session_id,
            "tab_id": request.tab_id,
            "snapshot_id": request.snapshot_id,
            "ref": request.ref,
            "origin": request.origin,
            "action": request.action,
        }
        if any(not isinstance(value, str) or not value for value in required.values()):
            raise BrowserActionPolicyError(
                "Browser approvals require session, tab, snapshot, ref, origin, and action"
            )
        try:
            normalized_origin = canonical_origin(request.origin).value
        except DestinationPolicyError as exc:
            raise BrowserActionPolicyError("Browser action origin is invalid") from exc
        if not hmac.compare_digest(normalized_origin, request.origin):
            # Build requests with BrowserActionRequest.build so approvals never bind two
            # visually different spellings of the same origin.
            raise BrowserActionPolicyError("Browser action origin is not canonical")
        _canonical_json(request.arguments)
        return request

    def _parameter_hash(self, request: BrowserActionRequest) -> str:
        body = {
            "arguments": request.arguments,
            "target": {
                "role": request.target.role,
                "accessible_name": request.target.accessible_name,
                "element_type": request.target.element_type,
                "inside_form": request.target.inside_form,
                "submits_form": request.target.submits_form,
                "consequence_known_safe": request.target.consequence_known_safe,
                "page_risk_hints": request.target.page_risk_hints,
            },
            "data_classification": request.data_classification,
        }
        return hmac.new(
            self._binding_key,
            _canonical_json(body),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _binding_payload(approval: BrowserActionApproval) -> bytes:
        return _canonical_json(
            {
                "session_id": approval.session_id,
                "tab_id": approval.tab_id,
                "snapshot_id": approval.snapshot_id,
                "ref": approval.ref,
                "origin": approval.origin,
                "action": approval.action,
                "parameter_hash": approval.parameter_hash,
                "data_classification": approval.data_classification,
            }
        )


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BrowserActionPolicyError(
            "Browser action arguments must be strict JSON"
        ) from exc


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _normalize_label(value: Any) -> str:
    return _normalize_text(str(value)).replace(" ", "_")
