// Opt-in live-model acceptance: isolated remix, real hidden renderer and SDK.
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
if (
  process.env.ARCADE_LIVE_ACCEPT !== "1" ||
  !process.env.ARCADE_CREDENTIAL_FILE ||
  !process.env.ARCADE_LIVE_MODEL
)
  throw Error(
    "Explicit live-model opt-in, credential file and model are required",
  );
const root = await mkdtemp("/tmp/arcade-live-playtest-"),
  evidence = resolve(
    process.env.ARCADE_REMIX_MODE === "1"
      ? "test-results/agent-remix"
      : process.env.ARCADE_VISION_PROBE === "1"
        ? "test-results/playtest-vision"
        : "test-results/playtest-live",
  );
await mkdir(evidence, { recursive: true });
const app = await electron.launch({
  args: [resolve("test/preview-host.mjs")],
  env: { ...process.env, ARCADE_DATA_DIR: root },
  timeout: 30000,
});
try {
  await app.evaluate(async () => {
    for (let i = 0; i < 100; i++) {
      if (globalThis.previewTest) return;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw Error("Preview host not ready");
  });
  await app.evaluate(
    async (_e, { root, base, evidence }) => {
      const {
        AgentService,
        SettingsStore,
        StarterCatalog,
        readFileSync,
        writeFileSync,
      } = globalThis.previewTest.dependencies;
      const key = readFileSync(process.env.ARCADE_CREDENTIAL_FILE, "utf8")
        .match(/^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.+)\s*$/m)?.[1]
        ?.trim()
        .replace(/^(['"])(.*)\1$/, "$2");
      if (!key) throw Error("Credential unavailable");
      const t = globalThis.previewTest,
        settings = new SettingsStore(root);
      t.calls = [];
      t.service = new AgentService(t.library, settings, {
        executable: base + "/../../target/release/openworker-harness",
        credentialStorage: "memory",
        emit() {},
        playtester: {
          async run(owner, game, args, signal) {
            if (
              t.calls.length >=
              (process.env.ARCADE_REMIX_MODE === "1" ? 24 : 12)
            )
              throw Error("Live acceptance call budget reached");
            const out = await t.manager.run(owner, game, args, signal);
            t.calls.push({
              action: args.action,
              keys: args.keys ?? null,
              image: !!out.image,
              error: out.error ?? null,
              digest: out.data?.digest ?? null,
            });
            if (out.image)
              writeFileSync(
                evidence + "/frame-" + t.calls.length + ".jpg",
                Buffer.from(out.image.base64, "base64"),
              );
            return out;
          },
          close(owner) {
            t.manager.close(owner);
          },
        },
      });
      await t.service.configure(
        {
          name: "Anthropic",
          preset: "anthropic",
          api: "anthropic",
          baseUrl: "https://api.anthropic.com/v1",
          model: process.env.ARCADE_LIVE_MODEL,
        },
        key,
      );
      const g = new StarterCatalog().remix(
        "pip-garden",
        t.library,
        settings.get().gamesRoot,
      );
      t.id = g.id;
      if (process.env.ARCADE_VISION_PROBE === "1") {
        t.codes = [
          String(Math.floor(100000 + Math.random() * 900000)),
          String(Math.floor(100000 + Math.random() * 900000)),
        ];
        t.library.writeDraft(
          g.id,
          "index.html",
          `<!doctype html><html><body style="margin:0"><canvas width="1280" height="800"></canvas><script>const codes=${JSON.stringify(t.codes)},c=document.querySelector('canvas'),x=c.getContext('2d');let n=0,p=0;function draw(){x.fillStyle='#13253a';x.fillRect(0,0,1280,800);x.fillStyle='white';x.font='bold 100px sans-serif';x.fillText(codes[n],300,250);x.fillStyle='#b4f284';x.fillRect(300,350,300,100);x.fillStyle='#13253a';x.font='40px sans-serif';x.fillText('Next',380,415);x.fillStyle='#ff8a66';x.fillRect(300+p,550,60,60)}c.onclick=()=>{n=1;draw()};addEventListener('keydown',e=>{if(e.key==='ArrowRight'){p+=200;draw()}});draw();</script></body></html>`,
        );
      }
      t.before = t.library.draft(g.id).digest;
      t.original = t.library.draft(g.id).files;
      t.revision = t.library.get(g.id).revisionId;
      await t.service.send(
        g.id,
        process.env.ARCADE_REMIX_MODE === "1"
          ? "Improve this Pip's Adventure remix's HUD: make minimap labels readable and reduce HUD clutter/overlap at normal and compact desktop sizes. Preserve gameplay, controls, world art and save formats. Edit only styles/adventure.css and index.html; no JavaScript/assets/vendor changes. Visually inspect the draft, read relevant source, make focused style improvements, reopen and test both 1280x800 and 960x600 layouts, movement and pause. Budget the entire turn to 20 model steps including reads, writes, previews and publication: aim for at most 8 preview calls and one focused edit pass. Record brief observations. Finish by requesting project.publish with the exact final digest for human approval. Do not ask questions."
          : process.env.ARCADE_VISION_PROBE === "1"
            ? "Visual-only acceptance test. Do not read or edit files. Use only preview.playtest, at most 6 calls: open at 1280x800, read the six-digit code drawn in the screenshot and say it in a short observation before the next tool call. Click the visible Next button and read the second code in another short observation. Press ArrowRight briefly, inspect what moved, then close. End by reporting both codes. The codes are only visible on the canvas, not in page text. If you cannot see the pixels, say so rather than guessing."
            : "Perform a short visual playtest of Pip's Adventure, without editing or publishing anything. Use preview.playtest: open at 1280x800, inspect the actual screenshot, wait briefly if loading, click Begin adventure using the visible button position, move Pip with a held arrow key and inspect the result, and pause if convenient. Use at most 8 preview calls. Close the preview. Briefly describe what you actually saw and any concrete issue; do not claim a whole-level playthrough. Do not read source files or use telemetry instead of looking at the screenshot. Do not ask questions.",
      );
    },
    { root, base: resolve("."), evidence },
  );
  const deadline =
    Date.now() + (process.env.ARCADE_REMIX_MODE === "1" ? 480000 : 240000);
  let status,
    continuations = 0;
  do {
    await new Promise((r) => setTimeout(r, 1000));
    status = await app.evaluate(() => {
      const t = globalThis.previewTest,
        v = t.service.view(t.id);
      return {
        active: v.active,
        stage: v.stage,
        calls: t.calls,
        codes: t.codes ?? null,
        messages: v.messages
          .filter((m) => m.role === "assistant")
          .map((m) => m.text),
        unchanged:
          t.library.draft(t.id).digest === t.before &&
          t.library.get(t.id).revisionId === t.revision,
        pending: v.pending.length,
        publishedUnchanged: t.library.get(t.id).revisionId === t.revision,
        digest: t.library.draft(t.id).digest,
        changed: Object.keys(t.library.draft(t.id).files).filter(
          (p) =>
            JSON.stringify(t.library.draft(t.id).files[p]) !==
            JSON.stringify(t.original[p]),
        ),
      };
    });
    if (
      process.env.ARCADE_REMIX_MODE === "1" &&
      !status.active &&
      !status.pending &&
      status.stage === "Build needs attention" &&
      continuations++ < 1
    ) {
      await app.evaluate(async () => {
        const t = globalThis.previewTest;
        await t.service.send(
          t.id,
          "Continue the interrupted HUD polish from the saved draft, not from scratch. Inspect project.list_files to obtain the latest digest. Keep all existing work. Use at most 4 preview calls to check the final draft if needed, then request project.publish with that digest. No additional cosmetic redesign. Do not ask questions.",
        );
      });
      status.active = true;
    }
  } while (status.active && Date.now() < deadline);
  if (process.env.ARCADE_REMIX_MODE === "1")
    await app.evaluate((_e, evidence) => {
      const t = globalThis.previewTest;
      t.dependencies.writeFileSync(
        evidence + "/draft.json",
        JSON.stringify(t.library.draft(t.id)),
      );
    }, evidence);
  await writeFile(
    join(evidence, "result.json"),
    JSON.stringify(status, null, 2),
  );
  assert.equal(
    status.active,
    false,
    "live test must finish within four minutes",
  );
  if (process.env.ARCADE_REMIX_MODE === "1") {
    assert.equal(status.pending, 1);
    assert.equal(status.publishedUnchanged, true);
    assert.ok(status.changed.length > 0);
    assert.ok(
      status.changed.every((p) =>
        ["styles/adventure.css", "index.html"].includes(p),
      ),
    );
    assert.ok(
      status.calls.some((c) => c.image && c.digest === status.digest),
      "must inspect final draft",
    );
    await app.evaluate(async (_e, evidence) => {
      const t = globalThis.previewTest,
        v = t.service.view(t.id);
      const { writeFileSync } = t.dependencies;
      writeFileSync(
        evidence + "/draft.json",
        JSON.stringify(t.library.draft(t.id)),
      );
      await t.service.respond(t.id, v.pending[0].id, "once");
    }, evidence);
    for (let i = 0; i < 120; i++) {
      if (
        !(await app.evaluate(
          () =>
            globalThis.previewTest.service.view(globalThis.previewTest.id)
              .active,
        ))
      )
        break;
      await new Promise((r) => setTimeout(r, 1000));
    }
    assert.equal(
      await app.evaluate(
        () =>
          globalThis.previewTest.library.get(globalThis.previewTest.id).status,
      ),
      "ready",
    );
  } else {
    assert.equal(status.pending, 0);
    assert.equal(status.unchanged, true);
  }
  if (status.codes)
    for (const code of status.codes)
      assert.ok(
        status.messages.join(" ").includes(code),
        "model must read random canvas code from pixels",
      );
  assert.ok(status.calls.filter((c) => c.image).length >= 3);
  assert.ok(status.calls.some((c) => c.action === "pointer"));
  assert.ok(status.calls.some((c) => c.action === "keys"));
  assert.ok(
    status.calls.every((c) => !c.error),
    JSON.stringify(status.calls),
  );
  console.log(JSON.stringify(status, null, 2));
} finally {
  await app
    .evaluate(async () => {
      const t = globalThis.previewTest;
      if (t.service) {
        if (t.service.activeGameId)
          await t.service.cancel(t.service.activeGameId);
        await t.service.close();
      }
      t.manager.close();
      t.library.close();
    })
    .catch(() => {});
  await app.close();
  await rm(root, { recursive: true, force: true });
}
