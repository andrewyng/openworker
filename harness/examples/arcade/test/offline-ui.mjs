import assert from "node:assert/strict";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
const root = await mkdtemp(join(tmpdir(), "arcade-offline-ui-"));
const evidence = resolve("test-results/offline");
await mkdir(evidence, { recursive: true });
const html = (name, color) =>
  `<!doctype html><html><head><style>body{margin:0;background:${color};color:#fff;font:18px system-ui;display:grid;place-content:center;text-align:center;height:100vh}h1{font-size:42px;letter-spacing:-2px}button{border:1px solid #ffffff50;background:#ffffff20;border-radius:18px;padding:18px 30px;color:inherit;font:inherit;cursor:pointer}p{opacity:.65}</style></head><body><h1>${name}</h1><p>A tiny playable test game</p><button id="play">Catch a star · 0</button><script>let score=0;document.querySelector('button').onclick=()=>document.querySelector('button').textContent='Catch a star · '+(++score);window.proof={bridge:typeof window.arcade,node:typeof require,parentBlocked:false,networkBlocked:false};try{void parent.document.body}catch{window.proof.parentBlocked=true}fetch('https://example.com').catch(()=>window.proof.networkBlocked=true);</script></body></html>`;
const brief = {
  idea: "Catch falling stars",
  kind: "Arcade",
  vibe: "Neon",
  difficulty: "Relaxed",
};
let app;
const launch = async () => {
  app = await electron.launch({
    args: [resolve(".")],
    env: { ...process.env, ARCADE_DATA_DIR: root },
    timeout: 60000,
  });
  const page = await app.firstWindow();
  await page
    .getByRole("heading", { name: "Moonlight Rally", exact: true, level: 1 })
    .waitFor();
  return page;
};
try {
  let page = await launch();
  await page.screenshot({ path: join(evidence, "empty-library.png") });
  await app.close();
  app = undefined;
  const settings = new SettingsStore(root),
    library = new Library(root);
  for (const [name, color] of [
    ["Neon Catch", "#23243f"],
    ["Cloud Garden", "#476d68"],
  ]) {
    const g = library.create(settings.get().gamesRoot, name, brief, null);
    library.publish(g.id, html(name, color), `fixture:${g.id}`);
    library.status(g.id, "ready");
  }
  library.close();
  page = await launch();
  await page
    .getByRole("button", { name: "Play Neon Catch", exact: true })
    .waitFor();
  await page.screenshot({ path: join(evidence, "library.png") });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Storage", exact: true }).click();
  await page.screenshot({ path: join(evidence, "storage-settings.png") });
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page
    .getByRole("button", { name: "Play Neon Catch", exact: true })
    .click();
  const frame = page.frameLocator("#game-preview");
  await frame
    .getByRole("button", { name: "Catch a star · 0", exact: true })
    .click();
  await frame
    .getByRole("button", { name: "Catch a star · 1", exact: true })
    .waitFor();
  const content = page
    .frames()
    .find((f) => f.url().startsWith("arcade-preview:"));
  await content.waitForFunction(() => window.proof?.networkBlocked);
  assert.deepEqual(await content.evaluate(() => window.proof), {
    bridge: "undefined",
    node: "undefined",
    parentBlocked: true,
    networkBlocked: true,
  });
  await page.screenshot({ path: join(evidence, "play.png") });
  await page.getByRole("button", { name: "Restart game", exact: true }).click();
  await frame
    .getByRole("button", { name: "Catch a star · 0", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Back to games", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Play Cloud Garden", exact: true })
    .click();
  await frame
    .getByRole("heading", { name: "Cloud Garden", exact: true })
    .waitFor();
  await app.close();
  app = undefined;
  page = await launch();
  await page
    .getByRole("button", { name: "Play Neon Catch", exact: true })
    .click();
  await page
    .frameLocator("#game-preview")
    .getByRole("button", { name: "Catch a star · 0", exact: true })
    .waitFor();
  console.log(
    "PASS: offline library, two games, full app restart, gameplay, preview isolation and restart button",
  );
  console.log(`Screenshots: ${evidence}`);
} finally {
  if (app) await app.close();
  await rm(root, { recursive: true, force: true });
}
