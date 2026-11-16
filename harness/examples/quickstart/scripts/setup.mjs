import { execFileSync } from "node:child_process";
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
// Install the published SDK and its platform runtime; no Rust build is needed.
execFileSync(npm, ["ci"], { stdio: "inherit", shell: process.platform === "win32" });
