import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
const runtime = resolve(
  "bin",
  process.platform === "win32"
    ? "openworker-harness.exe"
    : "openworker-harness",
);
if (!existsSync(runtime))
  throw new Error(
    "Run npm run runtime once before bundling a development app.",
  );
const args = [
  resolve("node_modules/electron-builder/cli.js"),
  "--dir",
  "--publish",
  "never",
  `-c.directories.output=${process.argv[2] ?? "release-dev"}`,
];
if (!/^release-[a-z0-9-]+$/.test(process.argv[2] ?? "release-dev"))
  throw new Error(
    "Use a release-<name> directory inside Arcade for a development build.",
  );
if (process.platform === "darwin")
  args.push("-c.mac.identity=-", "-c.mac.notarize=false");
if(process.platform === "win32") {
  const arch=process.env.ARCADE_TARGET_ARCH??process.arch;
  if(!["x64","arm64"].includes(arch))throw Error("Choose x64 or arm64 for the development app");
  args.push("--win",`--${arch}`,"-c.win.signAndEditExecutable=false");
}
const buildEnv = { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: "false" };
for (const key of [
  "CSC_LINK",
  "WIN_CSC_LINK",
  "CSC_KEY_PASSWORD",
  "WIN_CSC_KEY_PASSWORD",
])
  delete buildEnv[key];
const result = spawnSync(process.execPath, args, {
  stdio: "inherit",
  env: buildEnv,
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
