#!/usr/bin/env python3
"""Read-only CLI qualification with a real pinned chart and trapped AWS commands.
No cloud login, keys or network are used. This does not qualify installation.
"""
import argparse
import json
import os
import pathlib
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--ows', required=True)
p.add_argument('--openshell-archive', required=True)
a = p.parse_args()
repo = pathlib.Path(__file__).resolve().parents[3]
ows = str(pathlib.Path(a.ows).resolve())
with tempfile.TemporaryDirectory(prefix='ows-up-plan-') as tmp:
    root = pathlib.Path(tmp)
    tools = root / 'tools'
    tools.mkdir()
    aws = tools / 'aws'
    aws.write_text('''#!/usr/bin/python3
import json, os, sys
args=sys.argv[1:]; command=args[:2]
with open(os.environ['OWS_FIXTURE_CALLS'],'a') as f:f.write(json.dumps(command)+'\\n')
if command==['sts','get-caller-identity']:
 v={'Account':'123456789012','Arn':'arn:aws:iam::123456789012:role/operator'}
elif command==['ec2','describe-instance-types']:
 v={'InstanceTypes':[{'InstanceType':name,'VCpuInfo':{'DefaultVCpus':cpu},'MemoryInfo':{'SizeInMiB':mem*1024},'ProcessorInfo':{'SupportedArchitectures':['arm64']}} for name,cpu,mem in [('t4g.large',2,8),('t4g.xlarge',4,16),('t4g.2xlarge',8,32)]]}
elif command==['ec2','describe-instance-type-offerings']:
 v={'InstanceTypeOfferings':[{'InstanceType':n} for n in ['t4g.large','t4g.xlarge','t4g.2xlarge']]}
elif command==['pricing','get-products']:
 filters=json.loads(args[args.index('--filters')+1]); monthly=any(f['Value'] in ['Storage','Database Storage'] for f in filters)
 dimension={'unit':'GB-Mo' if monthly else 'Hrs','beginRange':'0','endRange':'Inf','pricePerUnit':{'USD':'0.1'}}
 v={'PriceList':[json.dumps({'terms':{'OnDemand':{'term':{'priceDimensions':{'fixture':dimension}}}}})]}
else:
 raise SystemExit('UNEXPECTED CLOUD COMMAND: '+str(command))
print(json.dumps(v))
''')
    compile(aws.read_text(), str(aws), "exec")
    aws.chmod(0o700)
    env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ.get('PATH', ''), HOME=str(root), OWS_FIXTURE_CALLS=str(root/'calls.jsonl'))
    def run(args, ok=True):
        r = subprocess.run([ows]+args, env=env, cwd=root, capture_output=True, text=True)
        if ok and r.returncode != 0:
            raise AssertionError(r.stderr)
        if not ok and r.returncode == 0:
            raise AssertionError('Unexpected success')
        return r
    run(['deploy','guard','package','--out',str(root/'guard')])
    (root/'shell.json').write_text(json.dumps({'global':{'image':{'tag':'0.1.7'}},'server':{'auth':{'allowUnauthenticatedUsers':False}}}))
    (root/'platform.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':[]}))
    subprocess.run(['python3',str(repo/'deploy/service/up/package.py'),'--version','fixture','--architecture','arm64',
        '--harness-image','ghcr.io/example/harness@sha256:'+'a'*64,'--runner-image','ghcr.io/example/runner@sha256:'+'b'*64,
        '--service-chart',str(repo/'deploy/service/chart'),'--openshell-archive',str(pathlib.Path(a.openshell_archive).resolve()),
        '--openshell-values',str(root/'shell.json'),'--platform-manifest',str(root/'platform.json'),
        '--database-ca',str(repo/'harness/store-postgres/tests/fixtures/public-ca.pem'),'--out',str(root/'release')], check=True, capture_output=True)
    infrastructure = json.loads((repo/'harness/service-cli/examples/aws-infrastructure.json').read_text())
    infrastructure['guardCode']['sha256'] = json.loads((root/'guard/package-manifest.json').read_text())['archiveSha256']
    yaml = "name: fixture\ncloud: {provider: aws, accountId: '123456789012', region: us-west-2}\nnodes: {count: 2, instanceType: t4g.large}\nadvanced:\n  release: " + json.dumps(str(root/'release/release.json')) + '\n  aws: {operatorArn: "arn:aws:iam::123456789012:role/operator"}\n  infrastructure: ' + json.dumps(infrastructure) + '\n'
    (root/'deployment.yaml').write_text(yaml)
    args = ['deploy','aws','up','--config',str(root/'deployment.yaml'),'--state',str(root/'state')]
    run(args+['--plan-only'])
    first = (root/'state/review.json').read_bytes()
    review = json.loads(first)
    assert review['config']['nodes']['count'] == 2
    plan = json.loads((root/'state/infrastructure/deployment-plan.json').read_text())
    assert plan['autoExpire'] is False
    assert plan['budget']['estimateCheckDisabled'] is True
    assert not (root/'setup.json').exists()
    run(args+['--plan-only'])
    assert (root/'state/review.json').read_bytes() == first
    too_small=run(args+['--plan-only','--max-estimated-cost-usd','1'],ok=False)
    assert 'exceeds' in too_small.stderr
    run(args+['--plan-only','--max-estimated-cost-usd','100000'])
    quote=json.loads((root/'state/cost-estimate.json').read_text())
    assert quote['ceilingCheck']=='within_estimated_ceiling'
    assert quote['reviewDigest']==review['digest']
    assert (root/'state/review.json').read_bytes()==first
    run(args+['--accept-plan','wrong'], ok=False)
    run(args+['--accept-plan',review['digest']], ok=False)  # bootstrap still unapproved
    kubectl = tools / 'kubectl'
    kubectl.write_text('#!/usr/bin/python3\nraise SystemExit(1)\n')
    kubectl.chmod(0o700)
    unavailable = run(args+['--accept-plan',review['digest'],'--accept-bootstrap',review['digest']], ok=False)
    assert 'Local prerequisite kubectl unavailable' in unavailable.stderr
    calls = (root/'calls.jsonl').read_bytes()
    (root/'deployment.yaml').write_text(yaml.replace('count: 2', 'count: 3'))
    result = run(args+['--plan-only'], ok=False)
    assert 'changed' in result.stderr
    assert (root/'calls.jsonl').read_bytes() == calls
    assert all(json.loads(line) in [['sts','get-caller-identity'],['ec2','describe-instance-types'],['ec2','describe-instance-type-offerings'],['pricing','get-products']] for line in calls.splitlines())
    print(json.dumps({'oneAuthoredYaml':True,'planCeilingChecked':True,'quoteRefreshPreservesIntent':True,'readOnlyPlan':True,'stableResume':True,'changedConfigRejected':True,'unapprovedBootstrapBlocked':True,'missingLocalToolBlocked':True,'cloudMutations':0,'installationQualified':False}))
