import {
  app,
  BrowserWindow,
  ipcMain,
  protocol,
  dialog,
  session as electronSession,
} from "electron";
import { join, basename } from "node:path";
import { pathToFileURL } from "node:url";
import { Library } from "./library.js";
import { SettingsStore } from "./settings.js";
import { AgentService } from "./agent-service.js";
import { GamePlaytester } from "./playtest.js";
import { DraftReview } from "./review.js";
import { starterFiles } from "./templates.js";
import { StarterCatalog } from "./starters.js";
import { progressScope } from "./game-progress.js";
import { zipSync } from "fflate";
import { atomicWrite, readBytes } from "./files.js";
import {
  fileBytes,
  fileSize,
  textFile,
  binaryAsset,
  ASSET_LIMIT,
  PROJECT_LIMIT,
  type ProjectFiles,
} from "./project.js";
import { previewResponse, previewUrl } from "./preview.js";
import type {
  Action,
  AppEvent,
  AppSnapshot,
  ProjectView,
} from "../shared/contracts.js";

app.setName("Arcade Studio");
app.setAppUserModelId("com.openworker.arcade.studio");
if (process.env.ARCADE_DATA_DIR)
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
app.commandLine.appendSwitch("site-per-process");
let window: BrowserWindow | undefined,
  library: Library,
  settings: SettingsStore,
  closing = false;
