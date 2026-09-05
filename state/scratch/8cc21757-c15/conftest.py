"""Test/bootstrap conftest.

`agpack` lives in a READ-ONLY bundle that this workspace cannot write to.
So we put its source dir on `sys.path` here (not modify the bundle). This
lets both the demo and the tests import `agpack` exactly as it ships.

Run:
    pytest -q                      # whole suite in this scratch
    pytest call_chain_demo_test.py  # just the call-chain demo
"""

import sys
from pathlib import Path

_PACKAGES_SRC = Path("/home/iconbaypark2900/dataScience/agpack/.scratch/agpack")
if _PACKAGES_SRC.is_dir() and str(_PACKAGES_SRC) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_SRC))
