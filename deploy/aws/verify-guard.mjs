import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { connect } from "./client.mjs";
import { name } from "./templates.mjs";
import { repo } from "../helm/local.mjs";
const { aws } = await connect(),
  path = join(repo, ".ow-aws/guard-invoke.json");
const result = await aws([
  "lambda",
  "invoke",
  "--function-name",
  name + "-cleanup",
  path,
]);
if (result.FunctionError)
  throw Error("Expiry guard invocation failed; inspect private result file");
const payload = JSON.parse(await readFile(path, "utf8"));
if (payload.status !== "not_due")
  throw Error("Unexpected guard state: " + payload.status);
console.log(
  "Cloud cleanup function runs and refuses early deletion. Deadline: " +
    payload.expiry,
);
