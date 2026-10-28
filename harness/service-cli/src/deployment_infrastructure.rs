//! Schema-3 offline AWS infrastructure export. No provider calls or execution.
//! Roles are owned by bootstrap; the infrastructure executor cannot mutate IAM.
use super::*;
#[path = "deployment_aws_permissions.rs"]
mod permissions;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Inputs {
    pub schema_version: u16,
    pub availability_zones: [String; 2],
    pub vpc_cidr: String,
    pub kubernetes_version: String,
    pub node_release_version: String,
    pub postgres_version: String,
    pub addons: Addons,
    pub guard_code: GuardCode,
    pub acknowledge_experiment_data_deletion: bool,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Addons {
    pub vpc_cni: String,
    pub kube_proxy: String,
    pub core_dns: String,
    pub pod_identity: String,
    pub ebs_csi: String,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct GuardCode {
    pub bucket: String,
    pub key: String,
    pub version_id: String,
    pub sha256: String,
}
fn digits(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit())
}
pub(super) fn addon_version(s: &str) -> bool {
    let Some((core, build)) = s.strip_prefix('v').and_then(|v| v.split_once("-eksbuild.")) else {
        return false;
    };
    let parts: Vec<_> = core.split('.').collect();
    parts.len() == 3 && parts.iter().all(|p| digits(p, 1, 4)) && digits(build, 1, 4)
}

impl Inputs {
    pub(super) fn validate(&self, p: &Plan) -> Result<()> {
        let Target::Aws { region, .. } = &p.target else {
            bail!("AWS infrastructure inputs require AWS target")
        };
        ensure!(
            self.schema_version == 1,
            "Unsupported infrastructure input schema"
        );
        ensure!(
            self.acknowledge_experiment_data_deletion,
            "Explicit experiment data-deletion acknowledgement required"
        );
        ensure!(
            self.availability_zones[0] != self.availability_zones[1],
            "Two distinct availability zones required"
        );
        for zone in &self.availability_zones {
            ensure!(
                zone.strip_prefix(region)
                    .map(|s| s.len() == 1 && s.as_bytes()[0].is_ascii_lowercase())
                    .unwrap_or(false),
                "Availability zone must belong to the target region"
            );
        }
        let parts: Vec<_> = self.kubernetes_version.split('.').collect();
        ensure!(
            parts.len() == 2
                && parts[0] == "1"
                && parts[1].len() == 2
                && parts[1].bytes().all(|b| b.is_ascii_digit()),
            "Explicit Kubernetes major.minor required"
        );
        let release = self
            .node_release_version
            .strip_prefix(&format!("{}.", self.kubernetes_version))
            .and_then(|s| s.split_once('-'));
        ensure!(
            release
                .map(|(patch, date)| digits(patch, 1, 3) && digits(date, 8, 8))
                .unwrap_or(false),
            "Pin an EKS ARM64 AL2023 release version matching Kubernetes"
        );
        ensure!(
            self.postgres_version.starts_with("16.")
                && self.postgres_version.len() <= 8
                && self.postgres_version[3..]
                    .bytes()
                    .all(|b| b.is_ascii_digit())
                && self.postgres_version.len() > 3,
            "Pin PostgreSQL 16 minor version"
        );
        for pin in [
            &self.addons.vpc_cni,
            &self.addons.kube_proxy,
            &self.addons.core_dns,
            &self.addons.pod_identity,
            &self.addons.ebs_csi,
        ] {
            ensure!(
                addon_version(pin),
                "Pin every add-on to an explicit eksbuild version"
            );
        }
        self.network_prefix()?;
        let code = &self.guard_code;
        ensure!(
            (3..=63).contains(&code.bucket.len())
                && code.bucket.as_bytes()[0].is_ascii_lowercase()
                && code
                    .bucket
                    .as_bytes()
                    .last()
                    .unwrap()
                    .is_ascii_alphanumeric()
                && code
                    .bucket
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
            "Guard bucket must be a DNS-style name without dots"
        );
        ensure!(
            !code.key.is_empty()
                && code.key.len() <= 1024
                && !code.key.chars().any(char::is_control),
            "Invalid guard artifact key"
        );
        ensure!(
            !code.version_id.is_empty()
                && code.version_id != "null"
                && code.version_id.len() <= 1024
                && !code.version_id.chars().any(char::is_control),
            "Guard artifact requires immutable S3 version ID"
        );
        ensure!(
            code.sha256.len() == 64
                && code
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()),
            "Guard artifact requires lowercase SHA-256 digest"
        );
        Ok(())
    }
    fn network_prefix(&self) -> Result<String> {
        let address = self
            .vpc_cidr
            .strip_suffix("/16")
            .context("Use an explicit private IPv4 /16 for the experiment")?;
        let ip: std::net::Ipv4Addr = address.parse().context("Invalid VPC CIDR")?;
        let [a, b, c, d] = ip.octets();
        ensure!(
            ip.is_private() && c == 0 && d == 0 && address == ip.to_string(),
            "VPC must be a canonical RFC1918 /16 network"
        );
        Ok(format!("{a}.{b}"))
    }
}
fn reference(id: &str) -> Value {
    json!({"Ref":id})
}
fn attribute(id: &str, key: &str) -> Value {
    json!({"Fn::GetAtt":[id,key]})
}
fn policy(statements: Value) -> Value {
    json!({"Version":"2012-10-17","Statement":statements})
}
fn service_trust(service: &str) -> Value {
    policy(json!([{"Effect":"Allow","Principal":{"Service":service},"Action":"sts:AssumeRole"}]))
}
fn tags(p: &Plan) -> Value {
    json!([{"Key":"ows:deployment-id","Value":p.deployment_id},{"Key":"ows:expires-at","Value":p.expires_at_unix.to_string()}])
}
fn scope(p: &Plan) -> (&str, &str) {
    match &p.target {
        Target::Aws { account, region } => (account, region),
        _ => unreachable!("validated AWS target"),
    }
}
fn role_arn(p: &Plan, suffix: &str) -> String {
    let (a, _) = scope(p);
    format!("arn:aws:iam::{a}:role/{}-{suffix}", p.prefix())
}
fn bucket(p: &Plan) -> String {
    format!("{}-{}", p.prefix(), scope(p).0)
}
fn resource(kind: &str, props: Value) -> Value {
    json!({"Type":kind,"Properties":props})
}
fn template(description: &str, resources: BTreeMap<String, Value>, outputs: Value) -> Value {
    json!({"AWSTemplateFormatVersion":"2010-09-09","Description":description,"Metadata":{"OpenWorker":{"qualification":"offline-export-only","liveApplyEnabled":false}},"Resources":resources,"Outputs":outputs})
}

