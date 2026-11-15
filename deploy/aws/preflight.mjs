import { connect } from "./client.mjs";
const { aws } = await connect();
for (const [name, args] of [
  [
    "computeQuota",
    [
      "service-quotas",
      "get-service-quota",
      "--service-code",
      "ec2",
      "--quota-code",
      "L-1216C47A",
      "--query",
      "Quota.{name:QuotaName,value:Value}",
    ],
  ],
  [
    "eksVersions",
    [
      "eks",
      "describe-cluster-versions",
      "--query",
      "clusterVersions[].{version:clusterVersion,status:versionStatus,end:endOfStandardSupportDate}",
    ],
  ],
  [
    "rdsVersions",
    [
      "rds",
      "describe-db-engine-versions",
      "--engine",
      "postgres",
      "--default-only",
      "--query",
      "DBEngineVersions[].EngineVersion",
    ],
  ],
  [
    "instances",
    [
      "ec2",
      "describe-instance-type-offerings",
      "--filters",
      "Name=instance-type,Values=t4g.large",
      "--query",
      "InstanceTypeOfferings[].InstanceType",
    ],
  ],
  [
    "eksQuota",
    [
      "service-quotas",
      "get-service-quota",
      "--service-code",
      "eks",
      "--quota-code",
      "L-1194D53C",
      "--query",
      "Quota.{name:QuotaName,value:Value}",
    ],
  ],
]) {
  try {
    console.log(JSON.stringify({ name, data: await aws(args) }));
  } catch (e) {
    console.log(
      JSON.stringify({
        name,
        error: String(e.stderr || e.message).slice(0, 1000),
      }),
    );
  }
}
