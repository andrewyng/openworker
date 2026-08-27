import json, urllib.request, urllib.parse
from datetime import datetime, timezone

def search(query, maxr=40):
    base = "http://export.arxiv.org/api/query?"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": maxr,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = base + urllib.parse.urlencode(params)
    return urllib.request.urlopen(url, timeout=30).read().decode()

import xml.etree.ElementTree as ET
NS = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}

QUERIES = {
    "quantum": '(abs:"quantum computing" AND (cat:quant-ph)) AND (abs:"algorithm" OR abs:"error correction" OR abs:"superconducting qubit" OR abs:"quantum machine learning" OR abs:"hardware")',
    "materials": '(cat:cond-mat.mtrl-sci) AND (abs:"density functional" OR abs:"interatomic potential" OR abs:"crystal structure" OR abs:"machine-learning potential")',
    "mlsystems": '(cat:cs.LG OR cat:cs.AR) AND (abs:"inference efficiency" OR abs:"quantization" OR abs:"mixture of experts" OR abs:"long context")',
    "agents": '(cat:cs.AI OR cat:cs.CL) AND (abs:"tool use" OR abs:"retrieval-augmented" OR abs:"agent evaluation" OR abs:"multi-agent")',
}

cutoff = "2026-08-13"
for topic, q in QUERIES.items():
    print(f"===== {topic} =====")
    try:
        xml = search(q)
        root = ET.fromstring(xml)
        n = 0
        for e in root.findall("a:entry", NS):
            aid = e.findtext("a:id", "", NS).split("/abs/")[-1]
            title = " ".join((e.findtext("a:title", "", NS) or "").split())
            pub = e.findtext("a:published", "", NS)[:10]
            if pub < cutoff:
                break
            n += 1
            print(f"{aid} | {pub} | {title[:120]}")
        if n == 0:
            print("(no entries in window)")
    except Exception as ex:
        print(f"ERROR: {ex}")
    print()
