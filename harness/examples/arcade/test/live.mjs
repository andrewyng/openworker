// Opt-in live model acceptance. Keys stay in memory and are never logged.
import assert from "node:assert/strict";
import { readFile, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { AgentService } from "../build/electron/agent-service.js";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { until } from "./model-fixture.mjs";
if (process.env.ARCADE_LIVE_ACCEPT !== "1")
  throw Error("Set ARCADE_LIVE_ACCEPT=1 to authorize billable model calls.");
const anthropic = process.env.ARCADE_LIVE_PROVIDER === "anthropic";
let key = anthropic
  ? process.env.ANTHROPIC_API_KEY
  : process.env.OPENAI_API_KEY;
if (!key && process.env.ARCADE_CREDENTIAL_FILE) {
  const s = await readFile(process.env.ARCADE_CREDENTIAL_FILE, "utf8");
  key = s
    .match(
      anthropic
        ? /^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.+)\s*$/m
        : /^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.+)\s*$/m,
    )?.[1]
    ?.trim()
    .replace(/^(['"])(.*)\1$/, "$2");
}
if (!key) throw Error("Provide the approved OpenAI credential source.");
const root = await mkdtemp(join(tmpdir(), "arcade-live-")),
  library = new Library(root),
  settings = new SettingsStore(root),
  model = process.env.ARCADE_LIVE_MODEL ?? "gpt-6-sol";
const options = {
  executable:
    process.env.OPENWORKER_RUNTIME ??
    resolve("../../target/release/openworker-harness"),
  credentialStorage: "memory",
  emit() {},
};
let service = new AgentService(library, settings, options);
const provider = {
  name: anthropic ? "Anthropic" : "OpenAI",
  preset: anthropic ? "anthropic" : "openai",
  api: anthropic ? "anthropic" : "responses",
  baseUrl: anthropic
    ? "https://api.anthropic.com/v1"
    : "https://api.openai.com/v1",
  model,
};
try {
  await service.configure(provider, key);
  const view = await service.create({
      idea: "Build a beautiful small playable star-catching game. A player moves a basket using left/right keys or touch to catch falling stars. Include visible score, clear controls, and a restart button. Keep it under 20 KB.",
      kind: "Catch & dodge",
      vibe: "Cozy",
      difficulty: "Relaxed",
    }),
    id = view.game.id;
  console.log("Live build started; waiting for publish approval.");
  await until(
    () => service.view(id).pending.length || !service.view(id).active,
    "live build",
    240000,
  );
  let p = service.view(id).pending[0];
  assert.equal(p?.kind, "approval");
  await service.close();
  service = new AgentService(library, settings, options);
  await service.configure(provider, key);
  const recovered = await service.open(id);
  assert.equal(recovered.pending[0]?.id, p.id);
  await service.respond(id, p.id, "session");
  await until(() => !service.view(id).active, "live publish", 240000);
  assert.equal(library.get(id).status, "ready");
  assert.match(library.html(id), /<script[\s>]/i);
  await service.send(
    id,
    "Make the star color gold and add a visible title Golden Catch. Keep the existing gameplay.",
  );
  await until(() => !service.view(id).active, "live edit", 240000);
  assert.equal(library.get(id).status, "ready");
  assert.equal(library.revisions(id).length, 2);
  assert.match(Object.values(library.project(id)).join("\n"), /Golden Catch/i);
  await service.close();
  service = new AgentService(library, settings, options);
  const replay = await service.open(id);
  assert.ok(replay.previewUrl);
  assert.ok(replay.messages.length > 2);
  await mkdir("test-results/live", { recursive: true });
  await writeFile(
    "test-results/live/result.json",
    JSON.stringify(
      {
        root,
        id,
        model,
        revisions: 2,
        streamedMessages: replay.messages.length,
        passed: true,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: live creation, approval across restart, edit, persisted revision and offline recovery. Evidence:",
    root,
  );
} finally {
  await service.close();
  library.close();
}
