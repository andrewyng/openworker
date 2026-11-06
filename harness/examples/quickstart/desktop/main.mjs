import { app, BrowserWindow, ipcMain, dialog } from "electron";
import { fileURLToPath, pathToFileURL } from "node:url";
import { join } from "node:path";
import { directorySnapshot } from "../dist/directory.js";
import { startHarness, createAgents, streamAnswer } from "../dist/agent.js";
const root = fileURLToPath(new URL("../", import.meta.url));
let window,
  runtime,
  selection,
  session,
  busy = false,
  quitting = false,
  cancelled = false;
app.whenReady().then(async () => {
  window = new BrowserWindow({
    width: 1080,
    height: 790,
    title: "Code Atlas",
    backgroundColor: "#f7f8fa",
    webPreferences: {
      preload: join(root, "desktop/preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  const entry = pathToFileURL(join(root, "public/desktop.html")).href;
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (url !== entry) event.preventDefault();
  });
  const check = (event) => {
    if (
      event.sender !== window.webContents ||
      event.senderFrame !== window.webContents.mainFrame
    )
      throw Error("Untrusted frame");
  };
  ipcMain.handle("summary:choose", async (event) => {
    check(event);
    if (busy) throw Error("Wait for the current summary.");
    const result = await dialog.showOpenDialog(window, {
      properties: ["openDirectory"],
    });
    if (result.canceled) return null;
    selection = await directorySnapshot(result.filePaths[0]);
    return {
      directory: result.filePaths[0],
      files: selection.files.map((f) => f.path),
      omitted: selection.omitted,
      bytes: selection.bytes,
    };
  });
  ipcMain.handle("summary:run", async (event) => {
    check(event);
    if (busy || !selection?.files.length)
      throw Error("Choose a directory with readable source files.");
    busy = true;
    cancelled = false;
    const emit = (value) => {
      if (!window.isDestroyed())
        window.webContents.send("summary:event", value);
    };
    try {
      if (!runtime) {
        if (app.isPackaged)
          process.env.OPENWORKER_EXECUTABLE = join(
            process.resourcesPath,
            "bin",
            process.platform === "win32"
              ? "openworker-harness.exe"
              : "openworker-harness",
          );
        runtime = await startHarness(join(app.getPath("userData"), "sessions"));
      }
      const { summarizer } = createAgents(runtime.harness, runtime.model);
      session = await summarizer.newSession();
      if (cancelled) throw Error("Stopped.");
      await streamAnswer(
        session,
        `Explain this source snapshot. It may omit files.\n${selection.context}`,
        emit,
      );
    } catch (error) {
      emit({ type: "error", text: error.message });
    } finally {
      busy = false;
      session = undefined;
    }
  });
  ipcMain.handle("summary:cancel", async (event) => {
    check(event);
    cancelled = true;
    await session?.cancel();
  });
  await window.loadURL(entry);
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", (event) => {
  if (runtime && !quitting) {
    event.preventDefault();
    quitting = true;
    void (async () => {
      await session?.cancel().catch(() => {});
      await runtime.harness.shutdown();
      app.quit();
    })();
  }
});
