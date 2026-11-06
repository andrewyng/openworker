#!/usr/bin/env python3
"""Build an isolated consumer from the actual .crate archives, without repo paths.
Useful before first publication, including Cargo workspace-package registry bugs.
"""
import argparse,json,tarfile,pathlib,subprocess,os
p=argparse.ArgumentParser();p.add_argument('--packages',required=True);p.add_argument('--out',required=True);p.add_argument('--example');p.add_argument('--fetch',action='store_true',help='Populate the dependency cache before the offline consumer check');a=p.parse_args()
h=pathlib.Path(__file__).resolve().parents[1];m=json.loads((h/'release/manifest.json').read_text());out=pathlib.Path(a.out).resolve();out.mkdir();(out/'src').mkdir()
patch=[]
for item in m['rustPackages']:
 name=item['name'];filename=f'{name}-{m["version"]}'
 with tarfile.open(pathlib.Path(a.packages)/(filename+'.crate')) as archive:archive.extractall(out/'crates',filter='data')
 patch.append(f'{name} = {{ path = {json.dumps(str(out/"crates"/filename))} }}')
(out/'Cargo.toml').write_text('[package]\nname="openworker-registry-consumer"\nversion="0.0.0"\nedition="2021"\n[workspace]\n[dependencies]\nopenworker-sdk = {version="='+m['version']+'", features=["security-adapters","compliance-export"]}\ntokio = {version="1", features=["macros","rt-multi-thread"]}\nfutures-util="0.3"\nanyhow="1"\n[patch.crates-io]\n'+'\n'.join(patch)+'\n')
(out/'src/main.rs').write_text(pathlib.Path(a.example).read_text() if a.example else 'fn main() { let _ = openworker_sdk::agents::Harness::builder(); }\n')
if a.fetch:
 subprocess.run(['cargo','fetch','--manifest-path',str(out/'Cargo.toml')],check=True)
subprocess.run(['cargo','check','--offline','--manifest-path',str(out/'Cargo.toml')],check=True)
print('Verified isolated archive consumer:',out)
