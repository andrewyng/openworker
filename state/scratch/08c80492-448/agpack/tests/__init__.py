"""Test suite root.

Layout mirrors ``src/agpack``: one test module per source module,
plus a couple of cross-cutting suites at the root:

- ``tests/test_determinism.py``    — the packager's P0 property: same
  input dir -> byte-identical bundles, run twice.
- ``tests/test_invariants.py``     — the sandbox security invariants
  from sandbox/__init__.py, each as a test:
      * guest requesting unlisted import -> fails at INSTANTIATION
      * unbounded loop -> kills on fuel, not wall-time
      * guest cannot read host fs through any import
      * emit.text is the only output channel
- ``tests/test_portability_proof.py`` — the step-4 demo as a test:
  same bundle on wasmtime + wasmer profiles -> HarnessReport.pass_flag.

Run with ``make test`` (pytest). Stubs: every test below is a
placeholder that raises when called, so `pytest` at this stage
reports them as *errors*, not *passes* — a green suite at scaffold
stage would be a lie (the trust/audit redaction note says it
best: a pass you didn't earn is a lie, full stop).
"""

# Intended surface (sketch — fill in during implementation):
#
#   def test_bundle_determinism(tmp_path):
#       a = pack(_HELLO_AGENT_DIR, max_cap_path=MAX, sign_key=KEY)
#       b = pack(_HELLO_AGENT_DIR, max_cap_path=MAX, sign_key=KEY)
#       assert read_bytes(a) == read_bytes(b)
#
#   def test_unlisted_import_fails_at_instantiation():
#       with pytest.raises(ImportNotDeclared):
#           host.dispatch(..., tool_cid="evil-tool", ...)
#
#   def test_portability_proof():
#       rep = harness.run(HELLO_BUNDLE, PROFILES["wasmtime"], PROFILES["wasmer"])
#       assert rep.pass_flag, rep.reasons

raise NotImplementedError("Scaffold stub — see module docstring for the suite plan.")
