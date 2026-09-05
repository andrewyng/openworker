import sys, importlib
from pathlib import Path

AGPACK = Path("/home/iconbaypark2900/dataScience/agpack/src")
SENTINEL = Path("/home/iconbaypark2900/jabCreative/dataScience/sentinel-local/src")
METERED = Path("/home/iconbaypark2900/jabCreative/dataScience/metered-web-broker")

# can we even reach the source roots?
for name, root in [("agpack", AGPACK), ("sentinel", SENTINEL), ("metered", METERED)]:
    print(f"{name}: exists={root.exists()} dir={root.is_dir()}")

sys.path[0:0] = [str(AGPACK), str(SENTINEL)]
import concord
r = concord.check()
print(r.print())
print()
try:
    concord.assert_real_reality(r)
    print("REALITY CHECK OK")
except AssertionError as exc:
    print("FAILED:", exc)
    sys.exit(1)
