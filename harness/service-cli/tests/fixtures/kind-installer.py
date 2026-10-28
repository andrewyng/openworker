#!/usr/bin/python3
"""Isolated durable fake Kubernetes/Helm server; no network or credentials."""
import json,os,pathlib,sys
home=pathlib.Path(os.environ['HOME']);store=home/'remote.json';a=sys.argv[1:];tool=pathlib.Path(sys.argv[0]).name
state=json.loads(store.read_text())
def finish(value=None):
    store.write_text(json.dumps(state))
    if value is not None:print(value if isinstance(value,str) else json.dumps(value))
    sys.exit(0)
def arg(flag,default=None):return a[a.index(flag)+1] if flag in a else default
with (home/'calls').open('a') as f:f.write(json.dumps([tool,a])+'\n')
if tool=='helm':
    ns=arg('-n');releases=state['releases'].setdefault(ns,{})
    if a[0]=='list':finish([{'name':n} for n in releases])
    if a[0]=='status':finish(releases[a[1]])
    if a[0]=='install':
        if os.environ.get('FAIL_BEFORE')=='yes':sys.exit(77)
        name=a[1];releases[name]={'version':1,'info':{'status':'deployed','description':arg('--description')},'uid':'release-uid'};store.write_text(json.dumps(state))
        if os.environ.get('FAIL_AFTER')=='yes':sys.exit(78)
        finish('installed')
    if a[0]=='uninstall':del releases[a[1]];finish('uninstalled')
    sys.exit(99)
# kubectl global options precede its command.
a=a[4:]
if a[:2]==['get','namespace']:
    ns=a[2]
    if ns=='kube-system':finish({'metadata':{'uid':state.get('clusterUid','cluster-uid')}})
    finish(state['namespaces'].get(ns))
if a[:2]==['get','crd']:finish({'spec':{'versions':[{'name':'v1beta1','served':True}]}})
if a[:2]==['get','storageclasses']:finish({'items':[{'metadata':{'annotations':{'storageclass.kubernetes.io/is-default-class':'true'}}}]})
if a[:2]==['get','daemonsets']:finish({'items':[{'metadata':{'name':'calico-node'},'status':{'desiredNumberScheduled':1,'numberReady':1}}]})
if a[0]=='create':
    ns=json.load(sys.stdin);ns['metadata']['uid']='uid-'+ns['metadata']['name'];state['namespaces'][ns['metadata']['name']]=ns;finish(ns)
if a[:2]==['get','secret']:
    name=a[2]
    if name.startswith('sh.helm.release.'):
        ns=arg('-n');name=name[len('sh.helm.release.v1.'):].rsplit('.v',1)[0]
        r=state['releases'].get(ns,{}).get(name);finish({'metadata':{'uid':r['uid']}} if r else None)
    finish({'metadata':{'uid':'secret-uid'}})
if a[:2]==['get','deployment']:
    if a[2]=='agent-sandbox-controller':finish({'status':{'availableReplicas':0 if state.get('controllerUnavailable') else 1}})
    if a[2]=='postgres':finish({'metadata':{'uid':'db'}})
    ns=arg('-n');finish({'metadata':{'uid':'api'}} if state['releases'].get(ns) else None)
if 'exec' in a:
    if 'to_regclass' in a[-1]:finish('t|t')
    finish('1' if state.get('active') else '0')
if '--raw' in a:
    options=json.load(sys.stdin);ns=arg('--raw').rsplit('/',1)[1]
    if state['namespaces'][ns]['metadata']['uid']!=options['preconditions']['uid']:sys.exit(79)
    del state['namespaces'][ns];finish({'status':'Success'})
if 'sandboxes.agents.x-k8s.io' in a:finish({'items':[]})
if any(op in a for op in ['rollout','scale','wait']):finish('ready')
sys.exit(99)
