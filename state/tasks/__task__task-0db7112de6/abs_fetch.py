"""Fetch full abstracts for selected arxiv IDs (retry-backed). Read-only."""
import subprocess, urllib.parse, time, xml.etree.ElementTree as ET

# (topic, id)
SELECTED = [
    ("quantum",   "2608.26272"),
    ("quantum",   "2608.25027"),
    ("quantum",   "2608.27267"),
    ("quantum",   "2608.25024"),
    ("materials", "2608.28100"),
    ("materials", "2608.27277"),
    ("materials", "2608.26962"),
    ("materials", "2608.28521"),
    ("mlsystems", "2608.28044"),
    ("mlsystems", "2608.28444"),
    ("mlsystems", "2608.28003"),
    ("mlsystems", "2608.26043"),
    ("mlsystems", "2608.25231"),
    ("agents",    "2608.28497"),
    ("agents",    "2608.27969"),
    ("agents",    "2608.28447"),
    ("agents",    "2608.26385"),
    ("agents",    "2608.27984"),
]

def fetch(idlist, tries=7):
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"id_list": idlist})
    last = None
    for attempt in range(tries):
        try:
            out = subprocess.run(["curl", "-sL", "--max-time", "30", url],
                                 capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.lstrip().startswith("<?xml"):
                return out.stdout
            last = out.stderr.strip()[:120] or out.stdout[:80]
        except Exception as ex:
            last = str(ex)
        time.sleep(5 * (2 ** attempt))
    raise RuntimeError(f"all {tries} failed. last: {last}")

out = fetch(",".join(i for _, i in SELECTED))
root = ET.fromstring(out)
for e in root.findall("a:entry", {"a": "http://www.w3.org/2005/Atom"}):
    aid = e.findtext("a:id", "", {"a": "http://www.w3.org/2005/Atom"}).split("/abs/")[-1]
    title = " ".join((e.findtext("a:title", "", {"a": "http://www.w3.org/2005/Atom"}) or "").split())
    summary = " ".join((e.findtext("a:summary", "", {"a": "http://www.w3.org/2005/Atom"}) or "").split())
    cats = [c.get("term") for c in e.findall("a:category", {"a": "http://www.w3.org/2005/Atom"})][:3]
    print("=" * 3, aid, "|", title)
    print("cats:", ", ".join(cats))
    print(summary)
    print()
    time.sleep(2)
