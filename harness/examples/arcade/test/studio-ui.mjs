import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { unzipSync, strFromU8 } from "fflate";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { previewUrl } from "../build/electron/preview.js";
import { until } from "./model-fixture.mjs";
const root = await mkdtemp(join(tmpdir(), "arcade-studio-ui-")),
  evidence = resolve("test-results/studio");
await mkdir(evidence, { recursive: true });
const l = new Library(root),
  s = new SettingsStore(root),
  brief = {
    idea: "An offline module game",
    kind: "Puzzle",
    vibe: "Neon",
    difficulty: "Easy",
  };
const other = l.create(s.get().gamesRoot, "Private project", brief, null);
const r = l.publishProject(
  other.id,
  {
    "index.html": "<!doctype html><html><body>Other game</body></html>",
    "private.json": '{"secret":"not accessible"}',
  },
  "other",
);
const otherUrl = new URL("private.json", previewUrl(other.id, r.id)).href;
const g = l.create(s.get().gamesRoot, "Module Garden", brief, null);
l.publishProject(
  g.id,
  {
    "index.html":
      '<!doctype html><html><head><link rel="stylesheet" href="./styles/game.css"></head><body><h1>Module Garden</h1><img alt="Star" src="./assets/star.svg"><button id="play">Loading</button><script type="module" src="./src/game.js"></script></body></html>',
    "styles/game.css":
      "body{background:#213b45;color:white;display:grid;place-content:center;height:100vh;margin:0;text-align:center;font:18px system-ui}img{width:90px;margin:20px auto}button{padding:20px;background:#c6f3cb;border:0;border-radius:20px;font:inherit}",
    "src/game.js": `import {label} from './util.js';const data=await(await fetch(new URL('../assets/config.json',import.meta.url))).json();let n=0;document.querySelector('button').textContent=label+' '+n;document.querySelector('button').onclick=()=>document.querySelector('button').textContent=label+' '+(++n);window.proof={config:data.ok,node:typeof require,bridge:typeof window.arcade,parent:false,network:false,cross:false};try{parent.document.body}catch{proof.parent=true}fetch('https://example.com').catch(()=>proof.network=true);fetch(${JSON.stringify(otherUrl)}).catch(()=>proof.cross=true);`,
    "src/util.js": "export const label='Grow';",
    "assets/config.json": '{"ok":true}',
    "assets/star.svg":
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="#fbd998" d="M50 0 62 38 100 50 62 62 50 100 38 62 0 50 38 38Z"/></svg>',
  },
  "module",
);
l.status(g.id, "ready");
l.close();
let app;
async function launch() {
  app = await electron.launch({
    ...(process.env.ARCADE_PACKAGED_APP
      ? { executablePath: process.env.ARCADE_PACKAGED_APP, args: [] }
      : { args: [resolve(".")] }),
    env: {
      ...process.env,
      ARCADE_DATA_DIR: root,
      OPENWORKER_RUNTIME: resolve("../../target/release/openworker-harness"),
    },
    timeout: 60000,
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await page
    .getByRole("heading", { name: "Moonlight Rally", exact: true, level: 1 })
    .waitFor();
  return page;
}
try {
  let page = await launch();
  await page
    .getByRole("button", { name: "Play Module Garden", exact: true })
    .click();
  const f = page.frameLocator("#game-preview");
  await f.getByAltText("Star").evaluate((image) => image.decode());
  await f.getByRole("button", { name: "Grow 0", exact: true }).click();
  await f.getByRole("button", { name: "Grow 1", exact: true }).waitFor();
  const frame = page
    .frames()
    .find((x) => x.url().startsWith("arcade-preview:"));
  await frame.waitForFunction(
    () => window.proof?.cross && window.proof?.network,
  );
  assert.deepEqual(await frame.evaluate(() => window.proof), {
    config: true,
    node: "undefined",
    bridge: "undefined",
    parent: true,
    network: true,
    cross: true,
  });
  assert.equal(await page.locator(".chat-panel").isVisible(), false);
  assert.equal(
    await page.evaluate(
      async () => (await window.arcade.action({ type: "snapshot" })).runtime,
    ),
    "idle",
  );
  assert.equal(
    await frame.evaluate(() => {
      localStorage.setItem("score-test", "42");
      sessionStorage.setItem("round", "1");
      return (
        localStorage.getItem("score-test") === "42" &&
        sessionStorage.getItem("round") === "1"
      );
    }),
    true,
  );
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  await f.getByRole("button", { name: "Grow 0", exact: true }).waitFor();
  const refreshed = page
    .frames()
    .find((x) => x.url().startsWith("arcade-preview:"));
  assert.equal(
    await refreshed.evaluate(() => localStorage.getItem("score-test")),
    null,
  );
  assert.equal(
    await f.getByAltText("Star").evaluate(async (e) => {
      await e.decode();
      return e.naturalWidth > 0;
    }),
    true,
  );
  await page.screenshot({ path: join(evidence, "play.png") });
  await page.getByRole("button", { name: "Build", exact: true }).click();
  await page.getByRole("button", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: /src\/game.js/ }).click();
  await page
    .locator(".source-view pre")
    .filter({ hasText: "import {label}" })
    .waitFor();
  await page.screenshot({ path: join(evidence, "files.png") });
  await page.getByRole("button", { name: "Close files", exact: true }).click();
  const zipPath = join(root, "project.zip");
  await app.evaluate(({ dialog }, path) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path });
  }, zipPath);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await until(async () => {
    try {
      return (await readFile(zipPath)).length > 0;
    } catch {
      return false;
    }
  }, "export ZIP");
  const files = unzipSync(await readFile(zipPath));
  assert.equal(strFromU8(files["src/util.js"]), "export const label='Grow';");
  assert.ok(files["assets/star.svg"]);
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page.screenshot({ path: join(evidence, "library.png") });
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await page.screenshot({ path: join(evidence, "create.png") });
  await page
    .getByRole("button", { name: /Try the playable 3D starter/ })
    .click();
  await f.getByRole("button", { name: /Let’s play/ }).waitFor();
  await until(
    async () =>
      !(await f.getByRole("button", { name: /Let’s play/ }).isDisabled()),
    "WebGL enabled",
  );
  await page.screenshot({ path: join(evidence, "three-intro.png") });
  await f.getByRole("button", { name: /Let’s play/ }).click();
  await f.getByRole("button", { name: "Move left", exact: true }).click();
  await f.getByRole("button", { name: "Move right", exact: true }).click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: join(evidence, "three-playing.png") });
  const three = page
    .frames()
    .find((x) => x.url().startsWith("arcade-preview:"));
  assert.equal(
    await three.evaluate(() => {
      const c = document.querySelector("canvas");
      const gl = c.getContext("webgl2");
      return !!gl && !gl.isContextLost() && c.width > 0;
    }),
    true,
  );
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Clone Orbit Runner", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await app.close();
  app = undefined;
  page = await launch();
  await page
    .getByRole("button", { name: "Play Orbit Runner copy", exact: true })
    .click();
  await page
    .frameLocator("#game-preview")
    .getByRole("button", { name: /Let’s play/ })
    .waitFor();
  console.log(
    "PASS: ES modules, local JSON/SVG/CSS, no external/cross-game/native access, offline Play, file inspector, ZIP, Three.js gameplay and clone/restart",
  );
} catch (e) {
  if (app)
    await (
      await app.firstWindow()
    ).screenshot({ path: join(evidence, "failure.png") });
  throw e;
} finally {
  await app?.close();
  await rm(root, { recursive: true, force: true });
}
