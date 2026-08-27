import urllib.request
import xml.etree.ElementTree as ET
url = "http://export.arxiv.org/api/query?id_list=2608.15609,2608.13756,2608.17275&max_results=10"
root = ET.fromstring(urllib.request.urlopen(url, timeout=60).read().decode())
NS = {"a": "http://www.w3.org/2005/Atom"}
for e in root.findall("a:entry", NS):
    aid = e.findtext("a:id", "", NS).split("/abs/")[-1]
    title = " ".join((e.findtext("a:title", "", NS) or "").split())
    pub = e.findtext("a:published", "", NS)[:10]
    summ = " ".join((e.findtext("a:summary", "", NS) or "").split())
    print(f"### {aid} ({pub})\n{title}\n{summ}\n")
