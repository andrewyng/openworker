"""Run the 4 topic searches against the arXiv Atom API, last 7 days.
Cutoff = 7 days before today (2026-08-31) -> 2026-08-24. Read-only."""
import urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone

NOW = datetime(2026, 8, 31)
CUTOFF = (NOW.replace(hour=0, minute=0, second=0, microsecond=0)
          ).strftime("%Y-%m-%d")
base = "http://export.arxiv.org/api/query/"
NS = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}

QUERIES = {
    "quantum": '(abs:"quantum computing" AND (cat:quant-ph)) AND (abs:"algorithm" OR abs:"error correction" OR abs:"superconducting qubit" OR abs:"quantum machine learning" OR abs:"hardware")',
    "materials": '(cat:cond-mat.mtrl-sci) AND (abs:"density functional" OR abs:"interatomic potential" OR abs:"crystal structure" OR abs:"machine-learning potential")',
    "mlsystems": '(cat:cs.LG OR cat:cs.AR) AND (abs:"inference efficiency" OR abs:"quantization" OR abs:"mixture of experts" OR abs:"long context")',
    "agents": '(cat:cs.AI OR cat:cs.CL) AND (abs:"tool use" OR abs:"retrieval-augmented" OR abs:"agent evaluation" OR abs:"multi-agent")',
}

import subprocess
def search(q, maxr=60):
    params = {"search_query": q, "start": 0, "max_results": maxr,
              "sortBy": "submittedDate", "sortOrder": "descending"}
    url = base + urllib.parse.urlencode(params)
    # export.arxiv.org 301s http->https; urllib mangles that, curl -L is clean.
    out = subprocess.run(["curl", "-sL", "--max-time", "30", url],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"curl {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout

for topic, q in QUERIES.items():
    print(f"===== {topic} (cutoff {CUTOFF}) =====")
    try:
        root = ET.fromstring(search(q))
        n = 0
        for e in root.findall("a:entry", NS):
            aid = e.findtext("a:id", "", NS).split("/abs/")[-1]
            title = " ".join((e.findtext("a:title", "", NS) or "").split())
            pub = e.findtext("a:published", "", NS)[:10]
            if pub < CUTOFF:
                break  # submittedDate desc -> once we pass cutoff, stop
            snippet = " ".join((e.findtext("a:summary", "", NS) or "").split())[:180]
            n += 1
            print(f"{pub} | {aid} | {title[:90]}")
            print(f"       | {snippet}")
        if n == 0:
            print("(no entries in window)")
    except Exception as ex:
        print(f"ERROR: {ex}")
    print()
