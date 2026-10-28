#!/usr/bin/python3
"""Deterministic fake AWS CLI. Does not load credentials or access a network."""
import hashlib,json,os,pathlib,sys,time
args=sys.argv[1:];service,operation=args[:2]; scenario=os.environ.get('TEST_SCENARIO','ok')
def arg(name,default=None):
    return args[args.index(name)+1] if name in args else default
def output(v):
    print(json.dumps(v));sys.exit(0)
def fail(code='AccessDenied'):
    print(code+' secret-sentinel-do-not-persist',file=sys.stderr);sys.exit(42)
def token():
    v=arg('--cli-input-json');return next(iter(json.loads(v).values())) if v else None
home=pathlib.Path(os.environ['HOME'])
with (home/'calls.jsonl').open('a') as f:
    f.write(json.dumps({'args':args,'temporaryCredentialPresent':bool(os.environ.get('AWS_SESSION_TOKEN')),
        'endpointOverridesIgnored':os.environ.get('AWS_IGNORE_CONFIGURED_ENDPOINT_URLS'),
        'maxAttempts':os.environ.get('AWS_MAX_ATTEMPTS'),'pager':os.environ.get('AWS_PAGER')})+'\n')
allowed={('sts','get-caller-identity'),('sts','assume-role'),('iam','get-role'),
 ('ec2','describe-availability-zones'),('ec2','describe-instance-type-offerings'),('ec2','describe-instances'),('ec2','describe-capacity-reservations'),('ec2','describe-instance-types'),('ec2','describe-images'),('ec2','describe-vpcs'),('ec2','describe-addresses'),('ec2','describe-nat-gateways'),
 ('eks','describe-cluster-versions'),('eks','describe-addon-versions'),('eks','list-clusters'),
 ('rds','describe-db-engine-versions'),('rds','describe-orderable-db-instance-options'),('rds','describe-account-attributes'),
 ('ssm','get-parameter'),('service-quotas','get-service-quota'),('pricing','get-products'),
 ('s3api','get-bucket-location'),('s3api','head-object'),('s3api','get-object'),('accessanalyzer','validate-policy')}
if (service,operation) not in allowed:fail('FORBIDDEN_MUTATION')
if os.environ.get('AWS_MAX_ATTEMPTS')!='1':fail('RETRIES_ENABLED')
if scenario=='timeout':time.sleep(20)
if scenario=='large-output':
    sys.stdout.write('secret-sentinel-do-not-persist'+'x'*(5*1024*1024));sys.exit(0)
if scenario=='malformed':print('secret-sentinel-do-not-persist invalid-json');sys.exit(0)
if service=='sts':
    if scenario=='access-denied':fail()
    if operation=='assume-role':
        if scenario=='assume-denied':fail()
        policy=json.loads(arg('--policy'))
        assert policy['Statement']==[{'Effect':'Allow','Action':'sts:GetCallerIdentity','Resource':'*'}]
        assert arg('--duration-seconds')=='900'
        output({'Credentials':{'AccessKeyId':'fake-access-key','SecretAccessKey':'fake-secret-key','SessionToken':'fake-session-token'}})
    if os.environ.get('AWS_SESSION_TOKEN'):
        assert os.environ.get('AWS_SECRET_ACCESS_KEY')=='fake-secret-key'
        assert '--profile' not in args
        plan=json.loads((home/'plan'/'deployment-plan.json').read_text());prefix='ows-'+plan['name']+'-'+plan['deploymentId'][4:16]
        role='wrong' if scenario=='assume-wrong-identity' else prefix+'-setup'
        output({'Account':'123456789012','Arn':f'arn:aws:sts::123456789012:assumed-role/{role}/preflight'})
    account='999999999999' if scenario=='wrong-account' else '123456789012'
    role='wrong' if scenario=='wrong-operator' else 'operator'
    output({'Account':account,'Arn':f'arn:aws:sts::{account}:assumed-role/{role}/session'})