const emit = (event: AppEvent) => {
  if (window && !window.isDestroyed())
    window.webContents.send("arcade:event", event);
};
let service: AgentService;
let starters: StarterCatalog;
const coverByRevision = new Map<string, string | undefined>();
let selectedGame: string | null = null;
const review = new DraftReview();
function snapshot(): AppSnapshot {
  return {
    starters: starters.list(),
    settings: settings.get(),
    games: library.list().map((game) => {
      if (game.revisionId && !game.thumbnail) {
        try {
          const key = `${game.id}.${game.revisionId}`;
          if (!coverByRevision.has(key)) {
            const files = library.project(game.id);
            let path = Object.hasOwn(files, "cover.svg")
              ? "cover.svg"
              : undefined;
            if (!path && typeof files["starter-origin.json"] === "string") {
              const origin = JSON.parse(files["starter-origin.json"]);
              if (
                typeof origin.cover === "string" &&
                Object.hasOwn(files, origin.cover)
              )
                path = origin.cover;
            }
            coverByRevision.set(key, path);
          }
          const path = coverByRevision.get(key);
          if (path)
            return {
              ...game,
              coverUrl: new URL(path, previewUrl(game.id, game.revisionId))
                .href,
            };
        } catch {
          /* Older/user-built games may have no cover. */
        }
      }
      return game;
    }),
    activeGameId: service?.activeGameId ?? null,
    runtime: service?.snapshot() ?? "idle",
  };
}
function offlineView(id: string): ProjectView {
  library.repair(id);
  const game = library.get(id);
  return {
    game,
    files: library.fileList(id),
    messages: [],
    partial: "",
    activity: [],
    pending: [],
    active: false,
    stage: game.revisionId ? "Ready to play" : "Ready to build",
    revisions: library.revisions(id),
    previewUrl: game.revisionId ? previewUrl(id, game.revisionId) : null,
  };
}
app
  .whenReady()
  .then(async () => {
    if (!app.requestSingleInstanceLock()) {
      app.quit();
      return;
    }
    app.on("second-instance", () => {
      window?.show();
      window?.focus();
    });
    settings = new SettingsStore(app.getPath("userData"));
    library = new Library(app.getPath("userData"));
    starters = new StarterCatalog(join(app.getAppPath(), "starter-games"));
    const helper =
      process.platform === "win32"
        ? "openworker-harness.exe"
        : "openworker-harness";
    service = new AgentService(library, settings, {
      executable: app.isPackaged
        ? join(process.resourcesPath, helper)
        : (process.env.OPENWORKER_RUNTIME ??
          join(app.getAppPath(), "bin", helper)),
      emit,
      playtester: new GamePlaytester(library),
    });
    protocol.handle("arcade-preview", (request) =>
      new URL(request.url).hostname.startsWith("review-")
        ? review.response(request.url)
        : previewResponse(library, request.url, starters),
    );
    electronSession.defaultSession.setPermissionRequestHandler(
      (_webContents, _permission, callback) => callback(false),
    );
    electronSession.defaultSession.setPermissionCheckHandler(() => false);
    window = new BrowserWindow({
      width: 1390,
      height: 900,
      minWidth: 880,
      minHeight: 640,
      title: "Arcade",
      backgroundColor: "#101218",
      webPreferences: {
        preload: join(app.getAppPath(), "electron/preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webviewTag: false,
      },
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    // A hidden playtest window must never keep the app alive after the user quits.
    window.on("closed", () => app.quit());
    const entry = app.isPackaged
      ? pathToFileURL(join(app.getAppPath(), "dist/index.html")).href
      : (process.env.ARCADE_DEV_URL ??
        pathToFileURL(join(app.getAppPath(), "dist/index.html")).href);
    window.webContents.on("will-navigate", (event, url) => {
      if (url !== entry) event.preventDefault();
    });
    let progressCount = 0,
      progressWindow = Date.now();
    ipcMain.handle(
      "arcade:progress",
      (event, url: unknown, request: unknown) => {
        if (
          !window ||
          event.sender !== window.webContents ||
          event.senderFrame !== window.webContents.mainFrame ||
          typeof url !== "string" ||
          url.length > 300
        )
          throw new Error("Untrusted save request.");
        if (Date.now() - progressWindow > 10000) {
          progressCount = 0;
          progressWindow = Date.now();
        }
        if (++progressCount > 120)
          throw new Error("Save request rate exceeded.");
        const { scope, revision } = progressScope(url, library, starters);
        return library.progress.request(scope, revision, request);
      },
    );
    ipcMain.handle("arcade:action", async (event, input: Action) => {
      if (
        !window ||
        event.sender !== window.webContents ||
        event.senderFrame !== window.webContents.mainFrame ||
        !input ||
        typeof input !== "object"
      )
        throw new Error("Untrusted request.");
      try {
        switch (input.type) {
          case "snapshot":
            return snapshot();
          case "settings.save":
            await service.configure(input.provider, input.apiKey);
            return snapshot();
          case "provider.test":
            await service.testProvider();
            return { connected: true };
          case "settings.directory": {
            const selected = await dialog.showOpenDialog(window, {
              title: "Choose where to keep new games",
              defaultPath: settings.get().gamesRoot,
              properties: ["openDirectory", "createDirectory"],
            });
            if (!selected.canceled && selected.filePaths[0])
              settings.gamesRoot(selected.filePaths[0]);
            return snapshot();
          }
          case "game.open":
            review.close();
            selectedGame = input.id;
            return service.open(input.id);
          case "game.play":
            review.close();
            selectedGame = input.id;
            return offlineView(input.id);
          case "game.review": {
            if (selectedGame !== input.id)
              throw Error("Open this game before comparing an update.");
            const digest = await service.reviewDigest(
              input.id,
              input.interactionId,
            );
            return review.open(library, input.id, digest);
          }
          case "game.template": {
            const game = library.create(
              settings.get().gamesRoot,
              "Orbit Runner",
              {
                idea: "A 3D runner. Collect gold rings and dodge coral blocks.",
                kind: "3D arcade",
                vibe: "Cosmic",
                difficulty: "A little challenge",
                engine: "three",
              },
              null,
            );
            library.publishProject(
              game.id,
              starterFiles("three"),
              `template:${game.id}`,
              "template",
            );
            library.status(game.id, "ready");
            selectedGame = game.id;
            emit({ type: "changed" });
            return offlineView(game.id);
          }
          case "starter.remix": {
            const game = starters.remix(
              input.id,
              library,
              settings.get().gamesRoot,
            );
            selectedGame = game.id;
            emit({ type: "changed" });
            return offlineView(game.id);
          }
          case "window.fullscreen":
            window.setFullScreen(!window.isFullScreen());
            return { fullscreen: window.isFullScreen() };
          case "game.file": {
            const game = library.get(input.id);
            if (!game.revisionId)
              throw new Error("Publish a version to inspect its files.");
            const text = library.asset(game.id, game.revisionId, input.path);
            if (typeof text !== "string")
              return {
                text: `Binary asset · ${fileSize(text).toLocaleString()} bytes\nUse this relative path in game code: ${input.path}\nIncluded in this revision and full-project export.`,
                truncated: false,
              };
            return {
              text: text.slice(0, 50_000),
              truncated: text.length > 50_000,
            };
          }
          case "game.import-assets": {
            library.get(input.id);
            if (service.activeGameId)
              throw new Error(
                "Finish or cancel the active build before importing assets.",
              );
            const choice = await dialog.showOpenDialog(window, {
              title: "Import assets into this game's draft",
              properties: ["openFile", "multiSelections"],
              filters: [
                {
                  name: "Game assets",
                  extensions: [
                    "png",
                    "webp",
                    "jpg",
                    "jpeg",
                    "wav",
                    "ogg",
                    "glb",
                  ],
                },
              ],
            });
            if (choice.canceled) return { paths: [] };
            if (choice.filePaths.length > 16)
              throw new Error("Import at most 16 assets at a time.");
            if (service.activeGameId)
              throw new Error(
                "A build started while the picker was open. Import after it finishes.",
              );
            const files: ProjectFiles = Object.create(null);
            let total = 0;
            for (const path of choice.filePaths) {
              const name = basename(path).replace(/[^a-zA-Z0-9_.-]/g, "-");
              const dest = `assets/imported/${name}`;
              if (Object.hasOwn(files, dest))
                throw new Error("Selected files have conflicting names.");
              const bytes = readBytes(path, ASSET_LIMIT);
              total += bytes.length;
              if (total > PROJECT_LIMIT)
                throw new Error(
                  "Selected assets exceed the project size limit.",
                );
              files[dest] = binaryAsset(dest, bytes);
            }
            library.importAssets(input.id, files);
            return { paths: Object.keys(files) };
          }
          case "game.duplicate": {
            const game = library.duplicate(input.id, settings.get().gamesRoot);
            selectedGame = game.id;
            emit({ type: "changed" });
            return offlineView(game.id);
          }
          case "game.restore":
            return service.restore(input.id, input.revisionId);
          case "game.export": {
            const game = library.get(input.id);
            const files = library.project(game.id),
              single = Object.keys(files).length === 1;
            const selected = await dialog.showSaveDialog(window, {
              title: "Export game",
              defaultPath: `${game.title.replace(/[^a-zA-Z0-9 -]/g, "") || "game"}.${single ? "html" : "zip"}`,
              filters: [
                {
                  name: single ? "HTML game" : "Game project",
                  extensions: [single ? "html" : "zip"],
                },
              ],
              properties: ["showOverwriteConfirmation", "createDirectory"],
            });
            if (!selected.canceled && selected.filePath)
              atomicWrite(
                selected.filePath,
                single
                  ? textFile(files["index.html"])
                  : Buffer.from(
                      zipSync(
                        Object.fromEntries(
                          Object.entries(files).map(([path, text]) => [
                            path,
                            fileBytes(text),
                          ]),
                        ),
                        { level: 6 },
                      ),
                    ),
              );
            return { exported: !selected.canceled };
          }
          case "game.thumbnail": {
            if (
              selectedGame !== input.id ||
              library.get(input.id).revisionId !== input.revisionId
            )
              return { saved: false };
            const expected = previewUrl(input.id, input.revisionId);
            const rect = await window.webContents.executeJavaScript(
              `(()=>{const el=document.getElementById('game-preview');if(document.querySelector('.overlay,.versions-panel,.files-panel')||!el||el.getAttribute('src')!==${JSON.stringify(expected)})return null;const r=el.getBoundingClientRect();if(r.right>innerWidth||r.bottom>innerHeight)return null;return {x:Math.ceil(r.x),y:Math.ceil(r.y),width:Math.floor(r.width),height:Math.floor(r.height)};})()`,
            );
            if (
              !rect ||
              rect.width < 1 ||
              rect.height < 1 ||
              rect.x < 0 ||
              rect.y < 0
            )
              return { saved: false };
            const image = await window.webContents.capturePage(rect);
            if (
              await window.webContents.executeJavaScript(
                "!!document.querySelector('.overlay,.versions-panel,.files-panel')",
              )
            )
              return { saved: false };
            if (
              selectedGame !== input.id ||
              library.get(input.id).revisionId !== input.revisionId
            )
              return { saved: false };
            library.thumbnail(
              input.id,
              input.revisionId,
              image.resize({ width: 600 }).toPNG(),
            );
            emit({ type: "changed" });
            return { saved: true };
          }
          case "game.create": {
            const view = await service.create(input.brief, input.title);
            selectedGame = view.game.id;
            return view;
          }
          case "game.send":
            await service.send(input.id, input.text);
            return { accepted: true };
          case "game.respond":
            await service.respond(
              input.id,
              input.interactionId,
              input.decision,
              input.answer,
            );
            review.close();
            return { accepted: true };
          case "game.cancel":
            await service.cancel(input.id);
            return { accepted: true };
          default:
            throw new Error("This action is not available yet.");
        }
      } catch (error) {
        throw new Error(
          error instanceof Error
            ? error.message
            : "The action could not finish.",
        );
      }
    });
    await window.loadURL(entry);
    app.on("activate", () => {
      window?.show();
      window?.focus();
    });
    app.on("window-all-closed", () => app.quit());
    app.on("before-quit", (event) => {
      if (closing) return;
      event.preventDefault();
      closing = true;
      void Promise.resolve(service?.close())
        .catch(() => {})
        .finally(() => {
          library.close();
          app.quit();
        });
    });
  })
  .catch(() => {
    dialog.showErrorBox(
      "Arcade could not start",
      "Your saved games have been preserved. Check the application data directory and try again.",
    );
    app.exit(1);
  });
