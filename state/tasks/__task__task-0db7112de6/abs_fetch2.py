"""Fetch the remaining 9 abstracts (retry-backed). Read-only."""
import subprocess, urllib.parse, time, xml.etree.ElementTree as ET
ids = ["2608.26272","2608.25024","2608.26962","2608.28521",
       "2608.28044","2608.28444","2608.28497","2608.27969","2608.27984"]
url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ",".join(ids)})
for attempt in range(7):
    try:
        out = subprocess.run(["curl","-sL","--max-time","30",url], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.lstrip().startswith("<?xml"):
            break
    except Exception as ex:
        out = None
    time.sleep(5 * (2 ** attempt))
root = ET.fromstring(out.stdout)
ns = {"a":"http://www.w3.org/2005/Atom"}
for e in root.findall("a:entry", ns):
    aid = e.findtext("a:id", "", ns).split("/abs/")[-1]
    title = " ".join((e.findtext("a:title", "", ns) or "").split())
    summary = " ".join((e.findtext("a:summary", "", ns) or "").split())
    cats = [c.get("term") for c in e.findall("a:category", ns)][:3]
    print("=" * 3, aid, "|", title)
    print("cats:", ", ".join(cats))
    print(summary); print()
    time.sleep(2)
