# Python/native harness contracts

`compaction.json` contains 15 reviewed pure-policy cases plus exact prompt strings,
pinned to public Python `c37b761`. `known-deviations.json` explicitly records P1–P3
from `docs/harness/PYTHON-RUST-PARITY.md`. Fixing those Python bugs should make
the reference adapter fail until their expected differences are reviewed/removed.
Do not refresh goldens automatically or broadly xfail failures.

Run from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 ../openworker/.venv/bin/python harness/tests/python-contract.py
cargo test --manifest-path harness/Cargo.toml --workspace --locked
```

The Python command uses a development environment only, fake providers and
temporary state. Rust tests consume the same fixture data. Neither invokes a
live provider. The fixture set is an initial shared subset, not proof of full
Python/Desktop parity. Runtime-specific fault tests cover additional native
durability/retry/isolation behavior independently.
