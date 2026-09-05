"""Scroll the default Qdrant collection and print every stored arxiv id + title.
This is the dedup base for today's corpus run. Read-only."""
import json, urllib.request

QDRANT = "http://localhost:6333"
COLL = "default"
PAGE = 100
seen = 0
ids = set()
rows = []
while True:
    body = json.dumps({
        "limit": PAGE,
        "offset": seen,
        "with_payload": True,
    }).encode()
    req = urllib.request.Request(f"{QDRANT}/collections/{COLL}/points/scroll",
                                 data=body, headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=30))
    pts = resp.get("result", {}).get("points", [])
    if not pts:
        break
    for p in pts:
        d = p.get("payload", {})
        doc = d.get("document", "") or ""
        aid = (d.get("metadata") or {}).get("arxiv_id")
        # also catch "arXiv:<id>" inside the document text
        if not aid:
            for tok in doc.split():
                if tok.startswith("arXiv:"):
                    aid = tok[6:]
                    break
        rows.append((aid, doc[:130]))
        if aid:
            ids.add(aid)
    seen += len(pts)
    if len(pts) < PAGE:
        break

print(f"TOTAL POINTS: {seen}")
src = [r for r in rows if r[0]]
print(f"WITH arxiv_ID: {len(src)}")
for aid, doc in sorted(src):
    print(f"  {aid}  |  {doc}")
