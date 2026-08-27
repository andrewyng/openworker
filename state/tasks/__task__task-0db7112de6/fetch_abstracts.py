import urllib.request
import xml.etree.ElementTree as ET

ID_LIST = [
    # quantum
    "2608.18985", "2608.18512", "2608.16857", "2608.15760",
    # materials
    "2608.19041", "2608.17716", "2608.15776", "2608.15609",
    # mlsystems
    "2608.18261", "2608.15602", "2608.16947", "2608.13756",
    # agents
    "2608.18554", "2608.18398", "2608.17275",
]

url = "http://export.arxiv.org/api/query?id_list=" + ",".join(ID_LIST) + "&max_results=50"
xml = urllib.request.urlopen(url, timeout=60).read().decode()
root = ET.fromstring(xml)
NS = {"a": "http://www.w3.org/2005/Atom"}
for e in root.findall("a:entry", NS):
    aid = e.findtext("a:id", "", NS).split("/abs/")[-1]
    title = " ".join((e.findtext("a:title", "", NS) or "").split())
    pub = e.findtext("a:published", "", NS)[:10]
    summ = " ".join((e.findtext("a:summary", "", NS) or "").split())
    print(f"### {aid} ({pub})\n{title}\n{summ}\n")