fn infrastructure_template(p: &Plan, i: &Inputs) -> Result<Value> {
    let prefix = p.prefix();
    let (account, region) = scope(p);
    let net = i.network_prefix()?;
    let mut r = BTreeMap::new();
    let mut add = |name: &str, kind: &str, props: Value| {
        r.insert(name.to_owned(), resource(kind, props));
    };
    add(
        "Vpc",
        "AWS::EC2::VPC",
        json!({"CidrBlock":i.vpc_cidr,"EnableDnsSupport":true,"EnableDnsHostnames":true,"Tags":tags(p)}),
    );
    add(
        "InternetGateway",
        "AWS::EC2::InternetGateway",
        json!({"Tags":tags(p)}),
    );
    add(
        "GatewayAttachment",
        "AWS::EC2::VPCGatewayAttachment",
        json!({"VpcId":reference("Vpc"),"InternetGatewayId":reference("InternetGateway")}),
    );
    add(
        "PublicSubnet",
        "AWS::EC2::Subnet",
        json!({"VpcId":reference("Vpc"),"CidrBlock":format!("{net}.0.0/24"),"AvailabilityZone":i.availability_zones[0],"MapPublicIpOnLaunch":false,"Tags":tags(p)}),
    );
    add(
        "PublicRoutes",
        "AWS::EC2::RouteTable",
        json!({"VpcId":reference("Vpc"),"Tags":tags(p)}),
    );
    add(
        "PublicRouteAssociation",
        "AWS::EC2::SubnetRouteTableAssociation",
        json!({"SubnetId":reference("PublicSubnet"),"RouteTableId":reference("PublicRoutes")}),
    );
    add(
        "InternetRoute",
        "AWS::EC2::Route",
        json!({"RouteTableId":reference("PublicRoutes"),"DestinationCidrBlock":"0.0.0.0/0","GatewayId":reference("InternetGateway")}),
    );
    add(
        "NatAddress",
        "AWS::EC2::EIP",
        json!({"Domain":"vpc","Tags":tags(p)}),
    );
    add(
        "Nat",
        "AWS::EC2::NatGateway",
        json!({"SubnetId":reference("PublicSubnet"),"AllocationId":attribute("NatAddress","AllocationId"),"ConnectivityType":"public","Tags":tags(p)}),
    );
    add(
        "PrivateRoutes",
        "AWS::EC2::RouteTable",
        json!({"VpcId":reference("Vpc"),"Tags":tags(p)}),
    );
    add(
        "NatRoute",
        "AWS::EC2::Route",
        json!({"RouteTableId":reference("PrivateRoutes"),"DestinationCidrBlock":"0.0.0.0/0","NatGatewayId":reference("Nat")}),
    );
    for n in 0..2 {
        let name = format!("PrivateSubnet{n}");
        add(
            &name,
            "AWS::EC2::Subnet",
            json!({"VpcId":reference("Vpc"),"CidrBlock":format!("{net}.{}.0/24",10+n),"AvailabilityZone":i.availability_zones[n],"MapPublicIpOnLaunch":false,"Tags":tags(p)}),
        );
        add(
            &format!("PrivateRouteAssociation{n}"),
            "AWS::EC2::SubnetRouteTableAssociation",
            json!({"SubnetId":reference(&name),"RouteTableId":reference("PrivateRoutes")}),
        );
    }
    add(
        "Cluster",
        "AWS::EKS::Cluster",
        json!({"Name":prefix,"Version":i.kubernetes_version,"RoleArn":role_arn(p,"cluster"),"BootstrapSelfManagedAddons":false,
        "AccessConfig":{"AuthenticationMode":"API","BootstrapClusterCreatorAdminPermissions":false},"UpgradePolicy":{"SupportType":"STANDARD"},
        "ResourcesVpcConfig":{"SubnetIds":[reference("PrivateSubnet0"),reference("PrivateSubnet1")],"EndpointPrivateAccess":true,"EndpointPublicAccess":false},"Tags":tags(p)}),
    );
    add(
        "PlatformAccess",
        "AWS::EKS::AccessEntry",
        json!({"ClusterName":reference("Cluster"),"PrincipalArn":role_arn(p,"setup"),"Type":"STANDARD",
        "AccessPolicies":[{"PolicyArn":"arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy","AccessScope":{"Type":"cluster"}}],"Tags":tags(p)}),
    );
    add(
        "LaunchTemplate",
        "AWS::EC2::LaunchTemplate",
        json!({"LaunchTemplateName":prefix,"TagSpecifications":[{"ResourceType":"launch-template","Tags":tags(p)}],
        "LaunchTemplateData":{"MetadataOptions":{"HttpTokens":"required","HttpPutResponseHopLimit":1,"HttpEndpoint":"enabled"},"BlockDeviceMappings":[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}],"TagSpecifications":[{"ResourceType":"instance","Tags":tags(p)},{"ResourceType":"volume","Tags":tags(p)}]}}),
    );
    add(
        "Nodes",
        "AWS::EKS::Nodegroup",
        json!({"ClusterName":reference("Cluster"),"NodegroupName":"workers","NodeRole":role_arn(p,"node"),"Subnets":[reference("PrivateSubnet0"),reference("PrivateSubnet1")],"AmiType":"AL2023_ARM_64_STANDARD","ReleaseVersion":i.node_release_version,"Version":i.kubernetes_version,"InstanceTypes":["t4g.large"],"CapacityType":"ON_DEMAND",
        "ScalingConfig":{"MinSize":2,"MaxSize":2,"DesiredSize":2},"LaunchTemplate":{"Id":reference("LaunchTemplate"),"Version":attribute("LaunchTemplate","LatestVersionNumber")},"Tags":{"ows:deployment-id":p.deployment_id,"ows:expires-at":p.expires_at_unix.to_string()}}),
    );
    for (logical, name, version) in [
        ("Cni", "vpc-cni", &i.addons.vpc_cni),
        ("KubeProxy", "kube-proxy", &i.addons.kube_proxy),
        ("CoreDns", "coredns", &i.addons.core_dns),
        (
            "PodIdentity",
            "eks-pod-identity-agent",
            &i.addons.pod_identity,
        ),
        ("Ebs", "aws-ebs-csi-driver", &i.addons.ebs_csi),
    ] {
        add(
            logical,
            "AWS::EKS::Addon",
            json!({"ClusterName":reference("Cluster"),"AddonName":name,"AddonVersion":version,"Tags":tags(p)}),
        );
    }
    add(
        "DatabaseSecurityGroup",
        "AWS::EC2::SecurityGroup",
        json!({"GroupDescription":"PostgreSQL from this experiment cluster only","VpcId":reference("Vpc"),"SecurityGroupIngress":[{"IpProtocol":"tcp","FromPort":5432,"ToPort":5432,"SourceSecurityGroupId":attribute("Cluster","ClusterSecurityGroupId")}],"Tags":tags(p)}),
    );
    add(
        "DatabaseSubnets",
        "AWS::RDS::DBSubnetGroup",
        json!({"DBSubnetGroupName":prefix,"DBSubnetGroupDescription":"Private experiment database","SubnetIds":[reference("PrivateSubnet0"),reference("PrivateSubnet1")],"Tags":tags(p)}),
    );
    add(
        "DatabaseParameters",
        "AWS::RDS::DBParameterGroup",
        json!({"DBParameterGroupName":prefix,"Family":"postgres16","Description":"TLS required","Parameters":{"rds.force_ssl":"1"},"Tags":tags(p)}),
    );
    add(
        "DatabaseSecret",
        "AWS::SecretsManager::Secret",
        json!({"Name":format!("{prefix}-database"),"Description":"Experiment database bootstrap credential; no model keys","GenerateSecretString":{"SecretStringTemplate":"{\"username\":\"openworker\"}","GenerateStringKey":"password","PasswordLength":32,"ExcludePunctuation":true},"Tags":tags(p)}),
    );
    add(
        "Database",
        "AWS::RDS::DBInstance",
        json!({"DBInstanceIdentifier":prefix,"Engine":"postgres","EngineVersion":i.postgres_version,"DBInstanceClass":"db.t4g.micro","AllocatedStorage":"20","StorageType":"gp3","StorageEncrypted":true,"DBName":"openworker","MasterUsername":"openworker","MasterUserPassword":{"Fn::Sub":"{{resolve:secretsmanager:${DatabaseSecret}:SecretString:password}}"},"DBSubnetGroupName":reference("DatabaseSubnets"),"DBParameterGroupName":reference("DatabaseParameters"),"VPCSecurityGroups":[reference("DatabaseSecurityGroup")],"PubliclyAccessible":false,"MultiAZ":false,"BackupRetentionPeriod":0,"DeleteAutomatedBackups":true,"DeletionProtection":false,"AutoMinorVersionUpgrade":false,"Tags":tags(p)}),
    );
    add(
        "DatabaseSecretTarget",
        "AWS::SecretsManager::SecretTargetAttachment",
        json!({"SecretId":reference("DatabaseSecret"),"TargetId":reference("Database"),"TargetType":"AWS::RDS::DBInstance"}),
    );
    add(
        "Artifacts",
        "AWS::S3::Bucket",
        json!({"BucketName":bucket(p),"PublicAccessBlockConfiguration":{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true},"BucketEncryption":{"ServerSideEncryptionConfiguration":[{"ServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]},"OwnershipControls":{"Rules":[{"ObjectOwnership":"BucketOwnerEnforced"}]},"LifecycleConfiguration":{"Rules":[{"Id":"abort-multipart","Status":"Enabled","AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]},"Tags":tags(p)}),
    );
    add(
        "ArtifactTls",
        "AWS::S3::BucketPolicy",
        json!({"Bucket":reference("Artifacts"),"PolicyDocument":policy(json!([{"Effect":"Deny","Principal":"*","Action":"s3:*","Resource":[format!("arn:aws:s3:::{}",bucket(p)),format!("arn:aws:s3:::{}/*",bucket(p))],"Condition":{"Bool":{"aws:SecureTransport":"false"}}}]))}),
    );
    add(
        "ArtifactIdentity",
        "AWS::EKS::PodIdentityAssociation",
        json!({"ClusterName":reference("Cluster"),"Namespace":"ow-service","ServiceAccount":"ows-artifacts","RoleArn":role_arn(p,"artifacts"),"Tags":tags(p)}),
    );
    for (id, suffix) in [
        ("HarnessImage", "harness"),
        ("RunnerImage", "runner"),
        ("UiImage", "service-ui"),
    ] {
        add(
            id,
            "AWS::ECR::Repository",
            json!({"RepositoryName":format!("{prefix}/{suffix}"),"EmptyOnDelete":true,"ImageTagMutability":"IMMUTABLE","ImageScanningConfiguration":{"ScanOnPush":true},"EncryptionConfiguration":{"EncryptionType":"AES256"},"Tags":tags(p)}),
        );
    }
    // Bootstrap CNI uses a node role initially. No untrusted pods may be admitted
    // until platform policies are installed and strict enforcement is verified.
    r.get_mut("Cni").unwrap()["Properties"]["ConfigurationValues"] = json!(serde_json::to_string(
        &json!({"enableNetworkPolicy":"true","env":{"NETWORK_POLICY_ENFORCING_MODE":"standard"}})
    )?);
    r.get_mut("Ebs").unwrap()["Properties"]["PodIdentityAssociations"] =
        json!([{"ServiceAccount":"ebs-csi-controller-sa","RoleArn":role_arn(p,"ebs")}]);
    for (id, deps) in [
        ("InternetRoute", json!(["GatewayAttachment"])),
        ("Nat", json!(["GatewayAttachment", "InternetRoute"])),
        (
            "Nodes",
            json!([
                "Cni",
                "KubeProxy",
                "NatRoute",
                "PrivateRouteAssociation0",
                "PrivateRouteAssociation1"
            ]),
        ),
        ("CoreDns", json!(["Nodes"])),
        ("PodIdentity", json!(["Nodes"])),
        ("Ebs", json!(["PodIdentity"])),
        ("ArtifactIdentity", json!(["PodIdentity"])),
    ] {
        r.get_mut(id).unwrap()["DependsOn"] = deps;
    }
    // The experiment explicitly opts out of retaining DB snapshots on replacement.
    for id in ["Database", "Artifacts"] {
        let v = r.get_mut(id).unwrap();
        v["DeletionPolicy"] = json!("Delete");
        v["UpdateReplacePolicy"] = json!("Delete");
    }
    Ok(template(
        "Disposable OpenWorker private EKS infrastructure; offline export, qualification required",
        r,
        json!({
            "Cluster":{"Value":reference("Cluster")},"Vpc":{"Value":reference("Vpc")},"PrivateSubnet0":{"Value":reference("PrivateSubnet0")},"PrivateSubnet1":{"Value":reference("PrivateSubnet1")},
            "DatabaseEndpoint":{"Value":attribute("Database","Endpoint.Address")},"DatabaseSecretArn":{"Value":reference("DatabaseSecret")},"ArtifactBucket":{"Value":reference("Artifacts")},"ArtifactPrefix":{"Value":format!("{}/",p.deployment_id)},
            "HarnessRepository":{"Value":attribute("HarnessImage","RepositoryUri")},"RunnerRepository":{"Value":attribute("RunnerImage","RepositoryUri")},"UiRepository":{"Value":attribute("UiImage","RepositoryUri")},
            "ExpectedClusterArn":{"Value":format!("arn:aws:eks:{region}:{account}:cluster/{prefix}")}
        }),
    ))
}

