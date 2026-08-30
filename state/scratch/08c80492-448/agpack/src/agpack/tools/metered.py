"""Step 5 — metered access as a built-in tool: pay-per-tool-call.

Continuity step from the decision memo: the metered-web broker (first
memo) does not get abandoned; it gets folded in as a *first-class
tool* this runtime ships with. This module is that adapter.

Shape — a tool, not a service:

- ``metered.call`` is a scope in the sandbox's import surface, backed
  by a host-side adapter that does the billing, the audit append, and
  the delegation hop. A bundle declares the scope in its permission
  policy and the platform max-capability file must allow it (see
  sandbox/capabilities.py "capability by exception").
- One dispatch = one billable unit; the *price* is the fuel consumed
  (sandbox/limits.py: "the fuel meter is the meter the
  metered-access tool bills against"). A bundle that bills a third
  party (e.g. the web-fetch tool in the metered-web broker) calls the
  ``metered.call`` import with (resource_id, fuel_used) and receives
  a receipt back in its linear memory.
- Every metered dispatch appends a ``delegate``-kind record to the
  audit ledger (trust/audit.py per-kind detail shapes) carrying
  bundle_id, resource_id, fuel spent, and the logical timestamp. The
  auditor reads the ledger, the guest reads the receipt — two
  different closed surfaces of the same event.

The adapter shape — what a billing *backend* must implement:

    @runtime_checkable
    class BillingBackend(Protocol):
        def charge(self, *, bundle_id: str, resource_id: str,
                   fuel_used: int, ts_unix: int) -> Receipt: ...
        def refund(self, *, receipt: Receipt) -> None: ...

``Receipt`` is a frozen dataclass (bundle_id, resource_id,
fuel_used, ts_unix, receipt_id). The receipt_id is *the backend's*
id (a KMS UUID, a Stripe session id, ...) — the protocol does not
care where it comes from; the audit ledger cares about the shape,
not the source. The backend is pluggable per deployment: a Stripe
adapter, a Kafka consumer, an OTel meter exporter — the adapter
shape is the stable part, the backend is the implementation.

What this module does NOT do (deliberate):
- No billing backends here. The shape is the contract; the backends
  are integrations, and they land as thin modules in their own
  package when a real integration needs them.
- No in-memory meter. Stateful metering objects are a liability in
  the sandbox model: the fuel counter (driver-side) IS the meter,
  and it is what this module bills against. Duplicating it here is
  a drift hazard.
- No subscription model in v0. Subscriptions are a billing-model
  concern, and one that changes the *receipt* shape (prorated units,
  commitment windows); it's a later adapter variant, not a v0 scope.
"""

# Intended surface (v0.1 — the v0 closed scope set in
# sandbox/capabilities.py does not include this yet):
#
#   METERED_CALL = Scope("metered.call")   # added to the closed set,
#                                          # platform max file updated
#
#   @dataclass(frozen=True)
#   class Receipt:
#       receipt_id: str
#       bundle_id: str
#       resource_id: str
#       fuel_used: int
#       ts_unix: int
#
#   class BillingBackend(Protocol):
#       def charge(self, *, bundle_id: str, resource_id: str,
#                  fuel_used: int, ts_unix: int) -> Receipt: ...
#       def refund(self, *, receipt: Receipt) -> None: ...
#
#   def handle_metered_call(*, scope_policy: CapabilityPolicy,
#                           backend: BillingBackend,
#                           bundle_id: str, resource_id: str,
#                           fuel_used: int, logical_now_unix: int,
#                           audit: AuditLedger) -> Receipt:
#       # 1. hard-fail if metered.call ∉ scope_policy.scopes
#       # 2. backend.charge(...) — a backend error surfaces to the
#       #    guest as a budget failure (fuel-exhaustion-shaped),
#       #    never as a host crash
#       # 3. audit.append(delegate record)
#       # 4. return the Receipt; the ledger is the audit source of
#       #    truth, the return value is the guest-facing surface

raise NotImplementedError("Scaffold stub — see module docstring.")
