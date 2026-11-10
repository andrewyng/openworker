import { execFileSync } from "node:child_process";
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
for (const args of [
  ["--prefix", "../../typescript-sdk", "install"],
  ["--prefix", "../../typescript-sdk", "run", "build"],
  ["install"],
])
  execFileSync(npm, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
  });
