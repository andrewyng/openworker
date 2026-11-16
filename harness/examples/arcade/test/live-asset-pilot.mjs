// Opt-in: one real-model build, isolated game library, memory-only credentials.
import assert from "node:assert/strict";
import { readFile, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { AgentService } from "../build/electron/agent-service.js";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { until } from "./model-fixture.mjs";
if (process.env.ARCADE_LIVE_ACCEPT !== "1")
  throw Error("Opt in to billable model calls with ARCADE_LIVE_ACCEPT=1");
const source = await readFile(process.env.ARCADE_CREDENTIAL_FILE, "utf8");
const key = source
  .match(/^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.+)\s*$/m)?.[1]
  ?.trim()
  .replace(/^(['"])(.*)\1$/, "$2");
if (!key) throw Error("Credential unavailable");
const root =
    process.env.ARCADE_PILOT_ROOT ??
    (await mkdtemp(join(tmpdir(), "arcade-pip-live-"))),
  library = new Library(root),
  settings = new SettingsStore(root),
  model = process.env.ARCADE_LIVE_MODEL;
if (!model) throw Error("Choose an exact model ID");
const service = new AgentService(library, settings, {
  executable: resolve("../../target/release/openworker-harness"),
  credentialStorage: "memory",
  emit() {},
});
try {
  await service.configure(
    {
      name: "Anthropic",
      preset: "anthropic",
      api: "anthropic",
      baseUrl: "https://api.anthropic.com/v1",
      model,
    },
    key,
  );
  let game;
  if (process.env.ARCADE_PILOT_ROOT) {
    game = library.list()[0];
    await service.open(game.id);
    await service.send(
      game.id,
      "Continue the interrupted build. Your previous model response hit its output-token limit before writing the gameplay script. Do not generate one giant tool call. Split JavaScript into several ES modules of at most 6000 characters each (world.js, actor.js, game.js etc) and issue one small write at a time. Re-read the current draft and preserve the imported robot, completed HTML/CSS and attribution. Finish the original brief including pipStatus telemetry, real animation clips, collecting five fireflies, restart and pause; then publish. Keep the game focused and avoid excessive decorative code. Do not ask questions.",
    );
  } else
    ({ game } = await service.create({
      engine: "three",
      kind: "Explore & collect",
      vibe: "Warm storybook garden",
      difficulty: "Relaxed",
      idea: `Build a polished small 3D game titled Pip's Garden. This is a real asset-integration pilot: call assets.list, then assets.add for pip-robot, read its metadata and use the real animated GLB as the player, never a primitive substitute. Make a charming sunny pocket garden on a floating round island: soft shadows, flowers, stepping stones, a few trees, warm cream/mint UI, attractive composition. Gameplay: freely walk with WASD/arrows to collect 5 glowing fireflies, then celebrate with the robot's Dance clip. Use Idle when stationary, Running/Walking when moving and Jump on Space. Collision and movement in a separate parent; +Z faces forward, so correctly face movement direction. Normalize its height and ground contact following metadata. Clear Start, Pause/Resume, Restart buttons and touch directions/Jump. Pause on blur and release held keys. Keep a visible loading state and an understandable load-error message, not a blank scene. No model/network calls or external assets inside the game. Keep scope small, robust and beautiful (not a giant world). Use multi-file Three.js project and the bundled loader. Preserve asset/license files. Include read-only acceptance telemetry window.pipStatus() returning {phase, x, z, score, total, assetLoaded, clip, mixerTime, frames, remaining:[{x,z}], geometries,textures}. Do not expose any state-changing test hooks. Keyboard axes: W/Up moves -Z, S/Down +Z, A/Left -X, D/Right +X. Start must be disabled until GLB is ready. Restart resets to the starting position and all collectibles. Do not ask questions: choose sensible defaults and publish the completed game for approval. Budget authored code around 30KB, split into modules of at most 6000 characters each to avoid output-token limits; keep it focused.`,
    }));
  console.log("Building isolated Pip game with", model, "game", game.id);
  await until(
    () => service.view(game.id).pending.length || !service.view(game.id).active,
    "asset pilot build",
    480000,
  );
  const view = service.view(game.id),
    pending = view.pending[0];
  if (pending?.kind !== "approval") {
    console.log(
      JSON.stringify(
        {
          stage: view.stage,
          error: view.game.error,
          lastActivity: view.activity.slice(-4),
          messages: view.messages.slice(-2),
        },
        null,
        2,
      ),
    );
    throw Error("Builder did not reach publication approval");
  }
  assert.ok(
    [
      "arcade-studio-builder-v5",
      "arcade-studio-builder-v6",
      "arcade-studio-builder-v7",
    ].includes(library.get(game.id).session.agent),
  );
  const draft = library.draft(game.id);
  assert.ok(
    draft.files["assets/library/pip-robot.glb"],
    "builder must use catalog tool",
  );
  assert.ok(draft.files["assets/library/pip-robot.LICENSE.txt"]);
  await service.respond(game.id, pending.id, "once");
  await until(() => !service.view(game.id).active, "publish", 120000);
  assert.equal(library.get(game.id).status, "ready");
  await mkdir("test-results/asset-pilot", { recursive: true });
  await writeFile(
    "test-results/asset-pilot/live.json",
    JSON.stringify(
      {
        root,
        id: game.id,
        model,
        revision: library.get(game.id).revisionId,
        files: Object.keys(library.project(game.id)),
        stage: "published; gameplay qualification pending",
      },
      null,
      2,
    ),
  );
  console.log("Published real agent-built game; evidence root:", root);
} finally {
  await service.close();
  library.close();
}
