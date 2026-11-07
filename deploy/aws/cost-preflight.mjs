// Read-only published On-Demand rates; not a billing cap or complete invoice.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { connect, account, region } from "./client.mjs";
import { repo } from "../helm/local.mjs";
const exec = promisify(execFile),
  { env } = await connect();
async function prices(service, filters) {
  const { stdout } = await exec(
    "aws",
    [
      "pricing",
      "get-products",
      "--service-code",
      service,
      "--filters",
      JSON.stringify(
        Object.entries(filters).map(([Field, Value]) => ({
          Type: "TERM_MATCH",
          Field,
          Value,
        })),
      ),
      "--region",
      "us-east-1",
      "--output",
      "json",
      "--no-cli-pager",
    ],
    { env, timeout: 60000, maxBuffer: 4 * 1024 * 1024 },
  );
  return JSON.parse(stdout).PriceList.flatMap((s) => {
    const p = JSON.parse(s);
    return Object.values(p.terms.OnDemand || {})
      .flatMap((t) => Object.values(t.priceDimensions))
      .map((d) => ({
        sku: p.product.sku,
        description: d.description,
        unit: d.unit,
        usd: Number(d.pricePerUnit.USD),
      }));
  });
}
const ec2 = await prices("AmazonEC2", {
  regionCode: region,
  instanceType: "t4g.large",
  operatingSystem: "Linux",
  tenancy: "Shared",
  preInstalledSw: "NA",
  capacitystatus: "Used",
});
const rds = await prices("AmazonRDS", {
  regionCode: region,
  instanceType: "db.t4g.micro",
  databaseEngine: "PostgreSQL",
  deploymentOption: "Single-AZ",
});
const hourly = (rows) => {
  const r = rows.filter((r) => r.unit === "Hrs" && r.usd > 0);
  if (r.length !== 1) throw Error("Ambiguous hourly price");
  return r[0].usd;
};
const base = 2 * hourly(ec2) + hourly(rds) + 0.1;
const report = {
  account,
  region,
  observed_at: new Date().toISOString(),
  ec2,
  rds,
  eks_standard_per_hour: 0.1,
  eks_source: "https://aws.amazon.com/eks/pricing/",
  fixed_compute_database_cluster_hourly: base,
  twenty_four_hour_base: 24 * base,
  planning_allowance_usd: 35,
  budget_usd: 100,
  limits:
    "Allowance is an estimate, not an enforced cap. Additional storage, IPv4, requests, image transfer, logging and burst credits accrue. No NAT/public load balancer/autoscaler. Fixed 24-hour cleanup deadline; early teardown after qualification.",
};
if (24 * base > 25)
  throw Error("Base costs changed; review capacity before launch");
await writeFile(
  join(repo, ".ow-aws/cost-preflight.json"),
  JSON.stringify(report, null, 2),
  { mode: 0o600 },
);
console.log(JSON.stringify(report, null, 2));