if service=='iam':
    if scenario=='missing-role':fail('NoSuchEntity')
    names={'AWSServiceRoleForAmazonEKS':'eks.amazonaws.com','AWSServiceRoleForAmazonEKSNodegroup':'eks-nodegroup.amazonaws.com','AWSServiceRoleForAutoScaling':'autoscaling.amazonaws.com','AWSServiceRoleForRDS':'rds.amazonaws.com'}
    name=arg('--role-name');output({'Role':{'Arn':f'arn:aws:iam::123456789012:role/aws-service-role/{names[name]}/{name}'}})
if operation=='describe-availability-zones':output({'AvailabilityZones':[{'ZoneName':'us-west-2'+z,'State':'available','OptInStatus':'opt-in-not-required'} for z in 'ab']})
if operation=='describe-instance-type-offerings':output({'InstanceTypeOfferings':[{'InstanceType':'t4g.large','Location':'us-west-2'+z} for z in 'ab']})
if operation=='describe-cluster-versions':output({'clusterVersions':[{'clusterVersion':'1.36','versionStatus':'EXTENDED_SUPPORT' if scenario=='extended-support' else 'STANDARD_SUPPORT'}]})
if service=='ssm':output({'Parameter':{'Value':json.dumps({'release_version':'1.36.4-20261007','image_id':'ami-0123456789abcdef0'})}})
if operation=='describe-images':output({'Images':[{'ImageId':'ami-0123456789abcdef0','State':'available','Architecture':'arm64','ImageOwnerAlias':'amazon'}]})
if operation=='describe-addon-versions':
    name=arg('--addon-name');versions={'vpc-cni':'v1.21.1-eksbuild.1','kube-proxy':'v1.36.4-eksbuild.1','coredns':'v1.13.1-eksbuild.1','eks-pod-identity-agent':'v1.3.8-eksbuild.1','aws-ebs-csi-driver':'v1.48.0-eksbuild.1'}
    output({'addons':[{'addonName':name,'addonVersions':[{'addonVersion':versions[name],'architecture':['amd64'] if scenario=='wrong-architecture' else ['arm64'],'compatibilities':[{'clusterVersion':'1.36'}]}]}]})
if operation=='describe-db-engine-versions':
    if scenario=='rds-marker' and not token():output({'DBEngineVersions':[],'Marker':'second-page'})
    if token():assert 'Marker' in json.loads(arg('--cli-input-json'))
    output({'DBEngineVersions':[{'Engine':'postgres','EngineVersion':'16.13','DBParameterGroupFamily':'postgres16','Status':'available'}]})
if operation=='describe-orderable-db-instance-options':output({'OrderableDBInstanceOptions':[{'EngineVersion':'16.13','DBInstanceClass':'db.t4g.micro','Vpc':True,'StorageType':'gp3','SupportsStorageEncryption':True,'AvailabilityZones':[{'Name':'us-west-2'+z} for z in 'ab']}]})
if operation=='get-service-quota':
    limits={'L-1216C47A':64,'L-1194D53C':10,'L-F678F1CE':5,'L-0263D0A3':5,'L-FE5A380F':5}
    value=limits[arg('--quota-code')]
    if scenario in ['quota-exhausted','reserved-capacity','scheduled-capacity'] and arg('--quota-code')=='L-1216C47A':value=4
    output({'Quota':{'ServiceCode':arg('--service-code'),'QuotaCode':arg('--quota-code'),'Value':value}})
if operation=='describe-instances':
    v={'Instances':[{'InstanceType':'t4g.large','State':{'Name':'running'},'Placement':{'Tenancy':'default'}}]}
    if scenario=='repeated-token':output({'Reservations':[v],'NextToken':'same-again'})
    if scenario=='paginated-usage' and not token():output({'Reservations':[v],'NextToken':'page2'})
    output({'Reservations':[v]})
if operation=='describe-capacity-reservations':
    if scenario=='scheduled-capacity':output({'CapacityReservations':[{'OwnerId':'123456789012','State':'scheduled','InstanceType':'t4g.large','TotalInstanceCount':2}]})
    output({'CapacityReservations':[{'OwnerId':'123456789012','State':'active','InstanceType':'t4g.large','AvailableInstanceCount':2,'TotalInstanceCount':2}] if scenario=='reserved-capacity' else []})
