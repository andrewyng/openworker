// Aggregate only completed evidence from this private, still-active experiment.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { root, context, save } from "./qualification-lib.mjs";
const m = await context(),
  reports = {};
const plan = JSON.parse(await readFile(join(root, "plan.json"), "utf8"));
const fresh = (report) =>
  assert.ok(
    Date.parse(report.time) >= Date.parse(plan.created),
    "Evidence predates this experiment",
  );
for (const area of [
  "http",
  "operations",
  "restore",
  "telemetry",
  "ui",
  "live",
]) {
  const file = `current-${area}-qualification.json`;
  const report = JSON.parse(await readFile(join(root, file), "utf8"));
  assert.equal(report.status, "completed", area);
  fresh(report);
  assert.ok(report.checks.length > 0);
  reports[area] = { file, checks: report.checks, limits: report.limits || [] };
}
const network = JSON.parse(
  await readFile(join(root, "network-proof.json"), "utf8"),
);
assert.equal(network.checks.length, 5);
fresh(network);
const build = JSON.parse(
  await readFile(join(root, "build-evidence.json"), "utf8"),
);
const images = JSON.parse(await readFile(join(root, "images.json"), "utf8"));
await save("current-qualification.json", {
  status: "completed",
  time: new Date().toISOString(),
  experiment: m.context,
  reports,
  network,
  build,
  images,
  teardown: "Required; inspect final-removal-audit.json separately",
  limits: [
    "Disposable two-node ARM64 EKS reference; no production SLA or tenant-isolation certification",
    "Four concurrent fixture sessions are correctness evidence, not throughput sizing",
    "Cloud rollback changes coworker bundle identity, not arbitrary native binaries",
    "Logical RDS restore retains original S3; no independent disaster recovery or sandbox-volume restoration",
    "Cloud collector export verified; production Grafana/alert routing not deployed",
  ],
});
console.log(
  "All required application qualification gates passed. Teardown and final resource audit remain required.",
);
