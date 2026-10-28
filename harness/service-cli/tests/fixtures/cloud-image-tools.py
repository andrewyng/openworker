#!/usr/bin/python3
"""Synthetic Docker/tar only. No socket, network or real credential use."""
import os,sys,json,pathlib,hashlib
root=pathlib.Path(os.environ['HOME']);args=sys.argv[1:];tool=pathlib.Path(sys.argv[0]).name
scenario=os.environ.get('SCENARIO','');config=json.dumps({'os':'linux','architecture':'arm64','config':{'Cmd':['fixture']}},separators=(',',':'))
digest=hashlib.sha256(config.encode()).hexdigest()
with (root/'image-calls.jsonl').open('a') as f:f.write(json.dumps([tool,args])+'\n')
def out(v):print(v if isinstance(v,str) else json.dumps(v));sys.exit(0)
def fail():print('secret-sentinel-do-not-persist',file=sys.stderr);sys.exit(79)
if tool=='tar':
 if args[-1]=='manifest.json':out([{'Config':'blobs/sha256/'+digest}])
 assert args[-1]=='blobs/sha256/'+digest
 sys.stdout.write(config);sys.exit(0)
if args[:2]==['context','inspect']:out('unix:///fixture/docker.sock')
assert args[0]=='--config' and args[2]=='--host'
private=pathlib.Path(args[1]);args=args[4:]
if args[:2]==['image','inspect']:
 identifier=hashlib.sha256(('changed' if scenario=='image-changed' else 'fixture').encode()).hexdigest()
 out([{'Id':'sha256:'+identifier,'Os':'linux','Architecture':'amd64' if scenario=='wrong-image-platform' else 'arm64','Size':1024}])
if args[0]=='login':
 password=sys.stdin.read();assert password=='registry-password-do-not-persist'
 (private/'config.json').write_text(json.dumps({'auths':{'fixture':{'auth':password}}}))
 out('logged in')
if args[:2]==['image','save']:
 pathlib.Path(args[args.index('--output')+1]).write_bytes(b'fake archive');out('')
if args[:2]==['image','tag']:out('')
if args[:2]==['image','push']:
 if scenario=='fail-before-push':fail()
 target=args[-1];file=root/'ecr.json';images=json.loads(file.read_text()) if file.exists() else {}
 manifest=json.dumps({'schemaVersion':2,'config':{'digest':'sha256:'+digest},'layers':[]},separators=(',',':'))
 images[target]={'imageManifest':manifest,'imageDigest':'sha256:'+hashlib.sha256(manifest.encode()).hexdigest()};file.write_text(json.dumps(images))
 if scenario=='fail-after-push':fail()
 out('pushed')
raise Exception('forbidden Docker operation')
