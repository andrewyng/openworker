// Keep every SDK integration target in CI without one serial 40-minute job.
// New targets default to core; each job owns its own PostgreSQL service.
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const groups = {
  teams: ["agent_team", "storage_teams", "team_types"],
  workspaces: ["team_workspaces", "coding_presets", "dynamic_agents"],
};
const root = fileURLToPath(new URL("../", import.meta.url));
const targets = readdirSync(new URL("../harness-sdk/tests/", import.meta.url))
  .filter((name) => name.endsWith(".rs"))
  .map((name) => name.slice(0, -3)).sort();
for (const name of Object.values(groups).flat()) {
  if (!targets.includes(name)) throw Error(`Missing SDK test target: ${name}`);
}
groups.core = targets.filter((name) => !Object.values(groups).flat().includes(name));
const shard = process.argv[2];
if (!Object.hasOwn(groups, shard)) throw Error("Expected core, teams or workspaces");
if (process.argv.includes("--list")) {
  console.log(JSON.stringify(groups[shard]));
} else {
  if (!process.env.OW_PG_TEST_URL) throw Error("OW_PG_TEST_URL is required: do not silently skip storage coverage");
  const run = (args) => {
    const result = spawnSync("cargo", args, { cwd: root, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exit(result.status ?? 1);
  };
  run(["test", "--locked", "-p", "openworker-sdk", "--features", "test-support",
    ...(shard === "core" ? ["--lib", "--bins"] : []),
    ...groups[shard].flatMap((name) => ["--test", name]),
    "--", "--include-ignored", "--test-threads=1"]);
  if (shard === "core") run(["test", "--locked", "-p", "openworker-sdk", "--doc"]);
}
