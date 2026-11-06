import { app, BrowserWindow, ipcMain, protocol } from "electron";
import { Harness } from "@openworker/harness";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { readFile, writeFile, mkdir, rename } from "node:fs/promises";
import { recoverProject } from "./recovery.mjs";
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
let harness,
  window,
  session,
  agent,
  closing = false,
  busy = false;
let previewHtml = "";
let previewVersion = 0;
protocol.registerSchemesAsPrivileged([
  { scheme: "arcade-preview", privileges: { standard: true, secure: true } },
]);
app.commandLine.appendSwitch("site-per-process");
const previewPolicy =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'";
const emit = (event) => {
  if (window && !window.isDestroyed())
    window.webContents.send("arcade:event", event);
};
const fail = () => {
  busy = false;
  emit({
    type: "error",
    message:
      "The action could not finish. Check model settings or resume the saved session; do not repeat uncertain actions.",
  });
};
const refPath = () => join(app.getPath("userData"), "session.json");
const preferencesPath = () =>
  join(app.getPath("userData"), "model-settings.json");
async function refreshProject() {
  let preferences = null;
  try {
    preferences = JSON.parse(await readFile(preferencesPath(), "utf8"));
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  const view = await recoverProject(harness, session, preferences);
  if (previewHtml !== view.html) previewVersion++;
  previewHtml = view.html;
  emit({
    type: "project",
    messages: view.messages,
    settings: view.settings,
    url: previewHtml
      ? `arcade-preview://localhost/index.html?v=${previewVersion}`
      : "",
  });
}
async function save() {
  await mkdir(app.getPath("userData"), { recursive: true, mode: 0o700 });
  const temp = refPath() + ".tmp";
  await writeFile(temp, JSON.stringify(session.reference()), { mode: 0o600 });
  await rename(temp, refPath());
}
async function stream(run) {
  try {
    for await (const event of run.events()) {
      const summary = event.summary;
      if (summary?.type === "tool_started")
        emit({ type: "activity", message: "Updating your game…" });
      if (summary?.type === "message")
        emit({
          type: "activity",
          message: "Putting the finishing touches on…",
        });
    }
    const result = await run.result();
    await refreshProject();
    emit({ type: "result", result });
  } catch {
    fail();
  } finally {
    busy = false;
  }
}
// Do not await whenReady at module top level: Electron waits for ESM loading.
app
  .whenReady()
  .then(async () => {
    protocol.handle("arcade-preview", (request) => {
      const url = new URL(request.url);
      if (url.hostname !== "localhost" || url.pathname !== "/index.html")
        return new Response("Not found", { status: 404 });
      return new Response(previewHtml, {
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Content-Security-Policy": previewPolicy,
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        },
      });
    });
    const config = JSON.parse(await readFile(join(root, "app.json"), "utf8"));
    // This starter exposes only trusted app callbacks, outside an OS sandbox.
    // Keep approval separate. Opt into OpenShell explicitly for isolated tool runners.
    harness = await Harness.start({
      appId: config.appId,
      sandbox: "off",
    });
    await harness.registerTools({
      namespace: "game",
      tools: {
        show_preview: {
          description:
            "Display a self-contained playable HTML game in the isolated preview.",
          inputSchema: {
            type: "object",
            properties: { html: { type: "string", maxLength: 100000 } },
            required: ["html"],
            additionalProperties: false,
          },
          execute: async ({ html }, context) => {
            if (context.signal.aborted) throw Error("Cancelled");
            if (typeof html !== "string") throw Error("Expected HTML");
            previewHtml = html;
            emit({
              type: "preview",
              url: `arcade-preview://localhost/index.html?v=${++previewVersion}`,
            });
            return { shown: true };
          },
        },
      },
    });
    agent = harness.createAgent({
      name: "arcade-builder-v1",
      instructions:
        "Build playful, polished self-contained HTML/CSS/JavaScript games. Call game.show_preview with the complete HTML. No external assets or network calls: use inline CSS, JavaScript and canvas. Iterate on the same game when asked. Do not claim it was shown unless the tool succeeds.",
      tools: ["game.show_preview"],
    });
    await agent.ready();
    try {
      const saved = JSON.parse(await readFile(refPath(), "utf8"));
      if (saved) session = await agent.resumeSession(saved);
    } catch (error) {
      if (error.code !== "ENOENT")
        throw Error(
          "Saved session could not be restored; preserve it and inspect before starting another project.",
        );
    }
    window = new BrowserWindow({
      width: 1280,
      height: 850,
      minWidth: 820,
      minHeight: 640,
      title: "Arcade",
      backgroundColor: "#f5f6f8",
      webPreferences: {
        preload: join(root, "electron/preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    const entry = app.isPackaged
      ? pathToFileURL(join(root, "dist/index.html")).href
      : process.env.ARCADE_DEV_URL;
    if (!entry) throw Error("Use npm run dev");
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      if (url !== entry) event.preventDefault();
    });
    ipcMain.handle("arcade:action", async (event, input) => {
      if (
        event.sender !== window.webContents ||
        event.senderFrame !== window.webContents.mainFrame
      )
        throw Error("Untrusted frame");
      if (!input || typeof input !== "object") throw Error("Invalid action");
      switch (input.type) {
        case "restore": {
          await refreshProject();
          if (session && !busy && (await session.state()).runId) {
            busy = true;
            void session
              .resume()
              .then(async (result) => {
                await refreshProject();
                emit({ type: "result", result });
              })
              .catch(fail)
              .finally(() => {
                busy = false;
              });
          }
          return { attached: !!session };
        }
        case "provider":
          await harness.setModelProvider(input.provider);
          await mkdir(app.getPath("userData"), {
            recursive: true,
            mode: 0o700,
          });
          await writeFile(
            preferencesPath() + ".tmp",
            JSON.stringify({
              provider: input.provider.name,
              api: input.provider.api,
              baseUrl: input.provider.baseUrl,
              model: input.model ?? "",
            }),
            { mode: 0o600 },
          );
          await rename(preferencesPath() + ".tmp", preferencesPath());
          return { saved: true };
        case "send": {
          if (busy) throw Error("Wait for this run");
          if (typeof input.text !== "string" || input.text.length > 32000)
            throw Error("Invalid message");
          busy = true;
          try {
            if (!session) {
              session = await agent.newSession({ model: input.model });
              await save();
            }
            const run = await session.send(input.text);
            void stream(run);
            return { accepted: true };
          } catch (error) {
            busy = false;
            throw error;
          }
        }
        case "respond": {
          if (!session || busy) throw Error("No waiting session");
          await session.send(
            input.interaction.kind === "approval"
              ? {
                  type: "approval",
                  interactionId: input.interaction.id,
                  targetSessionId: input.interaction.session_id,
                  decision: input.decision,
                }
              : {
                  type: "answer",
                  interactionId: input.interaction.id,
                  targetSessionId: input.interaction.session_id,
                  text: input.answer,
                },
          );
          busy = true;
          void (async () => {
            try {
              const resumed = await session.resumeStreamed();
              for await (const _ of resumed) {
              }
              const result = await resumed.result();
              await refreshProject();
              emit({ type: "result", result });
            } catch {
              fail();
            } finally {
              busy = false;
            }
          })();
          return { accepted: true };
        }
        case "cancel":
          if (session) await session.cancel();
          return { cancelled: true };
        case "new":
          if (busy) throw Error("Stop the current run first");
          if (session) await session.cancel();
          session = undefined;
          await writeFile(refPath() + ".tmp", "null", { mode: 0o600 });
          await rename(refPath() + ".tmp", refPath());
          await refreshProject();
          return { ready: true };
        default:
          throw Error("Unsupported app action");
      }
    });
    await window.loadURL(entry);
    app.on("window-all-closed", () => app.quit());
    app.on("before-quit", (event) => {
      if (closing) return;
      event.preventDefault();
      closing = true;
      void harness.shutdown().finally(() => app.quit());
    });
  })
  .catch(async () => {
    console.error(
      "Arcade startup failed. Check the bundled runtime and app state configuration.",
    );
    if (harness) await harness.shutdown().catch(() => {});
    app.exit(1);
  });
