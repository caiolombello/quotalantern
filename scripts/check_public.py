"""Check release references and code/assets without accessing user storage."""
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree
import ast,hashlib,json,re

root=Path(__file__).resolve().parent.parent
class References(HTMLParser):
    def handle_starttag(self,tag,attrs):
        for name,value in attrs:
            if name in ('href','src','srcset') and value and not value.startswith(('https:','http:','#')):
                assert (root/'site'/value.split('#')[0]).is_file(),value
for p in (root/'site').glob('*.html'):References().feed(p.read_text())
for p in list((root/'assets').glob('*.svg'))+list((root/'site/assets').rglob('*.svg')):
    tree=ElementTree.parse(p)
    for node in tree.iter():
        assert node.tag.rsplit('}',1)[-1] in ('svg','title','path','g','rect','circle')
        assert not any('href' in key or key.lower().startswith('on') for key in node.attrib)
for directory in ('codexbar_linux','tests','scripts'):
    for p in (root/directory).rglob('*.py'):ast.parse(p.read_text())
assert 'MIT License' in (root/'LICENSE').read_text()
assert 'Peter Steinberger' in (root/'NOTICE').read_text()
assert 'Peter Steinberger' in (root/'LICENSES/CodexBar-MIT.txt').read_text()
build=json.loads((root/'BUILD.json').read_text())
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in build['file_sha256'].items())
suspect=[]
patterns=[r'sk-(?:proj-)?[A-Za-z0-9_-]{30,}',r'gh[pousr]_[A-Za-z0-9]{30,}',r'(?:AKIA|ASIA)[0-9A-Z]{16}',r'AIza[0-9A-Za-z_-]{35}',r'GOCSPX-[0-9A-Za-z_-]{20,}',r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',r'/home/'+'caio']
for p in root.rglob('*'):
    if not p.is_file() or '.git' in p.parts or '__pycache__' in p.parts or p.name in ('tests-after.txt','validation-after.json'):continue
    if p.suffix not in ('.py','.md','.json','.html','.js','.css','.sh','.svg','.txt','.yml') and p.name not in ('LICENSE','NOTICE'):continue
    content=p.read_text()
    if any(re.search(pattern,content) for pattern in patterns):suspect.append(str(p.relative_to(root)))
assert not suspect,{'review_files':suspect}
print('Public checks passed: local references, SVGs, syntax, notices, build hashes and secret-pattern scan')
