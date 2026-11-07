// Isolated Electron test host. Never loaded by the shipped main process.
import { app, protocol } from "electron";
import { Library } from "../build/electron/library.js";
import { GamePlaytester } from "../build/electron/playtest.js";
import { AgentService } from "../build/electron/agent-service.js";
import { SettingsStore } from "../build/electron/settings.js";
import { StarterCatalog } from "../build/electron/starters.js";
import { readFileSync, writeFileSync } from "node:fs";
app.setPath("userData", process.env.ARCADE_DATA_DIR);
protocol.registerSchemesAsPrivileged([
  {
    scheme: "arcade-preview",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
]);
app.whenReady().then(() => {
  const library = new Library(process.env.ARCADE_DATA_DIR);
  globalThis.previewTest = {
    library,
    manager: new GamePlaytester(library),
    dependencies: {
      AgentService,
      SettingsStore,
      StarterCatalog,
      readFileSync,
      writeFileSync,
    },
  };
});
app.on("window-all-closed", () => {});
