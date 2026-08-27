import json, urllib.request
req = urllib.request.Request(
    "http://localhost:6333/collections/default/points/scroll",
    data=json.dumps({"limit": 200, "with_payload": True}).encode())
pts = json.load(urllib.request.urlopen(req))["result"]["points"]
print(f"TOTAL {len(pts)}")
for p in pts:
    d = p["payload"]
    doc = d.get("document", "")
    m = d.get("metadata", {})
    print(f"- [{m.get('topic','?')[:30]}] {m.get('arxiv_id','?')} | {doc[:110]}")
