"""Validate every published guide after a normal or versioned Studio build."""
import json, re, sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
site = Path(__file__).resolve().parent.parent
root = site.parent
output = Path(sys.argv[1]).resolve()
base = sys.argv[2].rstrip('/')
product = sys.argv[3] if len(sys.argv)>3 else 'sdk'
assert product in ('sdk','desktop'), product
pages = json.loads((site/('desktop-pages.json' if product=='desktop' else 'pages.json')).read_text())
class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.ids=set(); self.links=[]; self.sources=[]; self.indexed=False; self.studio=False; self.pres=0
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        if tag=='a' and attrs.get('href'): self.links.append(attrs['href'])
        if tag=='img': self.sources.append(attrs.get('src',''))
        if 'data-pagefind-body' in attrs: self.indexed=True
        if tag=='body': self.studio='studio' in attrs.get('class','').split()
        if tag=='pre': self.pres+=1
parsed={}
for p in pages:
    file=output/('index.html' if p['slug']=='index' else p['slug']+'/index.html')
    page=Page(file.read_text());parsed[file]=page
    assert page.studio and page.indexed, file
    # Source drawer has one extra pre; all Markdown code fences remain present.
    fences=len(re.findall(r'^```[^\n]*\n[\s\S]*?^```', (root/p['source']).read_text(), re.M))
    assert page.pres==fences+(product=='sdk' and p['slug'] in ('desktop','server')), (file,page.pres,fences)
for file,page in parsed.items():
    for target in page.links+page.sources:
        url=urlsplit(target)
        if url.scheme or url.netloc: continue
        if url.path:
            assert url.path.startswith(base+'/'), (file,target)
            path=output/unquote(url.path[len(base)+1:])
            if path.is_dir() or url.path.endswith('/'): path=path/'index.html'
        else: path=file
        assert path.exists(), (file,target,path)
        if url.fragment and path in parsed: assert unquote(url.fragment) in parsed[path].ids, (file,target)
assert (output/'pagefind/pagefind.js').exists(), 'Missing search bundle'
for name in ['apple','notion']:
    preview=output/'previews/desktop'/name/'index.html'
    if product=='sdk': assert not Page(preview.read_text()).indexed
    else: assert not preview.exists(), 'SDK previews must not appear in Desktop output'
if product=='desktop':
    from zipfile import ZipFile
    with ZipFile(output/'examples/meeting-notes.zip') as archive:
        samples=root/'docs/desktop/samples'
        expected=sorted(str(path.relative_to(samples)) for path in (samples/'meeting-notes').glob('*.md'))
        assert sorted(archive.namelist())==expected, 'Unexpected sample ZIP contents'
        for name in expected: assert archive.read(name)==(samples/name).read_bytes(), name
    assert not (output/'examples/desktop.json').exists(), 'SDK source bundle in Desktop output'
    assert not (output/'examples/web.json').exists(), 'SDK source bundle in Desktop output'
print(f'Checked {len(parsed)} Studio guides, code blocks, links, images, anchors and search assets at {base}')
