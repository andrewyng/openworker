import { account, region } from "./client.mjs";
import { name } from "./templates.mjs";
const ref = (x) => ({ Ref: x }),
  att = (x, k) => ({ "Fn::GetAtt": [x, k] });
const statement = (Action, Resource, Condition) => ({
  Effect: "Allow",
  Action,
  Resource,
  ...(Condition ? { Condition } : {}),
});
export function guard({ expiry, code }) {
  const email = process.env.OW_AWS_BUDGET_EMAIL;
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
    throw Error("Set OW_AWS_BUDGET_EMAIL for experiment cost alerts");
  const stack = `arn:aws:cloudformation:${region}:${account}:stack/${name}/*`,
    bucket = `${name}-${account}`;
  const regional = { StringEquals: { "aws:RequestedRegion": region } };
  const actions = [
    statement(
      ["events:DisableRule"],
      `arn:aws:events:${region}:${account}:rule/${name}-cleanup`,
    ),
    statement(
      [
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:ListStackResources",
        "cloudformation:DeleteStack",
      ],
      stack,
    ),
    statement(
      [
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:GetBucketLocation",
        "s3:GetBucketPolicy",
        "s3:DeleteBucket",
        "s3:DeleteBucketPolicy",
      ],
      `arn:aws:s3:::${bucket}`,
    ),
    statement(
      [
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts",
      ],
      `arn:aws:s3:::${bucket}/*`,
    ),
    statement(["ec2:Describe*"], "*", regional),
    statement(["ec2:DeleteVolume"], "*", {
      StringEquals: {
        "aws:RequestedRegion": region,
        "ec2:ResourceTag/Project": name,
      },
    }),
    // CFN executes deletion using the caller's temporary role, not the original
    // operator's administrator session. These are deletion-only infra operations.
    statement(
      [
        "ec2:DeleteVpc",
        "ec2:DeleteSubnet",
        "ec2:DeleteInternetGateway",
        "ec2:DetachInternetGateway",
        "ec2:DeleteRouteTable",
        "ec2:DeleteRoute",
        "ec2:DisassociateRouteTable",
        "ec2:DeleteSecurityGroup",
        "ec2:RevokeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:DeleteLaunchTemplate",
        "ec2:DeleteTags",
      ],
      "*",
      regional,
    ),
    statement(
      [
        "eks:DescribeCluster",
        "eks:DeleteCluster",
        "eks:DescribeNodegroup",
        "eks:DeleteNodegroup",
        "eks:DescribeAddon",
        "eks:DeleteAddon",
        "eks:DescribeAccessEntry",
        "eks:DeleteAccessEntry",
        "eks:DisassociateAccessPolicy",
        "eks:ListAssociatedAccessPolicies",
        "eks:DescribePodIdentityAssociation",
        "eks:DeletePodIdentityAssociation",
      ],
      [
        `arn:aws:eks:${region}:${account}:cluster/${name}`,
        `arn:aws:eks:${region}:${account}:nodegroup/${name}/*`,
        `arn:aws:eks:${region}:${account}:addon/${name}/*`,
        `arn:aws:eks:${region}:${account}:access-entry/${name}/*`,
        `arn:aws:eks:${region}:${account}:podidentityassociation/${name}/*`,
      ],
    ),
    statement(
      [
        "iam:GetRole",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:DetachRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:DeleteRole",
      ],
      `arn:aws:iam::${account}:role/${name}-*`,
    ),
    statement(
      [
        "rds:DescribeDBInstances",
        "rds:DescribeDBSubnetGroups",
        "rds:DescribeDBParameterGroups",
      ],
      "*",
      regional,
    ),
    statement(
      ["rds:DeleteDBInstance"],
      `arn:aws:rds:${region}:${account}:db:${name}`,
    ),
    statement(
      ["rds:DeleteDBSubnetGroup", "rds:DeleteDBParameterGroup"],
      [
        `arn:aws:rds:${region}:${account}:subgrp:${name}-*`,
        `arn:aws:rds:${region}:${account}:pg:${name}-*`,
      ],
    ),
    statement(
      ["secretsmanager:DeleteSecret", "secretsmanager:DescribeSecret"],
      `arn:aws:secretsmanager:${region}:${account}:secret:rds!db-*`,
    ),
    statement(
      ["ecr:DescribeRepositories", "ecr:DeleteRepository"],
      `arn:aws:ecr:${region}:${account}:repository/${name}/*`,
    ),
    statement(
      ["logs:CreateLogStream", "logs:PutLogEvents"],
      `arn:aws:logs:${region}:${account}:log-group:/aws/lambda/${name}-cleanup:*`,
    ),
  ];
  return {
    AWSTemplateFormatVersion: "2010-09-09",
    Description:
      "Bounded Task Desk expiry guard; retain until teardown is verified",
    Resources: {
      Budget: {
        Type: "AWS::Budgets::Budget",
        Properties: {
          Budget: {
            BudgetName: name + "-oregon-100",
            BudgetType: "COST",
            TimeUnit: "MONTHLY",
            BudgetLimit: { Amount: 100, Unit: "USD" },
            CostFilters: { Region: [region] },
          },
          NotificationsWithSubscribers: [50, 80, 100].map((Threshold) => ({
            Notification: {
              NotificationType: "ACTUAL",
              ComparisonOperator: "GREATER_THAN",
              Threshold,
              ThresholdType: "PERCENTAGE",
            },
            Subscribers: [
              {
                SubscriptionType: "EMAIL",
                Address: email,
              },
            ],
          })),
        },
      },
      Role: {
        Type: "AWS::IAM::Role",
        Properties: {
          RoleName: name + "-cleanup",
          AssumeRolePolicyDocument: {
            Version: "2012-10-17",
            Statement: [
              {
                Effect: "Allow",
                Principal: { Service: "lambda.amazonaws.com" },
                Action: "sts:AssumeRole",
              },
            ],
          },
          Policies: [
            {
              PolicyName: "delete-owned-experiment",
              PolicyDocument: { Version: "2012-10-17", Statement: actions },
            },
          ],
        },
      },
      Logs: {
        Type: "AWS::Logs::LogGroup",
        Properties: {
          LogGroupName: "/aws/lambda/" + name + "-cleanup",
          RetentionInDays: 7,
        },
      },
      Cleanup: {
        Type: "AWS::Lambda::Function",
        DependsOn: "Logs",
        Properties: {
          FunctionName: name + "-cleanup",
          Runtime: "nodejs22.x",
          Handler: "index.handler",
          Role: att("Role", "Arn"),
          Timeout: 120,
          MemorySize: 256,
          ReservedConcurrentExecutions: 1,
          Environment: {
            Variables: {
              EXPERIMENT: name,
              ACCOUNT: account,
              EXPIRY: expiry,
              BUCKET: bucket,
            },
          },
          Code: { ZipFile: code },
        },
      },
      Schedule: {
        Type: "AWS::Events::Rule",
        Properties: {
          Name: name + "-cleanup",
          ScheduleExpression: "rate(5 minutes)",
          State: "ENABLED",
          Targets: [
            {
              Id: "cleanup",
              Arn: att("Cleanup", "Arn"),
              RetryPolicy: {
                MaximumRetryAttempts: 2,
                MaximumEventAgeInSeconds: 300,
              },
            },
          ],
        },
      },
      Invoke: {
        Type: "AWS::Lambda::Permission",
        Properties: {
          FunctionName: ref("Cleanup"),
          Action: "lambda:InvokeFunction",
          Principal: "events.amazonaws.com",
          SourceArn: att("Schedule", "Arn"),
          SourceAccount: account,
        },
      },
    },
    Outputs: { Function: { Value: ref("Cleanup") } },
  };
}
