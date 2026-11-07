import { spawn } from "node:child_process";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
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
  for (let i = 0; i < 100; i++) {
    try {
      const response = await fetch("http://127.0.0.1:5173");
      if (response.ok) break;
    } catch {}
    if (i === 99) throw Error("Vite did not start");
    await new Promise((r) => setTimeout(r, 100));
  }
  electron = spawn(require("electron"), ["."], {
    stdio: "inherit",
    env: { ...process.env, ARCADE_DEV_URL: "http://127.0.0.1:5173" },
  });
  electron.on("exit", (code) => {
    vite.kill();
    process.exitCode = code ?? 0;
  });
} catch (error) {
  close();
  throw error;
}
