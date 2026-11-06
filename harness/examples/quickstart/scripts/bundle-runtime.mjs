import { resolveRuntimeExecutable } from "@openworkerai/harness-sdk";
import { mkdir, copyFile, chmod } from "node:fs/promises";
import { join } from "node:path";
const source = process.env.OPENWORKER_EXECUTABLE || resolveRuntimeExecutable();
const target = join(
  "bin",
  process.platform === "win32"
    ? "openworker-harness.exe"
    : "openworker-harness",
);
await mkdir("bin", { recursive: true });
await copyFile(source, target);
await chmod(target, 0o755);
