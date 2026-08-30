"""Audit — the replayable execution ledger. The *accountability* half.

The trust layer's promise, concretized: an auditor (or the caller
themselves, or a *future* version of the caller) can reconstruct,
from the ledger alone, what the agent *did* — the sequence of tool
dispatches, the host-import calls each one made, the fuel each one
spent, and the delegation-token hops that authorized each scope.

Two load-bearing properties (write these as tests first):

1. **Replay determinism.** Given the *same ledger*, a *replay* of a
   run produces the *same observable behavior*. This is a *stronger*
   property than "the log is accurate": it says the log is a
   *sufficient* description of the run. Concretely, the ledger must
   include, for every host-import call the guest made, *enough inputs
   to re-derive the inputs the host saw* — the argument buffer
   *fingerprint*, the scope, the resource, and the *output* the host
   returned. A replay is *not* "re-execute the guest and compare"; it
   is "walk the ledger and re-derive every host-observable event from
   the *recorded* inputs." This is what makes the ledger *independent*
   of the guest: a guest that misbehaves is still a guest that *left a
   ledger*, and the auditor's job is to audit the *ledger*, not the
   guest.

2. **Append-only in the *record*, mutable in the *file*.**
   The *logical* ledger is append-only: once a record is in the
   ledger, its *ordinal* (its 0-based position in the ledger) is
   fixed forever. The *physical* ledger file (whichever storage the
   caller picks — a JSONL file, an OTel batch exporter, a KMS-backed
   blob) is *mutable* in the sense that the *caller* can choose to
   *truncation-restart* from ordinal ``k`` with a *new* ledger
   instance that carries a *``continued_from``* reference to ordinal
   ``k`` of the previous one. The *trust module* never truncates; it
   never *replaces*; it only *appends* and it never *forgives*. (The
   *caller*'s ability to truncate is a *storage*-level decision and
   is *out of scope* for this module — the module's contract is
   "append-only *records*," not "append-only *files*.")

Data model:

- ``AuditRecord`` — one append-only entry. Fields:
      ordinal: int                 # 0-based, fixed forever
      run_id: str                  # the bundle run this record belongs
                                   # to (see sandbox.host RunResult)
      kind: Literal["dispatch", "import_call", "delegate", "budget"]
      ts_unix: int                 # 1-sec; *logical* clock, not wall
      subject: str                 # the agent id (or hop agent id for
                                   # delegate records) that *made* this
                                   # event. (For dispatch: the bundle's
                                   # agent id. For import_call: the
                                   # tool's component id. For delegate:
                                   # the *hop's* agent id.)
      detail: str                  # a *closed* JSON schema per kind.
                                   # See the kind-specific detail notes
                                   # below.
- ``AuditLedger`` — a thin *append-only* container: ``append(rec)``,
  ``iter()`` (in ordinal order), ``len()``, ``last_ordinal()``. The
  *storage* is the *caller's* job (see trust module docstring: this
  module *produces* records, it doesn't *store* them).
- ``AuditReplayer`` (a *later* function in this module, not a class
  in v0): ``replay(ledger, kind, ordinal_range)`` -> a *structured*
  view of the records in that range, *already parsed* into the
  per-kind detail shape. A replayer is *pure* — it reads the ledger
  and returns data; it does not *modify* the ledger and it does not
  *execute* anything.

Per-kind ``detail`` shape (a *closed* set; a record whose ``detail``
doesn't match its ``kind``'s schema is a *hard* validator failure —
the ledger must be *self-describing*, and a record that fails the
self-description check is *corrupt*, not malformed):

- ``dispatch``:    ``{tool_cid, args_sha256_prefix16, budget_spent,
                     output_sha256}``
- ``import_call``: ``{scope, resource, arg_fingerprint, host_return,
                     fuel_delta}``
- ``delegate``:    ``{token_id, parent_token_id, hop_depth,
                     scope, resource}``
- ``budget``:      ``{budget: Budget}``   (the *declared* budget for the
                                            run, recorded *once* at run
                                            start, ordinal 0 — this is
                                            the "the run's rules" record)

Redaction rule (the ``audit.redaction`` note the host module referenced):
- The *argument buffer* is **never** recorded in full. Only the first
  16 hex characters of its SHA-256 are recorded (the
  ``args_sha256_prefix16`` field). This is the *deliberate* loss: an
  auditor who has the *output* and the *budget* and the *import call
  sequence* can reconstruct *what the agent did* but not *what the
  agent was handed*. The *input* is the *caller's* data, not the
  *agent's*; the ledger is the *agent's* account of its own behavior,
  and the agent is *not allowed* to account for the caller's data.
- This has a *cost*: if the caller needs to *reproduce* the input
  (a *determinism* check, not a *security* check), the caller must
  *replay the input themselves* — the ledger is not a *replay input*,
  it's a *replay oracle*. (The portability harness in step 4 is the
  *determinism* check; the audit ledger is the *security* check. They
  are *different* tools and the scaffold keeps them *different*.)

What this module does *not* do (deliberate):
- No *exporters* in this module (no OTel SDK import, no JSONL writer,
  no *queue* push). The *caller* owns the export. (The *OTel-shaped*
  record format is a *vocabulary* choice, not a *dependency* — the
  shapes are close enough that an *adapter* can translate in a few
  lines, but the *trust module* stays *exporter-agnostic* so a
  compromised exporter is not a *compromised ledger*.)
- No *retention* policy. Retention is a *storage* decision; the
  ledger is *infinite* in the logical model and the *caller* is free
  to *truncate* the *physical* file (with a ``continued_from``
  reference, per the module docstring's storage note) but the
  *module's contract* is "the ledger I *append to* is append-only."

Design note on the ``ts_unix`` field:
- This is the *logical* time (from the ``clock.now`` scope), not the
  *host* wall time. A *future* version of this module that adds a
  *wall-time* field for *cross-run* correlation (a *dashboard* concern)
  will name it *explicitly* (``wall_ts_unix``) and will *not* repurpose
  the ``ts_unix`` field. The two time sources serve *different*
  auditors: the *security* auditor needs *logical* time (it's the
  only time the *agent* can be held to), the *operations* auditor
  needs *wall* time (it's the only time the *host* can be held to),
  and the *ledger* needs to name *both* without conflating them.
"""

# Intended surface:
#   @dataclass(frozen=True) class AuditRecord:
#       ordinal: int
#       run_id: str
#       kind: Literal["dispatch", "import_call", "delegate", "budget"]
#       ts_unix: int
#       subject: str
#       detail: dict[str, Any]
#   class AuditLedger:
#       def append(self, rec: AuditRecord) -> None: ...
#       def __iter__(self) -> Iterator[AuditRecord]: ...
#       def __len__(self) -> int: ...
#       def last_ordinal(self) -> int: ...
#   def replay(ledger: AuditLedger, kind: str | None = None,
#              ordinal_range: tuple[int, int] | None = None) -> list[dict]: ...
#   class LedgerCorrupt(Exception): ...   # a record fails its own kind's schema

raise NotImplementedError("Scaffold stub — see module docstring.")
