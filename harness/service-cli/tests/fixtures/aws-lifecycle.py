#!/usr/bin/python3
"""Durable isolated fake cloud. Never invokes any network/provider executable."""
import os,json,sys,pathlib,base64
root=pathlib.Path(os.environ['HOME']);args=sys.argv[1:];service,op=args[:2]
def debug_error(t,e,b):
 import traceback
 with (root/'fake-error.log').open('a') as f:traceback.print_exception(t,e,b,file=f)
sys.excepthook=debug_error
def arg(name):return args[args.index(name)+1]
q=json.loads(arg('--cli-input-json'))
plan=json.loads((root/'plan/deployment-plan.json').read_text())
prefix='ows-'+plan['name']+'-'+plan['deploymentId'][4:16];account=plan['target']['account'];region=plan['target']['region']
file=root/'remote.json';remote=json.loads(file.read_text()) if file.exists() else {}
scenario=os.environ.get('SCENARIO','')
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps([service,op,q])+'\n')
assert os.environ['AWS_MAX_ATTEMPTS']=='1'
assert os.environ['AWS_IGNORE_CONFIGURED_ENDPOINT_URLS']=='true'
assert arg('--profile')=='fixture-setup'
def out(v):print(json.dumps(v));sys.exit(0)
def fail():print('secret-sentinel-do-not-persist',file=sys.stderr);sys.exit(78)
def persist():file.write_text(json.dumps(remote))
if scenario=='access-denied':fail()
if service=='sts':
 assert op=='get-caller-identity'
 out({'Account':account,'Arn':f'arn:aws:sts::{account}:assumed-role/{prefix}-'+('wrong' if scenario=='wrong-identity' else 'setup')+'/fixture'})
if service=='cloudformation':
 selector=q.get('StackName')
 def find():return next((s for s in remote.values() if selector in [s['StackName'],s['StackId']]),None)
 if op=='create-stack':
  if scenario=='fail-before-create':fail()
  assert selector not in remote
  s={'StackName':selector,'StackId':f'arn:aws:cloudformation:{region}:{account}:stack/{selector}/original-id','StackStatus':'CREATE_COMPLETE','RoleARN':q['RoleARN'],'Tags':q['Tags'],'template':json.loads(q['TemplateBody']),'tokens':[q['ClientRequestToken']]}
  if selector.endswith('-infra'):
   registry=f'{account}.dkr.ecr.{region}.amazonaws.com/{prefix}'
   values={'Cluster':prefix,'ExpectedClusterArn':f'arn:aws:eks:{region}:{account}:cluster/{prefix}','Vpc':'vpc-0123456789abcdef0','DatabaseEndpoint':f'{prefix}.fixture.{region}.rds.amazonaws.com','DatabaseSecretArn':f'arn:aws:secretsmanager:{region}:{account}:secret:{prefix}-database-AbCd12','ArtifactBucket':prefix+'-'+account,'ArtifactPrefix':plan['deploymentId']+'/','HarnessRepository':registry+'/harness','RunnerRepository':registry+'/runner','UiRepository':registry+'/service-ui'}
   s['Outputs']=[{'OutputKey':k,'OutputValue':v} for k,v in values.items()]
  remote[selector]=s;persist()
  if scenario=='fail-after-create':fail()
  out({'StackId':s['StackId']})
 s=find()
 if not s:
  assert op=='describe-stacks'
  print(f'An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id {selector} does not exist',file=sys.stderr);sys.exit(77)
 if op=='describe-stacks':
  value={k:v for k,v in s.items() if k not in ['template','tokens']}
  if scenario=='replaced-stack':value['StackId']+='-replacement'
  if scenario=='failed-stack':value['StackStatus']='ROLLBACK_COMPLETE'
  out({'Stacks':[value]})
 if op=='get-template':
  template=s['template'].copy()
  if scenario=='template-drift':template['Description']='changed'
  out({'TemplateBody':json.dumps(template)})
 if op=='describe-stack-events':
  rows=[{'StackId':s['StackId'],'PhysicalResourceId':s['StackId'],'ResourceType':'AWS::CloudFormation::Stack','ClientRequestToken':t} for t in s['tokens']]
  if scenario=='wrong-event':rows=[]
  out({'StackEvents':rows,**({'NextToken':'repeat'} if scenario=='repeat-events' else {})})
 if op=='delete-stack':
  if scenario=='fail-before-delete':fail()
  s['StackStatus']='DELETE_IN_PROGRESS';s['tokens'].append(q['ClientRequestToken']);persist()
  if scenario=='fail-after-delete':fail()
  out({})
 raise Exception('forbidden cloudformation operation')
