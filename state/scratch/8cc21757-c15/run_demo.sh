#!/usr/bin/env bash
# Run the call-chain demo + its tests against agpack's real trust source.
# Agpack source is read-only here, so the conftest.py adds it to sys.path.
set -euo pipefail

PRIMARY=/home/iconbaypark2900/openworker-tasks/8cc21757-c15
AGPACK_SRC=/home/iconbaypark2900/dataScience/agpack/.scratch/agpack

# Pick a python that has `cryptography` and can import agpack from the read-only source.
for PY in "venv/bin/python" "/usr/bin/python3" "python3"; do
  if $PRIMARY/$PY -c "import cryptography, sys; sys.path.insert(0, '$AGPACK_SRC'); import agpack.trust.delegation as d; import agpack.trust.audit as a; print('ok', '$PY')" >/dev/null 2>&1; then
    echo "==> Using $PY"
    $PRIMARY/$PY -m pytest call_chain_demo_test.py -v
    echo "==> Running demo"
    $PRIMARY/$PY call_chain_demo.py
    exit 0
  fi
done
echo "No suitable python found." >&2
exit 1
