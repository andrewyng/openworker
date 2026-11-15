//! Permission domains for the emitted CloudFormation resources. Mutating IAM is
//! intentionally absent: bootstrap owns every platform and execution identity.
use super::*;
pub(super) struct Policies {
    pub execution: BTreeMap<String, Value>,
    pub cleanup: Value,
    pub review: Value,
}
fn statement(
    sid: &str,
    actions: &[&str],
    resources: Vec<String>,
    condition: Option<Value>,
) -> Value {
    let mut v = json!({"Sid":sid,"Effect":"Allow","Action":actions,"Resource":resources});
    if let Some(c) = condition {
        v["Condition"] = c;
    }
    v
}
pub(super) fn generate(p: &Plan, i: &Inputs, infra: &Value, guard: &Value) -> Result<Policies> {
    let (account, region) = scope(p);
    let prefix = p.prefix();
    let ec2 = |kind: &str| format!("arn:aws:ec2:{region}:{account}:{kind}/*");
    let regional = json!({"StringEquals":{"aws:RequestedRegion":region}});
    let owned = json!({"StringEquals":{"aws:RequestedRegion":region,"ec2:ResourceTag/ows:deployment-id":p.deployment_id}});
    let creating = json!({"StringEquals":{"aws:RequestedRegion":region,"aws:RequestTag/ows:deployment-id":p.deployment_id}});
    let mut create = vec![];
    for (sid, action, kind, parents) in [
        ("Vpc", "ec2:CreateVpc", "vpc", vec![]),
        (
            "Gateway",
            "ec2:CreateInternetGateway",
            "internet-gateway",
            vec![],
        ),
        ("Address", "ec2:AllocateAddress", "elastic-ip", vec![]),
        ("Subnet", "ec2:CreateSubnet", "subnet", vec!["vpc"]),
        ("Routes", "ec2:CreateRouteTable", "route-table", vec!["vpc"]),
        (
            "SecurityGroup",
            "ec2:CreateSecurityGroup",
            "security-group",
            vec!["vpc"],
        ),
        (
            "Nat",
            "ec2:CreateNatGateway",
            "natgateway",
            vec!["subnet", "elastic-ip", "vpc"],
        ),
        (
            "LaunchTemplate",
            "ec2:CreateLaunchTemplate",
            "launch-template",
            vec![],
        ),
    ] {
        create.push(statement(
            &format!("Create{sid}"),
            &[action],
            vec![ec2(kind)],
            Some(creating.clone()),
        ));
        if !parents.is_empty() {
            create.push(statement(
                &format!("Parents{sid}"),
                &[action],
                parents.iter().map(|k| ec2(k)).collect(),
                Some(owned.clone()),
            ));
        }
    }
    create.push(statement("CreationTags",&["ec2:CreateTags"],["vpc","internet-gateway","elastic-ip","subnet","route-table","security-group","natgateway","launch-template"].iter().map(|k|ec2(k)).collect(),Some(json!({"StringEquals":{"aws:RequestTag/ows:deployment-id":p.deployment_id,"ec2:CreateAction":["CreateVpc","CreateInternetGateway","AllocateAddress","CreateSubnet","CreateRouteTable","CreateSecurityGroup","CreateNatGateway","CreateLaunchTemplate"]}}))));
    let mut network = vec![statement(
        "NetworkObservation",
        &[
            "ec2:DescribeVpcs",
            "ec2:DescribeVpcAttribute",
            "ec2:DescribeSubnets",
            "ec2:DescribeInternetGateways",
            "ec2:DescribeRouteTables",
            "ec2:DescribeAddresses",
            "ec2:DescribeNatGateways",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeSecurityGroupRules",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeLaunchTemplates",
            "ec2:DescribeLaunchTemplateVersions",
            "ec2:DescribeTags",
            "ec2:DescribeAvailabilityZones",
            "ec2:DescribeAccountAttributes",
            "ec2:DescribeKeyPairs",
            "ec2:DescribeNetworkAcls",
        ],
        vec!["*".into()],
        Some(regional.clone()),
    )];
    for (sid, actions, kinds) in [
        (
            "VpcLifecycle",
            vec!["ec2:ModifyVpcAttribute", "ec2:DeleteVpc"],
            vec!["vpc"],
        ),
        (
            "SubnetLifecycle",
            vec!["ec2:ModifySubnetAttribute", "ec2:DeleteSubnet"],
            vec!["subnet"],
        ),
        (
            "GatewayAttach",
            vec!["ec2:AttachInternetGateway", "ec2:DetachInternetGateway"],
            vec!["vpc", "internet-gateway"],
        ),
        (
            "GatewayDelete",
            vec!["ec2:DeleteInternetGateway"],
            vec!["internet-gateway"],
        ),
        (
            "RoutesLifecycle",
            vec![
                "ec2:CreateRoute",
                "ec2:ReplaceRoute",
                "ec2:DeleteRoute",
                "ec2:DeleteRouteTable",
            ],
            vec!["route-table"],
        ),
        (
            "RouteAssociations",
            vec![
                "ec2:AssociateRouteTable",
                "ec2:DisassociateRouteTable",
                "ec2:ReplaceRouteTableAssociation",
            ],
            vec!["route-table", "subnet"],
        ),
        (
            "AddressRelease",
            vec!["ec2:ReleaseAddress"],
            vec!["elastic-ip"],
        ),
        (
            "NatDelete",
            vec!["ec2:DeleteNatGateway"],
            vec!["natgateway"],
        ),
        (
            "SecurityGroupLifecycle",
            vec![
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupEgress",
                "ec2:DeleteSecurityGroup",
            ],
            vec!["security-group"],
        ),
        (
            "LaunchTemplateLifecycle",
            vec![
                "ec2:CreateLaunchTemplateVersion",
                "ec2:ModifyLaunchTemplate",
                "ec2:DeleteLaunchTemplateVersions",
                "ec2:DeleteLaunchTemplate",
            ],
            vec!["launch-template"],
        ),
    ] {
        network.push(statement(
            sid,
            &actions,
            kinds.iter().map(|k| ec2(k)).collect(),
            Some(owned.clone()),
        ));
    }
    let tag_resources = [
        "vpc",
        "internet-gateway",
        "elastic-ip",
        "subnet",
        "route-table",
        "security-group",
        "natgateway",
        "launch-template",
    ]
    .iter()
    .map(|k| ec2(k))
    .collect::<Vec<_>>();
    network.push(statement("UpdateOwnedTags",&["ec2:CreateTags"],tag_resources.clone(),Some(json!({"StringEquals":{"ec2:ResourceTag/ows:deployment-id":p.deployment_id},"StringEqualsIfExists":{"aws:RequestTag/ows:deployment-id":p.deployment_id}}))));
    network.push(statement("RemoveOtherOwnedTags",&["ec2:DeleteTags"],tag_resources,Some(json!({"StringEquals":{"ec2:ResourceTag/ows:deployment-id":p.deployment_id},"ForAllValues:StringNotEquals":{"aws:TagKeys":["ows:deployment-id"]},"Null":{"aws:TagKeys":"false"}}))));
    let cluster = format!("arn:aws:eks:{region}:{account}:cluster/{prefix}");
    let eks_arn = |kind: &str| format!("arn:aws:eks:{region}:{account}:{kind}/{prefix}/*");
    let eks_resources = vec![
        cluster.clone(),
        eks_arn("nodegroup"),
        eks_arn("addon"),
        eks_arn("access-entry"),
        eks_arn("podidentityassociation"),
    ];
    let mut eks = vec![
        statement(
            "CreateTaggedPrivateCluster",
            &["eks:CreateCluster"],
            vec!["*".into()],
            Some(
                json!({"StringEquals":{"aws:RequestedRegion":region,"aws:RequestTag/ows:deployment-id":p.deployment_id,"eks:authenticationMode":"API"},"Bool":{"eks:endpointPublicAccess":"false","eks:endpointPrivateAccess":"true","eks:bootstrapClusterCreatorAdminPermissions":"false"}}),
            ),
        ),
        statement(
            "ClusterLifecycle",
            &[
                "eks:DescribeCluster",
                "eks:UpdateClusterVersion",
                "eks:DeleteCluster",
                "eks:DescribeUpdate",
                "eks:ListUpdates",
                "eks:CreateNodegroup",
                "eks:CreateAddon",
                "eks:CreateAccessEntry",
                "eks:CreatePodIdentityAssociation",
                "eks:ListNodegroups",
                "eks:ListAddons",
                "eks:ListAccessEntries",
                "eks:ListPodIdentityAssociations",
            ],
            vec![cluster.clone()],
            None,
        ),
        statement(
            "ChildrenLifecycle",
            &[
                "eks:DescribeNodegroup",
                "eks:UpdateNodegroupConfig",
                "eks:UpdateNodegroupVersion",
                "eks:DeleteNodegroup",
                "eks:DescribeAddon",
                "eks:UpdateAddon",
                "eks:DeleteAddon",
                "eks:DescribeAccessEntry",
                "eks:UpdateAccessEntry",
                "eks:DeleteAccessEntry",
                "eks:DescribePodIdentityAssociation",
                "eks:UpdatePodIdentityAssociation",
                "eks:DeletePodIdentityAssociation",
                "eks:ListAssociatedAccessPolicies",
                "eks:DescribeUpdate",
            ],
            eks_resources.clone(),
            None,
        ),
        statement(
            "PlatformAccess",
            &["eks:AssociateAccessPolicy", "eks:DisassociateAccessPolicy"],
            vec![eks_arn("access-entry")],
            Some(
                json!({"StringEquals":{"eks:policyArn":"arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"}}),
            ),
        ),
        statement(
            "EksTags",
            &[
                "eks:TagResource",
                "eks:UntagResource",
                "eks:ListTagsForResource",
            ],
            eks_resources,
            None,
        ),
    ];
    eks.push(statement(
        "AddonPodIdentityDependencies",
        &["eks:CreateAddon"],
        vec![eks_arn("podidentityassociation")],
        None,
    ));
    eks.push(statement("KeepClusterPrivate", &["eks:UpdateClusterConfig"], vec![cluster.clone()], Some(json!({"BoolIfExists":{"eks:endpointPublicAccess":"false","eks:endpointPrivateAccess":"true"}}))));
    for (sid, roles, services) in [
        (
            "PassClusterRole",
            vec!["cluster"],
            vec!["eks.amazonaws.com"],
        ),
        (
            "PassNodeRole",
            vec!["node"],
            vec!["eks.amazonaws.com", "ec2.amazonaws.com"],
        ),
        (
            "PassPodRoles",
            vec!["ebs", "artifacts"],
            vec!["pods.eks.amazonaws.com", "eks.amazonaws.com"],
        ),
    ] {
        eks.push(statement(
            sid,
            &["iam:PassRole"],
            roles.iter().map(|r| role_arn(p, r)).collect(),
            Some(json!({"StringEquals":{"iam:PassedToService":services}})),
        ));
    }
    eks.push(statement(
        "ReadExactPlatformRoles",
        &["iam:GetRole", "iam:ListAttachedRolePolicies"],
        ["cluster", "node", "ebs", "artifacts"]
            .iter()
            .map(|r| role_arn(p, r))
            .collect(),
        None,
    ));
    // EKS validates that its already-created node-group service-linked role exists
    // using the caller's GetRole authority. Preflight's operator check is not a
    // substitute for these exact executor reads. No SLR creation/modification.
    eks.push(statement(
        "ReadRequiredServiceLinkedRoles",
        &["iam:GetRole", "iam:ListAttachedRolePolicies"],
        [
            ("eks.amazonaws.com", "AWSServiceRoleForAmazonEKS"),
            (
                "eks-nodegroup.amazonaws.com",
                "AWSServiceRoleForAmazonEKSNodegroup",
            ),
            ("autoscaling.amazonaws.com", "AWSServiceRoleForAutoScaling"),
            ("rds.amazonaws.com", "AWSServiceRoleForRDS"),
        ]
        .iter()
        .map(|(service, name)| {
            format!("arn:aws:iam::{account}:role/aws-service-role/{service}/{name}")
        })
        .collect(),
        None,
    ));
    let db = format!("arn:aws:rds:{region}:{account}:db:{prefix}");
    let dbsub = format!("arn:aws:rds:{region}:{account}:subgrp:{prefix}");
    let dbparam = format!("arn:aws:rds:{region}:{account}:pg:{prefix}");
    let secret =
        format!("arn:aws:secretsmanager:{region}:{account}:secret:{prefix}-database-??????");
    let bucket_arn = format!("arn:aws:s3:::{}", bucket(p));
    let data = vec![
        statement(
            "DatabaseLifecycle",
            &[
                "rds:CreateDBInstance",
                "rds:ModifyDBInstance",
                "rds:DeleteDBInstance",
                "rds:DescribeDBInstances",
            ],
            vec![db.clone(), dbsub.clone(), dbparam.clone()],
            None,
        ),
        statement(
            "DatabaseSubnets",
            &[
                "rds:CreateDBSubnetGroup",
                "rds:ModifyDBSubnetGroup",
                "rds:DeleteDBSubnetGroup",
                "rds:DescribeDBSubnetGroups",
            ],
            vec![dbsub.clone()],
            None,
        ),
        statement(
            "ReadDatabaseEngineDefaults",
            &[
                "rds:DescribeEngineDefaultParameters",
                "rds:DescribeDBEngineVersions",
            ],
            vec!["*".into()],
            Some(regional.clone()),
        ),
        statement(
            "DatabaseParameters",
            &[
                "rds:CreateDBParameterGroup",
                "rds:ModifyDBParameterGroup",
                "rds:ResetDBParameterGroup",
                "rds:DeleteDBParameterGroup",
                "rds:DescribeDBParameterGroups",
                "rds:DescribeDBParameters",
            ],
            vec![dbparam.clone()],
            None,
        ),
        statement(
            "DatabaseTags",
            &[
                "rds:AddTagsToResource",
                "rds:RemoveTagsFromResource",
                "rds:ListTagsForResource",
            ],
            vec![db, dbsub, dbparam],
            None,
        ),
        statement(
            "NamedDatabaseSecret",
            &[
                "secretsmanager:CreateSecret",
                "secretsmanager:DescribeSecret",
                "secretsmanager:UpdateSecret",
                "secretsmanager:PutSecretValue",
                "secretsmanager:GetSecretValue",
                "secretsmanager:DeleteSecret",
                "secretsmanager:TagResource",
                "secretsmanager:UntagResource",
            ],
            vec![secret],
            None,
        ),
        statement(
            "GenerateDatabasePassword",
            &["secretsmanager:GetRandomPassword"],
            vec!["*".into()],
            Some(regional.clone()),
        ),
        statement(
            "DescribeEncryptionKeys",
            &["kms:DescribeKey"],
            vec![format!("arn:aws:kms:{region}:{account}:key/*")],
            None,
        ),
        statement(
            "ArtifactBucketLifecycle",
            &[
                "s3:CreateBucket",
                "s3:DeleteBucket",
                "s3:GetBucketLocation",
                "s3:GetAccelerateConfiguration",
                "s3:GetAnalyticsConfiguration",
                "s3:GetBucketCORS",
                "s3:GetInventoryConfiguration",
                "s3:GetBucketLogging",
                "s3:GetMetricsConfiguration",
                "s3:GetBucketNotification",
                "s3:GetBucketVersioning",
                "s3:GetReplicationConfiguration",
                "s3:GetBucketWebsite",
                "s3:GetBucketObjectLockConfiguration",
                "s3:GetBucketAcl",
                "s3:GetIntelligentTieringConfiguration",
                "s3:GetBucketMetadataTableConfiguration",
                "s3:ListBucket",
                "s3:GetBucketPolicy",
                "s3:PutBucketPolicy",
                "s3:DeleteBucketPolicy",
                "s3:GetBucketTagging",
                "s3:PutBucketTagging",
                "s3:GetBucketPublicAccessBlock",
                "s3:PutBucketPublicAccessBlock",
                "s3:GetEncryptionConfiguration",
                "s3:PutEncryptionConfiguration",
                "s3:GetBucketOwnershipControls",
                "s3:PutBucketOwnershipControls",
                "s3:GetLifecycleConfiguration",
                "s3:PutLifecycleConfiguration",
            ],
            vec![bucket_arn.clone()],
            None,
        ),
        statement(
            "ImageRepositories",
            &[
                "ecr:CreateRepository",
                "ecr:DescribeRepositories",
                "ecr:GetLifecyclePolicy",
                "ecr:GetRepositoryPolicy",
                "ecr:DeleteRepository",
                "ecr:TagResource",
                "ecr:UntagResource",
                "ecr:ListTagsForResource",
                "ecr:PutImageTagMutability",
                "ecr:PutImageScanningConfiguration",
            ],
            ["harness", "runner", "service-ui"]
                .iter()
                .map(|s| format!("arn:aws:ecr:{region}:{account}:repository/{prefix}/{s}"))
                .collect(),
            None,
        ),
    ];
    let lambda = format!("arn:aws:lambda:{region}:{account}:function:{prefix}-cleanup");
    let rule = format!("arn:aws:events:{region}:{account}:rule/{prefix}-cleanup");
    let log = format!("arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{prefix}-cleanup");
    let guard_permissions = vec![
        statement(
            "GuardControlTable",
            &[
                "dynamodb:CreateTable",
                "dynamodb:DescribeTable",
                "dynamodb:DeleteTable",
                "dynamodb:UpdateTable",
                "dynamodb:TagResource",
                "dynamodb:UntagResource",
                "dynamodb:ListTagsOfResource",
            ],
            vec![format!(
                "arn:aws:dynamodb:{region}:{account}:table/{prefix}-control"
            )],
            None,
        ),
        statement(
            "GuardFunction",
            &[
                "lambda:CreateFunction",
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:DeleteFunction",
                "lambda:PutFunctionConcurrency",
                "lambda:DeleteFunctionConcurrency",
                "lambda:GetFunctionConcurrency",
                "lambda:AddPermission",
                "lambda:RemovePermission",
                "lambda:GetPolicy",
                "lambda:TagResource",
                "lambda:UntagResource",
                "lambda:ListTags",
            ],
            vec![lambda],
            None,
        ),
        statement(
            "PassGuardRole",
            &["iam:PassRole"],
            vec![role_arn(p, "cleanup")],
            Some(json!({"StringEquals":{"iam:PassedToService":"lambda.amazonaws.com"}})),
        ),
        statement(
            "PinnedGuardCode",
            &["s3:GetObjectVersion"],
            vec![format!(
                "arn:aws:s3:::{}/{}",
                i.guard_code.bucket, i.guard_code.key
            )],
            Some(json!({"StringEquals":{"s3:VersionId":i.guard_code.version_id}})),
        ),
        statement(
            "GuardSchedule",
            &[
                "events:PutRule",
                "events:DescribeRule",
                "events:DeleteRule",
                "events:PutTargets",
                "events:RemoveTargets",
                "events:ListTargetsByRule",
                "events:TagResource",
                "events:UntagResource",
                "events:ListTagsForResource",
            ],
            vec![rule],
            None,
        ),
        statement(
            "GuardLogs",
            &[
                "logs:CreateLogGroup",
                "logs:DeleteLogGroup",
                "logs:PutRetentionPolicy",
                "logs:DeleteRetentionPolicy",
                "logs:TagResource",
                "logs:UntagResource",
                "logs:ListTagsForResource",
            ],
            vec![log.clone(), format!("{log}:*")],
            None,
        ),
        statement(
            "ObserveLogGroups",
            &["logs:DescribeLogGroups"],
            vec!["*".into()],
            Some(regional.clone()),
        ),
    ];
    let cleanup = policy(json!([
        statement(
            "GuardControlRecord",
            &["dynamodb:GetItem", "dynamodb:PutItem"],
            vec![format!(
                "arn:aws:dynamodb:{region}:{account}:table/{prefix}-control"
            )],
            Some(
                json!({"ForAllValues:StringEquals":{"dynamodb:LeadingKeys":[p.deployment_id]},"Null":{"dynamodb:LeadingKeys":"false"}})
            )
        ),
        statement(
            "ObserveOwnedInfraStack",
            &[
                "cloudformation:DescribeStacks",
                "cloudformation:DescribeStackEvents",
                "cloudformation:ListStackResources"
            ],
            vec![format!(
                "arn:aws:cloudformation:{region}:{account}:stack/{prefix}-infra/*"
            )],
            None
        ),
        statement(
            "DeleteOwnedInfraStack",
            &["cloudformation:DeleteStack"],
            vec![format!(
                "arn:aws:cloudformation:{region}:{account}:stack/{prefix}-infra/*"
            )],
            Some(json!({"StringEquals":{"cloudformation:RoleArn":role_arn(p,"executor")}}))
        ),
        statement(
            "PassDeletionExecutor",
            &["iam:PassRole"],
            vec![role_arn(p, "executor")],
            Some(json!({"StringEquals":{"iam:PassedToService":"cloudformation.amazonaws.com"}}))
        ),
        statement(
            "CheckBucketOwnership",
            &[
                "s3:GetBucketTagging",
                "s3:GetBucketVersioning",
                "s3:ListBucket",
                "s3:ListBucketMultipartUploads"
            ],
            vec![bucket_arn.clone()],
            None
        ),
        statement(
            "DeleteOwnedObjects",
            &[
                "s3:DeleteObject",
                "s3:AbortMultipartUpload",
                "s3:ListMultipartUploadParts"
            ],
            vec![format!("{bucket_arn}/{}/*", p.deployment_id)],
            None
        ),
        statement(
            "ObserveVolumes",
            &["ec2:DescribeVolumes"],
            vec!["*".into()],
            Some(regional)
        ),
        statement(
            "DeleteOwnedVolumes",
            &["ec2:DeleteVolume"],
            vec![ec2("volume")],
            Some(owned)
        ),
        statement(
            "GuardActivity",
            &["logs:CreateLogStream", "logs:PutLogEvents"],
            vec![format!("{log}:log-stream:*")],
            None
        ),
    ]));
    let mut execution = BTreeMap::from([
        ("NetworkCreate".into(), policy(json!(create))),
        ("NetworkManage".into(), policy(json!(network))),
        ("Eks".into(), policy(json!(eks))),
        ("Nodes".into(), nodes::permissions(p)),
        ("Data".into(), policy(json!(data))),
        ("Guard".into(), policy(json!(guard_permissions))),
    ]);
    if i.operator_access.is_some() {
        execution.insert("Access".into(), access::permissions(p));
    }
    let mut coverage = BTreeMap::new();
    for (phase, t) in [("infrastructure", infra), ("guard", guard)] {
        for (logical, r) in t["Resources"]
            .as_object()
            .context("Template resources missing")?
        {
            let kind = r["Type"].as_str().context("Resource type missing")?;
            let domains = match kind {
                "AWS::EC2::VPC"
                | "AWS::EC2::InternetGateway"
                | "AWS::EC2::Subnet"
                | "AWS::EC2::RouteTable"
                | "AWS::EC2::EIP"
                | "AWS::EC2::NatGateway"
                | "AWS::EC2::SecurityGroupIngress"
                | "AWS::EC2::SecurityGroup"
                | "AWS::EC2::LaunchTemplate" => vec!["NetworkCreate", "NetworkManage"],
                "AWS::EC2::VPCGatewayAttachment"
                | "AWS::EC2::Route"
                | "AWS::EC2::SubnetRouteTableAssociation" => vec!["NetworkManage"],
                "AWS::EC2::Instance" => vec!["Access"],
                "AWS::EKS::Nodegroup" => vec!["Eks", "Nodes"],
                "AWS::EKS::Cluster"
                | "AWS::EKS::Addon"
                | "AWS::EKS::AccessEntry"
                | "AWS::EKS::PodIdentityAssociation" => vec!["Eks"],
                "AWS::RDS::DBInstance"
                | "AWS::RDS::DBSubnetGroup"
                | "AWS::RDS::DBParameterGroup"
                | "AWS::SecretsManager::Secret"
                | "AWS::SecretsManager::SecretTargetAttachment"
                | "AWS::S3::Bucket"
                | "AWS::S3::BucketPolicy"
                | "AWS::ECR::Repository" => vec!["Data"],
                "AWS::DynamoDB::Table"
                | "AWS::Lambda::Function"
                | "AWS::Lambda::Permission"
                | "AWS::Events::Rule"
                | "AWS::Logs::LogGroup" => vec!["Guard"],
                _ => bail!("Resource has no reviewed permission domain: {kind}"),
            };
            coverage.insert(
                format!("{phase}/{logical}"),
                json!({"type":kind,"domains":domains}),
            );
        }
    }
    let review = json!({"schemaVersion":1,"cloudValidated":false,"coverage":coverage,
        "broadGrants":[
            {"statement":"NetworkObservation","reason":"EC2 Describe APIs do not support individual resource scoping; regional metadata only"},
            {"statement":"CreateTaggedPrivateCluster","reason":"EKS CreateCluster requires Resource *; request ownership tag and region/private-endpoint conditions constrain creation but do not cap cost or count"},
            {"statement":"RegistryLogin (setup)","reason":"ECR GetAuthorizationToken requires Resource *; requested region/expiry constrain login; actual image writes are restricted to three exact repositories"},
            {"statement":"ReadDatabaseEngineDefaults","reason":"RDS handlers read built-in engine-family defaults and engine-version metadata; this catalog API has no individual-resource authorization and is region constrained, with no database data or secret reads"},
            {"statement":"GenerateDatabasePassword","reason":"GetRandomPassword has no resource; returns generated randomness, not existing secret data"},
            {"statement":"ObserveLogGroups","reason":"DescribeLogGroups has no individual-resource authorization; regional metadata"},
            {"statement":"DescribeEncryptionKeys","reason":"Default AWS-managed key IDs resolve online; DescribeKey only within this account/region, no decrypt/grant/write"},
            {"statement":"Worker launch dependencies","reason":"Tagged EKS launch templates require caller RunInstances/CreateTags. Regional image/interface ARNs are permitted only through a launch template that separately must have this deployment ownership tag; instance type, subnet, cluster security group and new instance/volume tags are independently constrained. EKS resolves the pinned recommended release AMI; preflight checks it, not this IAM wildcard."},
            {"statement":"EC2 create/manage","reason":"Resource IDs are not known offline; resource-type/account/region ARN scopes plus creation/ownership tags. Parent resource authorizations are separate"},
            {"statement":"AWS managed platform policies","reason":"Explicit AWS-managed cluster/node/CSI grants are attached only by customer bootstrap; review their upstream permissions and node metadata isolation"}
        ],"notGranted":["IAM identity/policy mutation by executor","arbitrary PassRole","model secret reads","unrelated object reads","account-wide AdministratorAccess","service-linked role creation"],
        "remainingVerification":["AWS service IAM simulation and live resource handlers","creation/rollback/delete dependency permissions","SCP and permission boundaries","guard package digest and behavior","private control-plane connectivity","post-install network enforcement and volume ownership tagging"]});
    Ok(Policies {
        execution,
        cleanup,
        review,
    })
}