if service=='eks':
 network=root/'network.json'
 config=json.loads(network.read_text()) if network.exists() else {'enableNetworkPolicy':'true','env':{'NETWORK_POLICY_ENFORCING_MODE':'standard'}}
 if op=='describe-cluster':
  assert q['name']==prefix
  out({'cluster':{'name':prefix,'arn':f'arn:aws:eks:{region}:{account}:cluster/{prefix}','status':'ACTIVE','version':plan['infrastructure']['kubernetesVersion'],'endpoint':f'https://fixture.{region}.eks.amazonaws.com','certificateAuthority':{'data':base64.b64encode(b'-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n').decode()},'resourcesVpcConfig':{'endpointPublicAccess':scenario=='public-endpoint','endpointPrivateAccess':True,'vpcId':'vpc-0123456789abcdef0'}}})
 if op=='describe-addon':out({'addon':{'addonArn':f'arn:aws:eks:{region}:{account}:addon/{prefix}/vpc-cni/original-id','addonVersion':plan['infrastructure']['addons']['vpcCni'],'status':'ACTIVE','configurationValues':json.dumps(config)}})
 if op=='update-addon':
  assert q['resolveConflicts']=='NONE' and q['clientRequestToken']
  network.write_text(q['configurationValues'])
  if scenario=='lost-cni-reply':fail()
  out({'update':{'id':'original-update'}})
 raise Exception('forbidden eks operation')
if service=='secretsmanager':
 assert op=='get-secret-value' and q['SecretId']==f'arn:aws:secretsmanager:{region}:{account}:secret:{prefix}-database-AbCd12'
 out({'ARN':q['SecretId'],'SecretString':json.dumps({'host':f'{prefix}.fixture.{region}.rds.amazonaws.com','port':5432,'dbname':'openworker','username':'openworker','password':'private-password-do-not-persist'})})
if service=='s3api':
 if op=='get-bucket-tagging':out({'TagSet':[{'Key':'ows:deployment-id','Value':plan['deploymentId']}]})
 if op=='get-bucket-versioning':out({})
 if op=='list-objects-v2':out({'KeyCount':1 if scenario=='nonempty-bucket' else 0,'IsTruncated':False})
 if op=='list-multipart-uploads':out({'IsTruncated':False})
 raise Exception('forbidden s3 operation')
if service=='ecr':
 registry=f'{account}.dkr.ecr.{region}.amazonaws.com'
 if op=='describe-repositories':
  repository=q['repositoryNames'][0]
  assert repository.startswith(prefix+'/')
  out({'repositories':[{'repositoryArn':f'arn:aws:ecr:{region}:{account}:repository/{repository}','registryId':account,'repositoryUri':registry+'/'+repository,'imageTagMutability':'MUTABLE' if scenario=='mutable-repository' else 'IMMUTABLE','createdAt':100}]})
 if op=='get-authorization-token':
  out({'authorizationData':[{'proxyEndpoint':'https://'+registry,'authorizationToken':base64.b64encode(b'AWS:registry-password-do-not-persist').decode()}]})
 assert op=='batch-get-image'
 repository=q['repositoryName'];tag=q['imageIds'][0]['imageTag'];file=root/'ecr.json'
 images=json.loads(file.read_text()) if file.exists() else {};image=images.get(registry+'/'+repository+':'+tag)
 if image:out({'images':[{'registryId':account,'repositoryName':repository,'imageId':{'imageTag':tag,'imageDigest':image['imageDigest']},'imageManifest':image['imageManifest']}],'failures':[]})
 out({'images':[],'failures':[{'imageId':{'imageTag':tag},'failureCode':'ImageNotFound'}]})
if service=='lambda':
 function=f'arn:aws:lambda:{region}:{account}:function:{prefix}-cleanup'
 assert q['FunctionName']==function
 if op=='get-function-configuration':
  template=json.loads((root/'plan/guard.cfn.json').read_text())['Resources']['Cleanup']['Properties']
  out({'FunctionArn':function,'Runtime':'nodejs22.x','Handler':'index.handler','State':'Active','LastUpdateStatus':'Successful','Role':f'arn:aws:iam::{account}:role/{prefix}-cleanup','CodeSha256':base64.b64encode(bytes.fromhex(plan['infrastructure']['guardCode']['sha256'])).decode() if scenario!='wrong-code' else 'eA==','Environment':template['Environment']})
 if op=='get-function-concurrency':out({'ReservedConcurrentExecutions':0 if scenario=='guard-disabled' else 1})
 assert op=='invoke'
 assert json.loads(base64.b64decode(q['Payload']))=={'mode':'health'}
 path=pathlib.Path(args[-1]);path.write_text(json.dumps({'status':'healthy','destructiveActions':0}))
 out({'StatusCode':200,**({'FunctionError':'Unhandled'} if scenario=='lambda-error' else {})})
if service=='events':
 if op=='describe-rule':out({'State':'DISABLED' if scenario=='guard-schedule-disabled' else 'ENABLED','ScheduleExpression':'rate(5 minutes)','Arn':f'arn:aws:events:{region}:{account}:rule/{prefix}-cleanup'})
 assert op=='list-targets-by-rule'
 out({'Targets':[{'Arn':f'arn:aws:lambda:{region}:{account}:function:{prefix}-cleanup','RetryPolicy':{'MaximumRetryAttempts':0}}]})
raise Exception('FORBIDDEN_PROVIDER_CALL '+service+' '+op)