fn guard_template(p: &Plan, i: &Inputs) -> Value {
    let prefix = p.prefix();
    let (account, region) = scope(p);
    let mut r = BTreeMap::from([
        (
            "Control".into(),
            resource(
                "AWS::DynamoDB::Table",
                json!({"TableName":format!("{prefix}-control"),"BillingMode":"PAY_PER_REQUEST","AttributeDefinitions":[{"AttributeName":"deploymentId","AttributeType":"S"}],"KeySchema":[{"AttributeName":"deploymentId","KeyType":"HASH"}],"Tags":tags(p)}),
            ),
        ),
        (
            "Logs".into(),
            resource(
                "AWS::Logs::LogGroup",
                json!({"LogGroupName":format!("/aws/lambda/{prefix}-cleanup"),"RetentionInDays":7,"Tags":tags(p)}),
            ),
        ),
        (
            "Cleanup".into(),
            resource(
                "AWS::Lambda::Function",
                json!({"FunctionName":format!("{prefix}-cleanup"),"Runtime":"nodejs22.x","Handler":"index.handler","Role":role_arn(p,"cleanup"),"Timeout":120,"MemorySize":256,"ReservedConcurrentExecutions":1,
            "Code":{"S3Bucket":i.guard_code.bucket,"S3Key":i.guard_code.key,"S3ObjectVersion":i.guard_code.version_id},"Environment":{"Variables":{"OWS_DEPLOYMENT_ID":p.deployment_id,"OWS_ACCOUNT":account,"OWS_REGION":region,"OWS_STACK_NAME":format!("{prefix}-infra"),"OWS_EXPIRY":p.expires_at_unix.to_string(),"OWS_BUCKET":bucket(p),"OWS_OBJECT_PREFIX":format!("{}/",p.deployment_id),"OWS_EXECUTION_ROLE":role_arn(p,"executor")}},"Tags":tags(p)}),
            ),
        ),
        (
            "Schedule".into(),
            resource(
                "AWS::Events::Rule",
                json!({"Name":format!("{prefix}-cleanup"),"ScheduleExpression":"rate(5 minutes)","State":"ENABLED","Targets":[{"Id":"cleanup","Arn":attribute("Cleanup","Arn"),"RetryPolicy":{"MaximumRetryAttempts":0,"MaximumEventAgeInSeconds":300}}]}),
            ),
        ),
        (
            "Invoke".into(),
            resource(
                "AWS::Lambda::Permission",
                json!({"FunctionName":reference("Cleanup"),"Action":"lambda:InvokeFunction","Principal":"events.amazonaws.com","SourceArn":attribute("Schedule","Arn"),"SourceAccount":account}),
            ),
        ),
    ]);
    r.get_mut("Cleanup").unwrap()["DependsOn"] = json!(["Logs", "Control"]);
    template(
        "OpenWorker independent expiry guard; artifact and cleanup behavior require qualification",
        r,
        json!({"Function":{"Value":reference("Cleanup")}}),
    )
}

