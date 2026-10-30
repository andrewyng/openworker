#!/usr/bin/python3
"""Fake gcloud for executing exported bootstrap scripts without cloud access."""
import json,os,pathlib,sys
args=sys.argv[1:];home=pathlib.Path(os.environ['HOME']);scenario=os.environ.get('TEST_SCENARIO','ok')
i=json.loads((home/'bootstrap'/'gcp-identities.json').read_text())
def val(flag):
    for n,a in enumerate(args):
        if a==flag:return args[n+1]
        if a.startswith(flag+'='):return a.split('=',1)[1]
    return ''
with (home/'bootstrap-calls.jsonl').open('a') as f:f.write(json.dumps(args)+'\n')
assert val('--project')=='my-gcp-project'
assert val('--billing-project')=='my-gcp-project'
assert val('--account')=='operator@example.com'
assert '--quiet' in args
if args[:2]==['config','list']:
    print('https://untrusted.invalid' if scenario=='endpoint' else '');sys.exit(0)
if args[:2]==['auth','list']:print('foreign@example.com' if scenario=='wrong-caller' else 'operator@example.com');sys.exit(0)
if args[:2]==['projects','describe']:
    print(('999999999999' if scenario=='wrong-number' else '123456789012') if 'projectNumber' in val('--format') else 'ACTIVE');sys.exit(0)
if args[:2]==['services','list']:print('\n'.join([] if scenario=='missing-api' else i['requiredBootstrapApis']));sys.exit(0)
if args[:3]==['iam','service-accounts','list']:
    p=home/'created.json';created=json.loads(p.read_text()) if p.exists() else []
    if scenario=='existing':created=[i['accounts']['setup']]
    print('\n'.join(created));sys.exit(0)
create=args[:3]==['iam','service-accounts','create']
binding=args[:2]==['projects','add-iam-policy-binding'] or args[:3]==['iam','service-accounts','add-iam-policy-binding']
managed=args[:4]==['beta','services','identity','create']
assert create or binding or managed, args
p=home/'mutations.json';ms=json.loads(p.read_text()) if p.exists() else [];ms.append(args);p.write_text(json.dumps(ms))
if (scenario=='partial-failure' and len(ms)==3) or (scenario=='missing-service-agent' and managed):print('Simulated lost mutation response',file=sys.stderr);sys.exit(42)
if create:
    p=home/'created.json';created=json.loads(p.read_text()) if p.exists() else [];created.append(args[3]+'@my-gcp-project.iam.gserviceaccount.com');p.write_text(json.dumps(created))
print(json.dumps({'fixture':True}))
