import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { createServer } from "node:http";
const root = await mkdtemp(join(tmpdir(), "arcade-playtest-")),
  evidence = resolve("test-results/playtest");
await mkdir(evidence, { recursive: true });
let hits = 0;
const server = createServer((_req, res) => {
  hits++;
  res.end("should not reach network");
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const remote = `http://127.0.0.1:${server.address().port}`;
const app = await electron.launch({
  args: [resolve("test/preview-host.mjs")],
  env: { ...process.env, ARCADE_DATA_DIR: root },
});
console.log("Playtest host launched");
try {
  await app.evaluate(async () => {
    for (let i = 0; i < 100; i++) {
      if (globalThis.previewTest) return;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw Error("Preview host not ready");
  });
  const game = await app.evaluate(
    async (_electron, { root, remote }) => {
      const { library: l } = globalThis.previewTest;
      const g = l.create(
        root,
        "Preview fixture",
        {
          idea: "test",
          kind: "Arcade",
          vibe: "Bright",
          difficulty: "Easy",
          engine: "canvas",
        },
        null,
      );
      l.publishProject(
        g.id,
        {
          "index.html":
            "<!doctype html><html><body><h1>Published version</h1></body></html>",
        },
        "original",
      );
      l.writeDraft(
        g.id,
        "index.html",
        `<!doctype html><title>Playtest fixture</title><style>body{margin:0;background:#10283c;color:white;font:22px sans-serif}button{width:180px;height:60px}</style><button id="start">Start</button><div id="state">Ready</div><canvas width="960" height="400"></canvas><script>
    let count=0;const c=document.querySelector('canvas'),ctx=c.getContext('2d');function draw(){ctx.fillStyle='#10283c';ctx.fillRect(0,0,960,400);ctx.fillStyle='#b6ff81';ctx.fillRect(20+count*20,40,50,50)}draw();
    document.querySelector('#start').onclick=()=>document.querySelector('#state').textContent='Playing';
    addEventListener('keydown',e=>{if(e.key==='ArrowRight'){count++;document.querySelector('#state').textContent='Moved '+count;draw()}});
    addEventListener('keyup',e=>{document.querySelector('#state').textContent+=' released'});
    c.addEventListener('pointermove',e=>{if(e.buttons)document.querySelector('#state').textContent='Dragged '+e.clientX+','+e.clientY});
    fetch('${remote}').catch(()=>console.log('Network blocked'));
    fetch('file:///etc/passwd').catch(()=>console.log('File blocked'));
    window.open('${remote}');
    arcadeSave.save('checkpoint',1,{test:true}).then(()=>console.log('Temporary save written'));
    console.error('Fixture diagnostic');
    </script>`,
      );
      return { id: g.id, revision: l.get(g.id).revisionId };
    },
    { root, remote },
  );
  const run = (args, owner = "session-one") =>
    app.evaluate(
      async (_e, { args, owner, game }) => {
        const out = await globalThis.previewTest.manager.run(
          owner,
          game,
          args,
          new AbortController().signal,
        );
        return out;
      },
      { args, owner, game: game.id },
    );
  let opened = await run({ action: "open" });
  console.log("Preview opened", opened.data?.digest ?? opened);
  assert.ok(opened.image, JSON.stringify(opened));
  assert.equal(opened.data.diagnostics.viewport.width, 960);
  assert.equal(opened.data.stale, false);
  assert.match(opened.data.console.join(" "), /Fixture diagnostic/);
  assert.equal(opened.data.saveMode, "temporary_test_only");
  await writeFile(
    join(evidence, "open.jpg"),
    Buffer.from(opened.image.base64, "base64"),
  );
  const wrong = await run({ action: "observe" }, "another-session");
  assert.match(wrong.error, /owns/);
  const clicked = await run({ action: "pointer", x: 90, y: 30 });
  assert.match(clicked.data.diagnostics.text, /Playing/);
  const moved = await run({
    action: "keys",
    keys: ["ArrowRight"],
    durationMs: 300,
  });
  assert.match(moved.data.diagnostics.text, /Moved 1 released/);
  assert.notEqual(moved.image.base64, clicked.image.base64);
  const dragged = await run({
    action: "pointer",
    x: 300,
    y: 300,
    toX: 450,
    toY: 340,
    durationMs: 200,
  });
  assert.match(dragged.data.diagnostics.text, /Dragged 450,340/);
  assert.ok(dragged.data.performance.JSHeapUsedSize > 0);
  await writeFile(
    join(evidence, "played.jpg"),
    Buffer.from(moved.image.base64, "base64"),
  );
  assert.equal(hits, 0, "test renderer cannot reach network");
  await app.evaluate(
    (_e, id) =>
      globalThis.previewTest.library.writeDraft(id, "new.txt", "changed"),
    game.id,
  );
  assert.equal((await run({ action: "observe" })).data.stale, true);
  const reopened = await run({ action: "open", width: 1280, height: 800 });
  assert.equal(reopened.data.stale, false);
  assert.equal(reopened.data.width, 1280);
  assert.match(reopened.data.diagnostics.text, /Ready/);
  const current = await app.evaluate((_e, id) => {
    const l = globalThis.previewTest.library;
    return {
      revision: l.get(id).revisionId,
      html: l.project(id)["index.html"],
    };
  }, game.id);
  assert.equal(current.revision, game.revision);
  assert.match(current.html, /Published version/);
  const invalid = await run({
    action: "keys",
    keys: ["Meta"],
    durationMs: 100,
  });
  assert.match(invalid.error, /supported keys/);
  assert.equal((await run({ action: "observe" })).previewClosed, true);
  await run({ action: "open" });
  const cancelled = await app.evaluate(async (_e, id) => {
    const ac = new AbortController();
    setTimeout(() => ac.abort(), 100);
    return globalThis.previewTest.manager.run(
      "session-one",
      id,
      { action: "keys", keys: ["w"], durationMs: 2000 },
      ac.signal,
    );
  }, game.id);
  assert.equal(cancelled.previewClosed, true);
  assert.equal((await run({ action: "observe" })).previewClosed, true);
  await run({ action: "open" });
  assert.deepEqual(await run({ action: "close" }), { closed: true });
  const three = await app.evaluate(async () => {
    const t = globalThis.previewTest,
      g = new t.dependencies.StarterCatalog().remix(
        "pip-garden",
        t.library,
        t.library.dataDir,
      );
    const signal = new AbortController().signal;
    await t.manager.run("three-test", g.id, { action: "open" }, signal);
    const out = await t.manager.run(
      "three-test",
      g.id,
      { action: "wait", durationMs: 2000 },
      signal,
    );
    t.manager.close();
    return out;
  });
  assert.ok(three.image, JSON.stringify(three));
  assert.match(three.data.diagnostics.text, /Begin adventure/);
  await writeFile(
    join(evidence, "three-preview.jpg"),
    Buffer.from(three.image.base64, "base64"),
  );
  console.log(
    "PASS isolated draft playtest: screenshots, click, key/release, network/file denial, diagnostics, stale snapshot, viewport, unchanged publication, ownership, validation, cancellation and close",
  );
} finally {
  await app.close();
  server.close();
  await rm(root, { recursive: true, force: true });
}