pub(super) fn render(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let i = p
        .infrastructure
        .as_ref()
        .context("Infrastructure inputs missing")?;
    i.validate(p)?;
    let mut files = bootstrap::render(p)?;
    let infra = infrastructure_template(p, i)?;
    let guard = guard_template(p, i);
    let policies = permissions::generate(p, i, &infra, &guard)?;
    let mut boot: Value = serde_json::from_slice(&files["bootstrap.cfn.json"])?;
    let (account, region) = scope(p);
    let mut setup: Value = serde_json::from_slice(&files["setup-policy.json"])?;
    // Schema-3 lifecycle creates immutable stacks. Do not inherit the legacy
    // import path's update grant, especially for the independent expiry guard.
    for statement in setup["Statement"].as_array_mut().unwrap() {
        if let Some(actions) = statement["Action"].as_array_mut() {
            actions.retain(|v| v != "cloudformation:UpdateStack");
        }
    }
    setup["Statement"].as_array_mut().unwrap().extend([
        json!({"Sid":"VerifyOwnGuard","Effect":"Allow","Action":["lambda:GetFunctionConfiguration","lambda:GetFunctionConcurrency","lambda:InvokeFunction"],"Resource":format!("arn:aws:lambda:{region}:{account}:function:{}-cleanup",p.prefix())}),
        json!({"Sid":"VerifyOwnGuardSchedule","Effect":"Allow","Action":["events:DescribeRule","events:ListTargetsByRule"],"Resource":format!("arn:aws:events:{region}:{account}:rule/{}-cleanup",p.prefix())}),
        json!({"Sid":"CheckOwnBucketBeforeTeardown","Effect":"Allow","Action":["s3:GetBucketTagging","s3:GetBucketVersioning","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":format!("arn:aws:s3:::{}-{account}",p.prefix())}),
        json!({"Sid":"ReadOwnDatabaseBootstrap","Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":format!("arn:aws:secretsmanager:{region}:{account}:secret:{}-database-*",p.prefix())}),
        json!({"Sid":"ObserveOwnCni","Effect":"Allow","Action":["eks:DescribeAddon"],"Resource":format!("arn:aws:eks:{region}:{account}:addon/{}/vpc-cni/*",p.prefix())}),
        json!({"Sid":"ConfigureOwnCniBeforeExpiry","Effect":"Allow","Action":["eks:UpdateAddon"],"Resource":format!("arn:aws:eks:{region}:{account}:addon/{}/vpc-cni/*",p.prefix()),"Condition":{"NumericLessThan":{"aws:EpochTime":p.expires_at_unix}}}),
        json!({"Sid":"RegistryLogin","Effect":"Allow","Action":["ecr:GetAuthorizationToken"],"Resource":"*","Condition":{"StringEquals":{"aws:RequestedRegion":region},"NumericLessThan":{"aws:EpochTime":p.expires_at_unix}}}),
        json!({"Sid":"PublishOwnImages","Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:GetDownloadUrlForLayer","ecr:BatchGetImage","ecr:DescribeRepositories","ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage"],"Resource":(["harness","runner","service-ui"].iter().map(|s|format!("arn:aws:ecr:{region}:{account}:repository/{}/{s}",p.prefix())).collect::<Vec<_>>()),"Condition":{"NumericLessThan":{"aws:EpochTime":p.expires_at_unix}}}),
    ]);
    boot["Resources"]["SetupRole"]["Properties"]["Policies"][0]["PolicyDocument"] = setup.clone();
    files.insert("setup-policy.json".into(), json_bytes(&setup)?);
    let resources = boot["Resources"].as_object_mut().unwrap();
    let mut policy_refs = vec![];
    for (domain, doc) in &policies.execution {
        let id = format!("ExecutionPolicy{domain}");
        ensure!(
            serde_json::to_vec(doc)?.len() <= 6144,
            "Execution policy exceeds IAM managed-policy size: {domain}"
        );
        resources.insert(id.clone(),resource("AWS::IAM::ManagedPolicy",json!({"ManagedPolicyName":format!("{}-exec-{}",p.prefix(),domain.to_lowercase()),"PolicyDocument":doc})));
        policy_refs.push(reference(&id));
        files.insert(
            format!("execution-policy-{}.json", domain.to_lowercase()),
            json_bytes(doc)?,
        );
    }
    resources.insert("ExecutionRole".into(),resource("AWS::IAM::Role",json!({"RoleName":format!("{}-executor",p.prefix()),"MaxSessionDuration":3600,"AssumeRolePolicyDocument":service_trust("cloudformation.amazonaws.com"),"ManagedPolicyArns":policy_refs,"Tags":tags(p)})));
    for (id, suffix, service, managed) in [
        (
            "ClusterRole",
            "cluster",
            "eks.amazonaws.com",
            vec!["AmazonEKSClusterPolicy"],
        ),
        (
            "NodeRole",
            "node",
            "ec2.amazonaws.com",
            vec![
                "AmazonEKSWorkerNodePolicy",
                "AmazonEC2ContainerRegistryPullOnly",
                "AmazonEKS_CNI_Policy",
            ],
        ),
    ] {
        resources.insert(id.into(),resource("AWS::IAM::Role",json!({"RoleName":format!("{}-{suffix}",p.prefix()),"AssumeRolePolicyDocument":service_trust(service),"ManagedPolicyArns":managed.iter().map(|s|format!("arn:aws:iam::aws:policy/{s}")).collect::<Vec<_>>(),"Tags":tags(p)})));
    }
    let mut ebs_trust = resources["ArtifactRole"]["Properties"]["AssumeRolePolicyDocument"].clone();
    ebs_trust["Statement"][0]["Condition"]["StringEquals"]["aws:RequestTag/kubernetes-namespace"] =
        json!("kube-system");
    ebs_trust["Statement"][0]["Condition"]["StringEquals"]
        ["aws:RequestTag/kubernetes-service-account"] = json!("ebs-csi-controller-sa");
    resources.insert("EbsRole".into(),resource("AWS::IAM::Role",json!({"RoleName":format!("{}-ebs",p.prefix()),"AssumeRolePolicyDocument":ebs_trust,"ManagedPolicyArns":["arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"],"Tags":tags(p)})));
    resources.insert("CleanupRole".into(),resource("AWS::IAM::Role",json!({"RoleName":format!("{}-cleanup",p.prefix()),"AssumeRolePolicyDocument":service_trust("lambda.amazonaws.com"),"Policies":[{"PolicyName":"owned-cleanup","PolicyDocument":policies.cleanup}],"Tags":tags(p)})));
    boot["Description"]=json!("OpenWorker generated deployment identities only; no cloud infrastructure provisioned by bootstrap");
    boot["Outputs"]["ExecutionRoleArn"] = json!({"Value":attribute("ExecutionRole","Arn")});
    // Role existence checks apply only to imported executors. The generated role
    // does not exist until this identity stack is created by the customer.
    let script = String::from_utf8(files["bootstrap.sh"].clone())?;
    let script = script
        .lines()
        .filter(|line| !line.starts_with("executor=$(") && !line.starts_with("[ \"$executor\" ="))
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";
    files.insert("bootstrap.sh".into(), script.into_bytes());
    files.insert("bootstrap.cfn.json".into(), json_bytes(&boot)?);
    files.insert("infrastructure.cfn.json".into(), json_bytes(&infra)?);
    files.insert("guard.cfn.json".into(), json_bytes(&guard)?);
    files.insert("cleanup-policy.json".into(), json_bytes(&policies.cleanup)?);
    files.insert(
        "permission-review.json".into(),
        json_bytes(&policies.review)?,
    );
    files.insert("infrastructure-inputs.json".into(), json_bytes(i)?);
    files.insert("guard-contract.json".into(),json_bytes(&json!({"schemaVersion":1,"qualified":false,"code":i.guard_code,"requiredBeforeInfrastructure":["verify versioned S3 object SHA-256","verify Lambda account/region/deployment identity","invoke read-only health probe","prove rejection of foreign stack, bucket and volume ownership","prove expiry cleanup and lost-response reconciliation"],"requiredBehavior":["no destructive effects before fixed expiry","resolve exact infra stack ARN and verify deployment tag before deletion","never update or delete the bootstrap stack or guard stack","delete only deployment-prefix objects and tagged available volumes","refuse foreign objects/versioned bucket and fail closed on ambiguous ownership","use exact execution role and stable deletion request identity","report incomplete teardown; retain guard until independently verified"]}))?);
    let review = review(p, i, &policies.execution);
    files.insert("BOOTSTRAP.md".into(), review.clone().into_bytes());
    files.insert("REVIEW.md".into(), review.into_bytes());
    files.insert("resource-graph.json".into(),json_bytes(&json!({"schemaVersion":2,"deploymentId":p.deployment_id,"resolved":false,"templatesGenerated":true,"cloudValidated":false,"identityResources":boot["Resources"].as_object().unwrap().keys().collect::<Vec<_>>(),"infrastructureResources":infra["Resources"].as_object().unwrap().keys().collect::<Vec<_>>(),"guardResources":guard["Resources"].as_object().unwrap().keys().collect::<Vec<_>>(),"phaseOrder":["customer identity bootstrap","guard artifact and IAM verification","guard installation and health qualification","infrastructure","private API connectivity","OpenShell platform and strict networking","service/UI and workload identity","live qualification"]}))?);
    files.insert("permissions-request.json".into(),json_bytes(&json!({"schemaVersion":2,"format":"ows.permission-requirements.v2","deploymentId":p.deployment_id,"target":p.target,"generated":true,"cloudValidated":false,"applyReady":false,"bootstrapCallerRequires":"reviewed IAM role/managed-policy creation plus CloudFormation bootstrap operations; no automatic grant","serviceLinkedRolesRequired":["AWSServiceRoleForAmazonEKS","AWSServiceRoleForAmazonEKSNodegroup","AWSServiceRoleForAutoScaling","AWSServiceRoleForRDS"],"additionalPhases":["image upload exact repository grants","secret handoff exact returned secret ARN","private Kubernetes connectivity","OpenShell cluster-wide install privileges"]}))?);
    Ok(files)
}
fn review(p: &Plan, i: &Inputs, policies: &BTreeMap<String, Value>) -> String {
    let (account, region) = scope(p);
    let prefix = p.prefix();
    format!(
        r#"# {} AWS experiment review

Deployment `{}`; account `{account}`; region `{region}`; resource prefix `{prefix}`.
Budget authorization ${}; no verified estimate and no spending cap. Fixed expiry {}.

## Generated, not provisioned

This bundle contains private EKS/RDS/network/S3/ECR and independent guard templates,
plus the identities and execution policies they reference. It has only been tested
locally. Ordinary `ows deploy apply` remains disabled; the explicit experimental
`ows deploy aws lifecycle` path requires opt-in and fresh preflight. No resources
have been provisioned to generate this bundle.
Cloud IAM behavior, supported versions, quotas, prices and guard behavior are NOT
certified by offline validation. `guard-contract.json` is a mandatory qualification
gate, not a claim that the supplied code implements that contract.

## Explicit experiment choices

Two ARM64 t4g.large workers in two private subnets; private EKS endpoint; no public
workload ingress. One NAT gateway in the first zone provides outbound access: it
costs money and is a single point of failure. This is not a production HA network.
CIDR {}; AZs {:?}. The customer must establish a private API access path; this
bundle does not create a VPN or quietly enable the public EKS endpoint.

PostgreSQL 16 is encrypted, private and TLS-required, with a generated named secret.
The customer explicitly acknowledged deletion: no backups/snapshots are retained
on experiment teardown or replacement. The experiment secret does not rotate
automatically; this choice keeps secret authority confined to an exact deployment
name. No master password or model credential value appears in the bundle.

## Identity bootstrap

Run only after review and authorization: `bash bootstrap.sh --apply-identities`.
It creates IAM identities/managed policies, not infrastructure. The caller must
already have CloudFormation bootstrap create/read and IAM role/policy lifecycle
permissions for the exact names in `bootstrap.cfn.json`. Rollback needs detach/delete
permissions; organization SCPs/boundaries still apply. It checks exact caller/account,
validates expiry/integrity and never updates/adopts an existing stack or retries an
unknown mutation. It no longer requires an existing CloudFormation execution role.

The generated executor receives {} domain policies. It cannot create/edit roles,
attach policies, fetch arbitrary secrets, or pass arbitrary roles. Exact platform
roles are created by customer bootstrap. AWS-managed cluster/node/CSI policies are
listed in the template and are a separate review boundary; their upstream content
can change. Service-linked roles must already exist or be installed by the customer
bootstrap authority after preflight, never by widening the executor to general IAM.

`permission-review.json` maps every infrastructure and guard resource to a permission
domain and explains broad grants. EC2 generated IDs need type/region/account wildcards
with request/resource ownership tags. EKS CreateCluster requires Resource *; it is
region/request-tag constrained, not an IAM-enforced cluster count/name limit. Named
EKS access-entry APIs and cluster administration are restricted to this cluster;
platform installation needs this power for OpenShell CRDs/CNI. Runtime UI/agents do
not receive it. No statement may be described as enforcing the $ budget or node count.

## Platform handoff and cleanup

After verified guard installation, the infrastructure executor can create the infra
stack. The setup role gets this cluster's platform access entry. Before admitting
untrusted agent pods, install OpenShell/network policies and qualify strict CNI mode.
The chart must use the artifact service account only for artifact-using components.
The setup policy includes exact image repositories and the generated DB bootstrap
secret; model secret reads are not granted. The experimental lifecycle/shared
installer verifies that handoff. No static cloud keys are installed in pods.

Guard code is pinned to a versioned S3 object and expected SHA-256. Its packaging,
health probe and cleanup behavior have local deterministic tests; actual Lambda
scheduling, IAM and expiry cleanup still require cloud qualification before release. The independent cleanup role may delete only the owned infra stack, inspect
ownership, empty its deployment object prefix and remove tagged available volumes.
It passes the exact executor only to CloudFormation deletion. It cannot edit roles,
provision new infrastructure or remove its own guard/bootstrap stack. Keep the guard
until teardown is verified; the customer removes identity resources last.

Neither a failed response nor a timeout proves an action did not occur. Reconcile
stable operation/resource IDs before retrying; do not generate a new identity to
retry an uncertain action. Template hashes detect changes, not malicious rewrites
of the entire bundle. No cloud execution was performed to generate this review.
"#,
        p.name,
        p.deployment_id,
        p.budget.authorized_usd,
        p.expires_at_unix,
        i.vpc_cidr,
        i.availability_zones,
        policies.len()
    )
}
