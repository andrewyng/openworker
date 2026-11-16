import { spawn } from "node:child_process";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const run = (args) =>
  new Promise((resolve, reject) => {
    const p = spawn(npm, args, {
      stdio: "inherit",
      shell: process.platform === "win32",
    });
    p.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error("Build failed")),
    );
  });
await run(["run", "sdk"]);
await run(["run", "build:main"]);
const vite = spawn(process.execPath, ["node_modules/vite/bin/vite.js"], {
  stdio: "inherit",
});
let electron;
const close = () => {
  electron?.kill();
  vite.kill();
};
process.on("SIGINT", close);
process.on("SIGTERM", close);
try {
  for (let n = 0; n < 100; n++) {
    try {
      if ((await fetch("http://127.0.0.1:5184")).ok) break;
    } catch {}
    if (n === 99) throw new Error("The app UI did not start.");
    await new Promise((r) => setTimeout(r, 100));
  }
  electron = spawn(require("electron"), ["."], {
    stdio: "inherit",
    env: { ...process.env, ARCADE_DEV_URL: "http://127.0.0.1:5184" },
  });
  electron.on("exit", (code) => {
    vite.kill();
    process.exitCode = code ?? 0;
  });
} catch (e) {
  close();
  throw e;
}
