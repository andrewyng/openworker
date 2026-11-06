import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// The standalone server writes a fresh token to `sidecar-8765.token` on every launch, so the
// browser dev GUI reads it on each page load instead of once when vite starts: after a server
// restart (or a vite that started first), the signed-out gate's Reload picks up the current
// token. It sets `__COWORKER_DEV_TOKEN__`, the lowest-precedence token in api.ts, never
// `__COWORKER_API_TOKEN__`, which cloudAuth reads as "the shell injected one, skip login".
function devTokenScript(): Plugin {
  const state =
    process.env.COWORKER_STATE_DIR ||
    (process.platform === "win32"
      ? path.join(process.env.APPDATA || os.homedir(), "coworker")
      : path.join(os.homedir(), ".config", "coworker"));
  return {
    name: "coworker-dev-token",
    apply: "serve",
    transformIndexHtml() {
      let token = "";
      try {
        token = fs.readFileSync(path.join(state, "sidecar-8765.token"), "utf8").trim();
      } catch {
        // The Tauri dev shell injects its in-memory token at runtime. Plain browser dev
        // shows the normal startup retry until the standalone server/token file exists.
      }
      if (!token) return [];
      // `<` escaped so the value can never close the inline script.
      const value = JSON.stringify(token).replace(/</g, "\\u003c");
      return [{ tag: "script", children: `globalThis.__COWORKER_DEV_TOKEN__=${value};`, injectTo: "head-prepend" }];
    },
  };
}

// `base: "./"` makes built asset URLs relative, so the bundle loads from the `tauri://`
// origin in the desktop shell (absolute `/assets` 404s there); a server-hosted build is
// unaffected. Dev runs on a fixed port (1420) with strictPort so the Tauri webview always
// loads the vite instance Tauri itself spawns (a drifting port would make the window load a
// stale/other server). `tauri.conf.json` devUrl must match this.
export default defineConfig(() => {
  // COWORKER_DEV_PROXY=http://127.0.0.1:8790 points the dev GUI at a hosted-
  // shaped backend (the machines service) through vite's own origin, so the
  // browser sees no cross-origin calls — the hosted service serves its bundle
  // same-origin and deliberately has no CORS. Pair it with
  // VITE_COWORKER_HTTP/WS set to the vite origin. Off unless set.
  const devProxy = process.env.COWORKER_DEV_PROXY || "";
  const proxy = devProxy
    ? {
        "/v1": { target: devProxy, changeOrigin: true },
        "/ws": { target: devProxy.replace(/^http/, "ws"), ws: true, changeOrigin: true },
        "/j": { target: devProxy, changeOrigin: true },
      }
    : undefined;
  return {
    base: "./",
    plugins: [react(), devTokenScript()],
    server: { port: 1420, strictPort: true, ...(proxy ? { proxy } : {}) },
    // Tauri CLI looks for these; harmless for the browser build.
    clearScreen: false,
    envPrefix: ["VITE_", "TAURI_"],
  };
});
