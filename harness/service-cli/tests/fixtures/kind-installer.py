#!/usr/bin/python3
"""Isolated durable fake Kubernetes/Helm server; no network or credentials."""
import json,os,pathlib,sys
home=pathlib.Path(os.environ['HOME']);store=pathlib.Path(os.environ.get('KUBE_FIXTURE_FILE',str(home/'remote.json')));a=sys.argv[1:];tool=pathlib.Path(sys.argv[0]).name
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
        name=a[1];labels=dict(pair.split('=',1) for pair in arg('--labels').split(','));releases[name]={'version':1,'info':{'status':'failed' if os.environ.get('FAIL_RELEASE') else 'deployed','description':'context deadline exceeded' if os.environ.get('FAIL_RELEASE') else arg('--description')},'uid':'release-uid','labels':labels};store.write_text(json.dumps(state))
        if os.environ.get('FAIL_RELEASE'):sys.exit(81)
        if os.environ.get('FAIL_AFTER')=='yes':sys.exit(78)
        finish('installed')
    if a[0]=='uninstall':del releases[a[1]];finish('uninstalled')
    sys.exit(99)
# kubectl global options precede its command.
a=a[4:]
if a[0]=='get' and len(a)>1:a[1]=a[1].lower()
if a[:2]==['get','namespace']:
    ns=a[2]
    if ns=='kube-system':finish({'metadata':{'uid':state.get('clusterUid','cluster-uid')}})
    finish(state['namespaces'].get(ns))
if state.get('bootstrapPlatform') and a[0]=='get' and a[1] in ['deployment','customresourcedefinition','namespace'] and not (a[1]=='namespace' and a[2]=='kube-system'):
    if a[1]=='namespace':finish(state['namespaces'].get(a[2]))
    finish(state.get('objects',{}).get(a[1]+':'+arg('-n','')+':'+a[2]))
if a[:2]==['get','crd']:finish({'spec':{'versions':[{'name':'v1beta1','served':True}]}})
if a[:2]==['get','storageclasses']:
    classes=state.get('storageclasses',[v for k,v in state.get('objects',{}).items() if k.startswith('storageclass:')])
    finish({'items':classes or [{'metadata':{'annotations':{'storageclass.kubernetes.io/is-default-class':'true'}}}]})
if a[:2]==['get','daemonsets']:finish({'items':[{'metadata':{'name':'calico-node'},'status':{'desiredNumberScheduled':1,'numberReady':1}}]})
if a[0]=='create':
    ns=json.load(sys.stdin);ns['metadata']['uid']='uid-'+ns['kind']+'-'+ns['metadata']['name']
    if ns['kind']=='Namespace':
        ns['metadata']['uid']='uid-'+ns['metadata']['name'];state['namespaces'][ns['metadata']['name']]=ns
    else:
        if ns['kind']=='Deployment':ns['status']={'availableReplicas':1}
        key=ns['kind'].lower()+':'+ns['metadata'].get('namespace','')+':'+ns['metadata']['name'];state.setdefault('objects',{})[key]=ns
        state.setdefault('objectCreates',{})[key]=state.get('objectCreates',{}).get(key,0)+1;store.write_text(json.dumps(state))
        if os.environ.get('FAIL_AFTER_OBJECT')==ns['kind']:sys.exit(80)
    finish(ns)
if a[:2]==['get','secret']:
    name=a[2]
    if name=='openshell-client-tls':finish({'metadata':{'uid':'external-tls'},'data':{'ca.crt':'Y2E=','tls.crt':'Y2VydA==','tls.key':'a2V5'}})
    if name.startswith('sh.helm.release.'):
        ns=arg('-n');name=name[len('sh.helm.release.v1.'):].rsplit('.v',1)[0]
        r=state['releases'].get(ns,{}).get(name);finish({'metadata':{'uid':r['uid'],'labels':r.get('labels',{})}} if r else None)
    key='secret:'+arg('-n','')+':'+name
    if state.get('managedSecrets'):finish(state.get('objects',{}).get(key))
    finish({'metadata':{'uid':'secret-uid'}})
if a[:2]==['get','deployment']:
    if a[2]=='agent-sandbox-controller':finish({'status':{'availableReplicas':0 if state.get('controllerUnavailable') else 1}})
    if a[2]=='postgres':finish({'metadata':{'uid':'db'}})
    ns=arg('-n');finish({'metadata':{'uid':'api'}} if state['releases'].get(ns) else None)
if a[:2]==['get','job']:finish({'metadata':{'uid':'bootstrap'},'status':{'succeeded':1}})
if 'exec' in a:
    if 'service-install-status' in a:finish({'schemaVersion':1,'openSessions':1 if state.get('active') else 0,'liveEnvironments':0,'pendingAdmissions':0,'queuedTurns':0})
    if 'to_regclass' in a[-1]:finish('t|t')
    finish('1' if state.get('active') else '0')
if '--raw' in a:
    options=json.load(sys.stdin);ns=arg('--raw').rsplit('/',1)[1]
    if state['namespaces'][ns]['metadata']['uid']!=options['preconditions']['uid']:sys.exit(79)
    del state['namespaces'][ns];finish({'status':'Success'})
if 'sandboxes.agents.x-k8s.io' in a:finish({'items':[]})
if any(op in a for op in ['rollout','scale','wait']):finish('ready')
if a[0]=='get':finish(state.get('objects',{}).get(a[1].lower()+':'+arg('-n','')+':'+a[2]))
sys.exit(99)
