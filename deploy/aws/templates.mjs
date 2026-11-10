// Disposable, private-access experiment. Not an AWS production landing zone.
import { account, region } from "./client.mjs";
const ref = (x) => ({ Ref: x }),
  att = (x, k) => ({ "Fn::GetAtt": [x, k] }),
  sub = (s) => ({ "Fn::Sub": s });
export const name = "ow-taskdesk-20261012";
const tag = (expiry) => [
  { Key: "Project", Value: name },
  { Key: "ExpiresAt", Value: expiry },
];
const r = (Type, Properties, extra = {}) => ({ Type, Properties, ...extra });
const trust = (service) => ({
  Version: "2012-10-17",
  Statement: [
    {
      Effect: "Allow",
      Principal: { Service: service },
      Action: "sts:AssumeRole",
    },
  ],
});
const policy = (Statement) => ({ Version: "2012-10-17", Statement });
const allow = (Action, Resource, extra = {}) => ({
  Effect: "Allow",
  Action,
  Resource,
  ...extra,
});
export function infra({
  expiry,
  operatorCidr,
  cniVersion,
  podIdentityVersion,
  ebsVersion,
  pgVersion,
}) {
  if (!/^\d+\.\d+\.\d+\.\d+\/32$/.test(operatorCidr))
    throw Error("A single operator IPv4 /32 is required");
  const Tags = tag(expiry),
    Resources = {};
  const add = (key, type, props, extra) =>
    (Resources[key] = r(type, props, extra));
  add("Vpc", "AWS::EC2::VPC", {
    CidrBlock: "10.94.0.0/16",
    EnableDnsSupport: true,
    EnableDnsHostnames: true,
    Tags,
  });
  add("Gateway", "AWS::EC2::InternetGateway", { Tags });
  add("GatewayAttachment", "AWS::EC2::VPCGatewayAttachment", {
    VpcId: ref("Vpc"),
    InternetGatewayId: ref("Gateway"),
  });
  add("PublicRoutes", "AWS::EC2::RouteTable", { VpcId: ref("Vpc"), Tags });
  add(
    "InternetRoute",
    "AWS::EC2::Route",
    {
      RouteTableId: ref("PublicRoutes"),
      DestinationCidrBlock: "0.0.0.0/0",
      GatewayId: ref("Gateway"),
    },
    { DependsOn: "GatewayAttachment" },
  );
  for (let i = 0; i < 2; i++)
    for (const kind of ["Public", "Private"]) {
      const key = kind + i;
      add(key, "AWS::EC2::Subnet", {
        VpcId: ref("Vpc"),
        AvailabilityZone: { "Fn::Select": [i, { "Fn::GetAZs": "" }] },
        CidrBlock: `10.94.${kind === "Public" ? i : 10 + i}.0/24`,
        MapPublicIpOnLaunch: kind === "Public",
        Tags,
      });
      if (kind === "Public")
        add("RouteAssociation" + i, "AWS::EC2::SubnetRouteTableAssociation", {
          SubnetId: ref(key),
          RouteTableId: ref("PublicRoutes"),
        });
    }
  add("ClusterRole", "AWS::IAM::Role", {
    RoleName: name + "-cluster",
    AssumeRolePolicyDocument: trust("eks.amazonaws.com"),
    ManagedPolicyArns: ["arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"],
    Tags,
  });
  add("NodeRole", "AWS::IAM::Role", {
    RoleName: name + "-node",
    AssumeRolePolicyDocument: trust("ec2.amazonaws.com"),
    ManagedPolicyArns: [
      "AmazonEKSWorkerNodePolicy",
      "AmazonEC2ContainerRegistryPullOnly",
      "AmazonEKS_CNI_Policy",
      "AmazonSSMManagedInstanceCore",
    ].map((x) => "arn:aws:iam::aws:policy/" + x),
    Tags,
  });
  add("Cluster", "AWS::EKS::Cluster", {
    Name: name,
    Version: "1.36",
    RoleArn: att("ClusterRole", "Arn"),
    BootstrapSelfManagedAddons: false,
    AccessConfig: {
      AuthenticationMode: "API",
      BootstrapClusterCreatorAdminPermissions: false,
    },
    UpgradePolicy: { SupportType: "STANDARD" },
    ResourcesVpcConfig: {
      SubnetIds: [ref("Public0"), ref("Public1")],
      EndpointPrivateAccess: true,
      EndpointPublicAccess: true,
      PublicAccessCidrs: [operatorCidr],
    },
    Tags,
  });
  add("OperatorAccess", "AWS::EKS::AccessEntry", {
    ClusterName: ref("Cluster"),
    PrincipalArn: `arn:aws:iam::${account}:role/OrganizationAccountAccessRole`,
    Type: "STANDARD",
    AccessPolicies: [
      {
        PolicyArn:
          "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
        AccessScope: { Type: "cluster" },
      },
    ],
  });
  // Only trusted cluster bootstrap pods exist in standard mode. The installer
  // applies their policies, switches to strict, then admits harness/sandbox pods.
  add("Cni", "AWS::EKS::Addon", {
    ClusterName: ref("Cluster"),
    AddonName: "vpc-cni",
    AddonVersion: cniVersion,
    ConfigurationValues: JSON.stringify({
      enableNetworkPolicy: "true",
      env: { NETWORK_POLICY_ENFORCING_MODE: "standard" },
    }),
    Tags,
  });
  add("KubeProxy", "AWS::EKS::Addon", {
    ClusterName: ref("Cluster"),
    AddonName: "kube-proxy",
    Tags,
  });
  add(
    "CoreDns",
    "AWS::EKS::Addon",
    { ClusterName: ref("Cluster"), AddonName: "coredns", Tags },
    { DependsOn: "Nodes" },
  );
  add("LaunchTemplate", "AWS::EC2::LaunchTemplate", {
    LaunchTemplateName: name,
    LaunchTemplateData: {
      MetadataOptions: {
        HttpTokens: "required",
        HttpPutResponseHopLimit: 1,
        HttpEndpoint: "enabled",
      },
      BlockDeviceMappings: [
        {
          DeviceName: "/dev/xvda",
          Ebs: {
            VolumeSize: 30,
            VolumeType: "gp3",
            Encrypted: true,
            DeleteOnTermination: true,
          },
        },
      ],
      TagSpecifications: [
        { ResourceType: "instance", Tags },
        { ResourceType: "volume", Tags },
      ],
    },
    TagSpecifications: [{ ResourceType: "launch-template", Tags }],
  });
  add(
    "Nodes",
    "AWS::EKS::Nodegroup",
    {
      ClusterName: ref("Cluster"),
      NodegroupName: "workers",
      NodeRole: att("NodeRole", "Arn"),
      Subnets: [ref("Public0"), ref("Public1")],
      AmiType: "AL2023_ARM_64_STANDARD",
      InstanceTypes: ["t4g.large"],
      CapacityType: "ON_DEMAND",
      ScalingConfig: { MinSize: 2, MaxSize: 2, DesiredSize: 2 },
      LaunchTemplate: {
        Id: ref("LaunchTemplate"),
        Version: att("LaunchTemplate", "LatestVersionNumber"),
      },
      Tags: { Project: name, ExpiresAt: expiry },
    },
    { DependsOn: ["Cni", "KubeProxy", "InternetRoute"] },
  );
  add(
    "PodIdentityAgent",
    "AWS::EKS::Addon",
    {
      ClusterName: ref("Cluster"),
      AddonName: "eks-pod-identity-agent",
      AddonVersion: podIdentityVersion,
      Tags,
    },
    { DependsOn: "Nodes" },
  );
  const podTrust = {
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Principal: { Service: "pods.eks.amazonaws.com" },
        Action: ["sts:AssumeRole", "sts:TagSession"],
        Condition: {
          StringEquals: { "aws:SourceAccount": account },
          ArnEquals: {
            "aws:SourceArn": `arn:aws:eks:${region}:${account}:cluster/${name}`,
          },
        },
      },
    ],
  };
  add("EbsRole", "AWS::IAM::Role", {
    RoleName: name + "-ebs",
    AssumeRolePolicyDocument: podTrust,
    ManagedPolicyArns: [
      "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy",
    ],
    Tags,
  });
  add(
    "Ebs",
    "AWS::EKS::Addon",
    {
      ClusterName: ref("Cluster"),
      AddonName: "aws-ebs-csi-driver",
      AddonVersion: ebsVersion,
      PodIdentityAssociations: [
        {
          ServiceAccount: "ebs-csi-controller-sa",
          RoleArn: att("EbsRole", "Arn"),
        },
      ],
      Tags,
    },
    { DependsOn: "PodIdentityAgent" },
  );
  add("DatabaseSecurityGroup", "AWS::EC2::SecurityGroup", {
    GroupDescription: "Only this experiment cluster reaches PostgreSQL",
    VpcId: ref("Vpc"),
    SecurityGroupIngress: [
      {
        IpProtocol: "tcp",
        FromPort: 5432,
        ToPort: 5432,
        SourceSecurityGroupId: att("Cluster", "ClusterSecurityGroupId"),
      },
    ],
    Tags,
  });
  add("DatabaseSubnets", "AWS::RDS::DBSubnetGroup", {
    DBSubnetGroupDescription: "Private experiment database",
    SubnetIds: [ref("Private0"), ref("Private1")],
    Tags,
  });
  add("DatabaseParameters", "AWS::RDS::DBParameterGroup", {
    Family: "postgres16",
    Description: "TLS required",
    Parameters: { "rds.force_ssl": "1" },
    Tags,
  });
  add(
    "Database",
    "AWS::RDS::DBInstance",
    {
      DBInstanceIdentifier: name,
      Engine: "postgres",
      EngineVersion: pgVersion,
      DBInstanceClass: "db.t4g.micro",
      AllocatedStorage: "20",
      StorageType: "gp3",
      StorageEncrypted: true,
      DBName: "openworker",
      MasterUsername: "openworker",
      ManageMasterUserPassword: true,
      DBSubnetGroupName: ref("DatabaseSubnets"),
      DBParameterGroupName: ref("DatabaseParameters"),
      VPCSecurityGroups: [ref("DatabaseSecurityGroup")],
      PubliclyAccessible: false,
      MultiAZ: false,
      BackupRetentionPeriod: 0,
      DeleteAutomatedBackups: true,
      DeletionProtection: false,
      EnablePerformanceInsights: false,
      Tags,
    },
    { DeletionPolicy: "Delete", UpdateReplacePolicy: "Delete" },
  );
  const bucket = `${name}-${account}`;
  add(
    "Artifacts",
    "AWS::S3::Bucket",
    {
      BucketName: bucket,
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        IgnorePublicAcls: true,
        BlockPublicPolicy: true,
        RestrictPublicBuckets: true,
      },
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [
          { ServerSideEncryptionByDefault: { SSEAlgorithm: "AES256" } },
        ],
      },
      OwnershipControls: {
        Rules: [{ ObjectOwnership: "BucketOwnerEnforced" }],
      },
      LifecycleConfiguration: {
        Rules: [
          {
            Id: "abort-multipart",
            Status: "Enabled",
            AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 },
          },
        ],
      },
      Tags,
    },
    { DeletionPolicy: "Delete", UpdateReplacePolicy: "Delete" },
  );
  add("ArtifactTls", "AWS::S3::BucketPolicy", {
    Bucket: ref("Artifacts"),
    PolicyDocument: policy([
      {
        Effect: "Deny",
        Principal: "*",
        Action: "s3:*",
        Resource: [`arn:aws:s3:::${bucket}`, `arn:aws:s3:::${bucket}/*`],
        Condition: { Bool: { "aws:SecureTransport": "false" } },
      },
    ]),
  });
  add("ArtifactRole", "AWS::IAM::Role", {
    RoleName: name + "-artifacts",
    AssumeRolePolicyDocument: podTrust,
    Policies: [
      {
        PolicyName: "artifact-objects",
        PolicyDocument: policy([
          allow(
            ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
            `arn:aws:s3:::${bucket}/*`,
          ),
          allow(
            ["s3:ListBucket", "s3:GetBucketLocation"],
            `arn:aws:s3:::${bucket}`,
          ),
        ]),
      },
    ],
    Tags,
  });
  add(
    "ArtifactIdentity",
    "AWS::EKS::PodIdentityAssociation",
    {
      ClusterName: ref("Cluster"),
      Namespace: "ow-app",
      ServiceAccount: "ow-api",
      RoleArn: att("ArtifactRole", "Arn"),
    },
    { DependsOn: "PodIdentityAgent" },
  );
  for (const suffix of ["harness", "runner", "taskdesk"])
    add("Image" + suffix, "AWS::ECR::Repository", {
      RepositoryName: name + "/" + suffix,
      EmptyOnDelete: true,
      ImageTagMutability: "IMMUTABLE",
      EncryptionConfiguration: { EncryptionType: "AES256" },
      Tags,
    });
  return {
    AWSTemplateFormatVersion: "2010-09-09",
    Description:
      "Disposable OpenWorker Task Desk experiment; no public workload ingress",
    Resources,
    Outputs: {
      Cluster: { Value: ref("Cluster") },
      Vpc: { Value: ref("Vpc") },
      DatabaseEndpoint: { Value: att("Database", "Endpoint.Address") },
      DatabaseSecret: { Value: att("Database", "MasterUserSecret.SecretArn") },
      Bucket: { Value: ref("Artifacts") },
      HarnessRepository: { Value: att("Imageharness", "RepositoryUri") },
      RunnerRepository: { Value: att("Imagerunner", "RepositoryUri") },
      AppRepository: { Value: att("Imagetaskdesk", "RepositoryUri") },
    },
  };
}
