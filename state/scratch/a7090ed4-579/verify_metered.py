import os
METERED = "/home/iconbaypark2900/jabCreative/dataScience/metered-web-broker"
for rel in ["packages/core/src/types.ts", "packages/budget/src/engine.ts"]:
    p = os.path.join(METERED, rel)
    print(rel, "EXISTS" if os.path.isfile(p) else "MISSING")
