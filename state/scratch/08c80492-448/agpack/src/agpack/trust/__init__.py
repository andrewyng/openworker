"""Step 3 — the trust layer: signing, scoped delegation, audit.

The *why this is the product* module. The sandbox (step 2) says "this
guest can't escape"; the trust layer says "and here's *proof*":

- ``signing``     — signed bundle manifests. The *cryptographic*
                    guarantee that the bundle bytes are what the
                    publisher said they were.
- ``delegation``  — per-hop scoped delegation tokens. The *authority*
                    guarantee that a call this deep in a chain still
                    stays within what the chain's root allowed.
- ``audit``       — the replayable execution ledger. The *accountability*
                    guarantee that an auditor (or the caller) can
                    reconstruct what happened, not just "trust the log."

Together, these are the answer to the decision memo's "valid credential
only guarantees the door opens" line: the credential is the signature,
the door is the sandbox, and the *delegation token + audit ledger* are
what make "door + credential" into "authorized execution."

Design rule that unifies the three:

- Nothing in this module writes to disk. The *ledger* is an append-only
  in-memory structure that the *caller* (the host, the CLI) owns and
  persists — the trust module *produces* records, it doesn't *store*
  them. This keeps a compromised audit module from being a compromised
  *evidence* source; the storage layer (the ledger file / the OTel
  exporter) is the *actual* evidence, and it's the caller's to trust.
"""
