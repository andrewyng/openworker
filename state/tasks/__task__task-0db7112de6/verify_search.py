"""Round-trip verification: embed a query with fastembed, search the corpus."""
import json, urllib.request
import fastembed

QDRANT, COLL, V = "http://localhost:6333", "default", "fast-all-minilm-l6-v2"
queries = [
    "fault-tolerant quantum error correction decoder",
    "machine learning interatomic potential materials",
    "LLM inference quantization quantized kernels",
    "multi-agent LLM evaluation benchmark",
]
model = fastembed.TextEmbedding()
for q in queries:
    v = [float(x) for x in next(model.embed([q]))]
    body = json.dumps({"vector": {V: v}, "limit": 3, "with_payload": True}).encode()
    # named vector required for search on this collection
    req = urllib.request.Request(f"{QDRANT}/collections/{COLL}/points/search",
                                 data=body, headers={"Content-Type": "application/json"})
    res = json.load(urllib.request.urlopen(req))["result"]
    print(f"Q: {q}")
    for r in res:
        aid = (r["payload"].get("metadata") or {}).get("arxiv_id", "-")
        print(f"   {r['score']:.3f}  {aid:11s} {r['payload']['document'][:70]}")
