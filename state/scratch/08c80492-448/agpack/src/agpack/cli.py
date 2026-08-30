"""`agpack` CLI — the operator surface for the whole system.

Four verbs, each a thin wrapper over one subpackage:

- ``agpack bundle <agent_dir>``    -> :mod:`agpack.artifact.packager`
    One command bundles ``{agent code, system prompt, tool manifest, memory
    contract, permission policy, eval suite}`` into a single signed, versioned
    bundle. The *command itself* is part of the standards contribution: it must
    be deterministic (same inputs -> same bundle bytes) so that signatures and
    provenance are meaningful across machines and runtimes.

- ``agpack run <bundle> --driver <name>``
    Validate + load a bundle, instantiate the WASM sandbox via a
    :class:`agpack.portability.driver.RuntimeDriver`, and dispatch the agent's
    declared entry tool call(s). Every host import the guest touches is
    mediated by the capability policy and appended to the audit ledger.
    ``--driver`` is what makes the portability demo real: the *same bundle*
    must produce the *same observable behavior* on ``wasmtime`` and ``wasmer``.

- ``agpack verify <bundle>``
    Signing + structural verification with ZERO execution: does the signature
    check, is the manifest schema-valid, is the declared permission policy
    well-formed and inside the platform's allow-list? This is the auditor /
    CI hook.

- ``agpack audit <bundle> --run <run_id>``
    Replay the execution ledger for one run: reconstruct the sequence of
    tool calls, capability checks, delegation-token hops, and fuel consumption.
    The output is human-readable by default; ``--json`` for OTel-shaped
    machine output so downstream tooling (the audit dashboard from the
    metered-web broker work) can ingest it directly.

Design notes / decisions captured by the scaffold:
- The CLI stays thin. All policy decisions live in ``artifact.validator``
  and ``sandbox.capabilities``; the CLI just threads arguments through.
- No network calls from the CLI on the ``verify`` path — that's the point
  of a *verifiable* artifact: verification must work offline.
- Exit codes are part of the contract: 0=ok, 1=verification failure,
  2=syntax/usage error. CI depends on them.
"""

# Intended surface (typer app):
#   app = typer.Typer()
#   @app.command() def bundle(agent_dir: Path, out: Path, key: Path) -> Path: ...
#   @app.command() def run(bundle: Path, driver: str, tool: str, args: JSON) -> JSON: ...
#   @app.command() def verify(bundle: Path, policy_max: Path | None) -> Report: ...
#   @app.command() def audit(bundle: Path, run_id: str, fmt: Literal["text","json"]) -> Output: ...
#   def main() -> None: app()

raise NotImplementedError("Scaffold stub — CLI implementation is step 1 of the build plan.")
