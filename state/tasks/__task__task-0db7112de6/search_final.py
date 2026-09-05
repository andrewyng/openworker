"""Final robust 4-topic arxiv search (last 7 days), with retry/backoff.
export.arxiv.org intermittently returns a 404 HTML page under load; retry
exponentially. Uses curl -L (handles the http->https 301 redirect).
Read-only."""
import subprocess, urllib.parse, time, xml.etree.ElementTree as ET

CUTOFF = "2026-08-24"  # 7 days before 2026-08-31
NS = {"a": "http://www.w3.org/2005/Atom"}
QUERIES = {
    "quantum": '(abs:"quantum computing" AND (cat:quant-ph)) AND (abs:"algorithm" OR abs:"error correction" OR abs:"superconducting qubit" OR abs:"quantum machine learning" OR abs:"hardware")',
    "materials": '(cat:cond-mat.mtrl-sci) AND (abs:"density functional" OR abs:"interatomic potential" OR abs:"crystal structure" OR abs:"machine-learning potential")',
    "mlsystems": '(cat:cs.LG OR cat:cs.AR) AND (abs:"inference efficiency" OR abs:"quantization" OR abs:"mixture of experts" OR abs:"long context")',
    "agents": '(cat:cs.AI OR cat:cs.CL) AND (abs:"tool use" OR abs:"retrieval-augmented" OR abs:"agent evaluation" OR abs:"multi-agent")',
}

def search(q, maxr=40, tries=7):
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": q, "start": 0, "max_results": maxr,
         "sortBy": "submittedDate", "sortOrder": "descending"})
    last = None
    for attempt in range(tries):
        try:
            out = subprocess.run(["curl", "-sL", "--max-time", "30", url],
                                 capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.lstrip().startswith("<?xml"):
                return out.stdout
            last = out.stderr.strip()[:150] or out.stdout[:80]
        except Exception as ex:
            last = str(ex)
        backoff = 5 * (2 ** attempt)
        print(f"   retry {attempt+1}/{tries} in {backoff}s (last: {last!r})", flush=True)
        time.sleep(backoff)
    raise RuntimeError(f"all {tries} attempts failed. last: {last}")

for i, (topic, q) in enumerate(QUERIES.items()):
    print(f"\n===== {topic} =====")
    try:
        xml = search(q)
        root = ET.fromstring(xml)
        n = 0
        for e in root.findall("a:entry", NS):
            aid = e.findtext("a:id", "", NS).split("/abs/")[-1]
            title = " ".join((e.findtext("a:title", "", NS) or "").split())
            pub = e.findtext("a:published", "", NS)[:10]
            if pub < CUTOFF:
                break
            n += 1
            print(f"  {pub} | {aid} | {title[:95]}")
        print(f"  --> {n} entries in window")
    except Exception as ex:
        print(f"  ERROR: {ex}")
    if i < len(QUERIES) - 1:
        time.sleep(4)  # arXiv asks for >=3s between calls
