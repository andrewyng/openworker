#!/usr/bin/python3
"""Offline fake gcloud: no credentials, subprocesses or network."""
import json, os, pathlib, sys, time
args=sys.argv[1:];home=pathlib.Path(os.environ['HOME']);scenario=os.environ.get('TEST_SCENARIO','ok')
def value(flag,default=None):
    for i,a in enumerate(args):
        if a==flag:return args[i+1]
        if a.startswith(flag+'='):return a.split('=',1)[1]
    return default
def output(v):print(json.dumps(v));sys.exit(0)
def failure():print('PERMISSION_DENIED secret-sentinel-never-persist',file=sys.stderr);sys.exit(42)
command=[]
for x in args:
    if x.startswith('--'):break
    command.append(x)
command=tuple(command)
allowed={('config','list'),('auth','list'),('projects','describe'),('billing','projects','describe'),('services','list'),('projects','get-iam-policy'),('iam','service-accounts','describe'),('iam','service-accounts','get-iam-policy'),('compute','regions','describe'),('container','get-server-config')}
command=next((c for c in sorted(allowed,key=len,reverse=True) if tuple(args[:len(c)])==c),command)
with (home/'calls.jsonl').open('a') as f:f.write(json.dumps({'args':args,'prompts':os.environ.get('CLOUDSDK_CORE_DISABLE_PROMPTS'),'logHttp':os.environ.get('CLOUDSDK_CORE_LOG_HTTP'),'tokenPresent':bool(os.environ.get('CLOUDSDK_AUTH_ACCESS_TOKEN'))})+'\n')
assert command in allowed, command
assert value('--project')=='my-gcp-project'
assert value('--billing-project')=='my-gcp-project'
assert value('--account')=='operator@example.com'
assert '--quiet' in args
assert os.environ.get('CLOUDSDK_CORE_DISABLE_PROMPTS')=='1'
assert os.environ.get('CLOUDSDK_CORE_LOG_HTTP')=='false'
if scenario=='timeout':time.sleep(5)
if scenario=='large-output':print('secret-sentinel-never-persist'+'x'*(5*1024*1024));sys.exit(0)
if scenario=='malformed':print('secret-sentinel-never-persist invalid');sys.exit(0)
if scenario=='access-denied':failure()
i=json.loads((home/'bootstrap'/'gcp-identities.json').read_text());accounts=i['accounts']
if command==('config','list'):
    output({'api_endpoint_overrides':{'iam':'https://untrusted.invalid/'}} if scenario=='endpoint-config' else {})
if command==('auth','list'):output([{'account':'other@example.com' if scenario=='wrong-caller' else 'operator@example.com','status':'ACTIVE'}])
if command==('projects','describe'):
    output({'projectId':'foreign-project' if scenario=='wrong-project' else 'my-gcp-project','projectNumber':'999999999999' if scenario=='wrong-number' else '123456789012','lifecycleState':'DELETE_REQUESTED' if scenario=='inactive-project' else 'ACTIVE'})
if command==('billing','projects','describe'):output({'projectId':'my-gcp-project','billingEnabled':scenario!='billing-disabled','billingAccountName':'billingAccounts/redacted-fixture'})
if command==('services','list'):
    if scenario=='truncated':output([{'config':{'name':'unused.googleapis.com'}}]*1001)
    output([{'config':{'name':s}} for s in ([] if scenario=='missing-apis' else i['requiredBootstrapApis']+i['requiredInfrastructureApis'])])
if command==('projects','get-iam-policy'):
    bindings=[{'role':b['role'],'members':[b['member']]} for b in i['bindings'] if b['resource'].startswith('projects/')]
    if scenario=='broad-role':bindings.append({'role':'roles/owner','members':['serviceAccount:'+accounts['executor']]})
    output({'bindings':bindings})
if command==('iam','service-accounts','describe'):
    email=args[3]
    if scenario=='missing-identity' or (scenario=='missing-service-agent' and email.startswith('service-')):failure()
    output({'projectId':'my-gcp-project','email':email,'uniqueId':'10000000000','disabled':scenario=='disabled-identity'})
if command==('iam','service-accounts','get-iam-policy'):
    email=args[3];bs=[{'role':b['role'],'members':[b['member']]} for b in i['bindings'] if b['resource']==email]
    if scenario=='missing-service-agent':bs=[b for b in bs if not b['members'][0].startswith('serviceAccount:service-')]
    if scenario=='conditional-trust':bs[0]['condition']={'expression':'true','title':'fixture'}
    if scenario=='excess-trust':bs.append({'role':'roles/iam.serviceAccountTokenCreator','members':['user:foreign@example.com']})
    output({'bindings':bs})
if command==('compute','regions','describe'):output({'name':'us-central1','quotas':[{'metric':'CPUS','limit':32,'usage':1}]})
if command==('container','get-server-config'):output({'validMasterVersions':['1.35.0-fixture']})
raise AssertionError(command)