if operation=='describe-instance-types':output({'InstanceTypes':[{'InstanceType':'t4g.large','VCpuInfo':{'DefaultVCpus':2}}]})
if operation=='list-clusters':output({'clusters':[]})
if operation=='describe-vpcs':output({'Vpcs':[{'VpcId':'vpc-existing'}]})
if operation=='describe-addresses':output({'Addresses':[]})
if operation=='describe-nat-gateways':output({'NatGateways':[]})
if operation=='describe-account-attributes':output({'AccountQuotas':[{'AccountQuotaName':'DBInstances','Max':40,'Used':1}]})
if service=='pricing':
    assert arg('--region')=='us-east-1'
    if scenario=='pricing-denied':fail()
    filters={v['Field']:v['Value'] for v in json.loads(arg('--filters'))}; code=arg('--service-code')
    attrs={'regionCode':'us-west-2','usagetype':'USW2-BoxUsage:t4g.large'};unit='Hrs';rate='0.0672';sku='node'
    if code=='AmazonEKS':unit='Hours';attrs['usagetype']='USW2-AmazonEKS-Hours:perCluster';rate='10.0' if scenario=='over-budget' else '0.1';sku='cluster'
    elif code=='AmazonVPC':attrs['usagetype']='USW2-PublicIPv4:InUseAddress';rate='0.005';sku='ipv4'
    elif code=='AmazonRDS':
        if filters.get('productFamily')=='Database Storage':attrs['volumeType']='General Purpose-GP3';rate='0.115';unit='GB-Mo';sku='rds-storage'
        else:rate='0.018';sku='rds';attrs['usagetype']='USW2-InstanceUsage:db.t4g.micro'
    elif filters.get('productFamily')=='NAT Gateway':rate='0.045';sku='nat';attrs['usagetype']='USW2-NatGateway-Hours'
    elif filters.get('productFamily')=='Storage':rate='0.08';unit='GB-Mo';sku='ebs'
    if scenario=='zero-price':rate='0'
    if scenario=='nan-price':rate='NaN'
    product={'product':{'sku':sku,'attributes':attrs},'terms':{'OnDemand':{'current':{'priceDimensions':{'dimension':{'unit':unit,'beginRange':'0','endRange':'Inf','pricePerUnit':{'USD':rate}}}}}}}
    rows=[json.dumps(product)]
    if scenario=='ambiguous-price':rows.append(json.dumps(product))
    # Real catalogs include unused reservation / regional NAT products too.
    if sku in ['rds','nat']:
        alternate=json.loads(json.dumps(product));alternate['product']['sku']='alternate'
        alternate['product']['attributes']['usagetype']='USW2-UnusedBox:db.t4g.micro' if sku=='rds' else 'USW2-RegionalNatGateway-Hours'
        rows.append(json.dumps(alternate))
    output({'PriceList':rows})
if service=='s3api':
    assert arg('--expected-bucket-owner')=='123456789012'
    if operation=='get-bucket-location':output({'LocationConstraint':'us-east-1' if scenario=='guard-wrong-region' else 'us-west-2'})
    version=arg('--version-id');body=b'guard-code-fixture'
    if scenario=='guard-too-large' and operation=='head-object':output({'VersionId':version,'ContentLength':99*1024*1024})
    if operation=='head-object':output({'VersionId':version,'ContentLength':len(body)})
    if scenario=='guard-tampered':body=b'x'*len(body)
    assert arg('--range')==f'bytes=0-{len(body)-1}'
    # Positional output immediately follows --range value, before global options.
    filename=args[args.index('--range')+2];pathlib.Path(filename).write_bytes(body)
    output({'VersionId':version,'ContentLength':len(body)})
if service=='accessanalyzer':
    findings=[]
    if scenario=='policy-warning':findings=[{'findingType':'SECURITY_WARNING','issueCode':'PASS_ROLE_WITH_STAR_IN_RESOURCE','findingDetails':'secret-sentinel-do-not-persist'}]
    output({'findings':findings})
fail('UNKNOWN_FAKE_OPERATION')
