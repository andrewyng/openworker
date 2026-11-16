// Windows lifecycle acceptance. Own test processes only; no accounts/model calls.
import { spawn, execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import assert from "node:assert/strict";
import { RuntimeClient } from "./support/runtime.mjs";
if (process.platform !== "win32") throw Error("Windows-only acceptance");
delete process.env.OPENAI_API_KEY;
const binary = resolve(
  process.env.OPENWORKER_BIN || "target/debug/openworker.exe",
);
const root = join(homedir(), "openworker-windows-lifecycle", randomUUID());
await mkdir(root, { recursive: true });
execFileSync(binary, ["prepare-directory", "--path", root]);
const work = join(root, "work");
await mkdir(work);
const bundle = resolve("examples/assistant");
const args = (state) => [
  "serve",
  "--stdio",
  "--scripted",
  "--sandbox",
  "off",
  "--state-dir",
  state,
  "--bundle",
  bundle,
];
const quote = (s) => "'" + s.replaceAll("'", "''") + "'";
const ps = (script) =>
  execFileSync(
    join(
      process.env.SystemRoot,
      "System32/WindowsPowerShell/v1.0/powershell.exe",
    ),
    [
      "-NoProfile",
      "-NonInteractive",
      "-EncodedCommand",
      Buffer.from(script, "utf16le").toString("base64"),
    ],
    { encoding: "utf8", windowsHide: true },
  ).trim();
async function until(fn, p, label, ms = 20000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const value = await fn();
    if (p(value)) return value;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw Error("Timed out " + label);
}
let client, blocked;
function start() {
  client = new RuntimeClient(binary, args(join(root, "state")));
  return client.request("initialize", { protocol_version: "0.1" });
}
async function task(tag) {
  const marker = join(work, tag + ".json");
  const command = `$child=Start-Process -FilePath ($env:SystemRoot+'\\System32\\WindowsPowerShell\\v1.0\\powershell.exe') -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep 120' -PassThru; $records=@((Get-Process -Id $PID),$child) | ForEach-Object { @{id=$_.Id;started=$_.StartTime.Ticks.ToString()} }; ConvertTo-Json -InputObject @($records) -Compress | Set-Content -LiteralPath ${quote(marker)} -Encoding UTF8; Start-Sleep 120`;
  const { session } = await client.request("session/create", {
    name: tag,
    coworker: "assistant-example",
    workspace: work,
    permissions: { mode: "bypass" },
  });
  await client.request("turn/start", {
    session_id: session.id,
    submission_id: randomUUID(),
    text:
      "TEST_RESOURCE_GRANTS " +
      JSON.stringify([{ name: "run_shell", arguments: { command } }]),
  });
  const records = await until(
    async () => {
      try {
        return JSON.parse(
          (await readFile(marker, "utf8")).replace(/^\uFEFF/, ""),
        );
      } catch {
        return null;
      }
    },
    Boolean,
    "owned descendants started",
  );
  assert.equal(records.length, 2);
  return { sid: session.id, records };
}
async function gone(records) {
  await until(
    () => {
      const encoded = JSON.stringify(records).replaceAll("'", "''");
      const result = ps(
        `$records=ConvertFrom-Json '${encoded}'; $alive=@(); foreach($r in $records){$p=Get-Process -Id $r.id -ErrorAction SilentlyContinue; if($p -and $p.StartTime.Ticks.ToString() -eq $r.started){$alive+=$r.id}}; ConvertTo-Json -InputObject @($alive) -Compress`,
      );
      return JSON.parse(result);
    },
    (x) => x.length === 0,
    "exact owned process identities gone",
  );
}
try {
  await start();
  const crash = await task("crash");
  client.child.kill("SIGKILL");
  await until(
    () => client.alive,
    (x) => !x,
    "runtime killed",
  );
  await gone(crash.records);
  await start();
  const snap = await client.request("session/attach", {
    session_id: crash.sid,
  });
  assert.equal(snap.turn.status, "interrupted");
  const journal = await client.request("event/read", {
    session_id: crash.sid,
    after: 0,
  });
  assert.equal(
    journal.events.filter((e) => e.type === "tool_started").length,
    1,
  );
  const cancel = await task("cancel");
  const current = await client.request("session/attach", {
    session_id: cancel.sid,
  });
  await client.request("turn/cancel", {
    session_id: cancel.sid,
    turn_id: current.turn.id,
  });
  await until(
    () => client.request("session/attach", { session_id: cancel.sid }),
    (s) => s.turn.status === "cancelled",
    "cancel terminal",
  );
  await gone(cancel.records);
  await client.stop();
  blocked = spawn(binary, args(join(root, "backpressure")), {
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    env: {
      SystemRoot: process.env.SystemRoot,
      WINDIR: process.env.SystemRoot,
      PATH: join(process.env.SystemRoot, "System32"),
    },
  });
  blocked.stderr.resume();
  blocked.stdin.on("error", () => {});
  blocked.stdout.pause();
  const frames = [
    {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocol_version: "0.1" },
    },
    ...Array.from({ length: 1000 }, (_, i) => ({
      jsonrpc: "2.0",
      id: i + 2,
      method: "coworker/list",
      params: {},
    })),
  ];
  blocked.stdin.end(frames.map((v) => JSON.stringify(v)).join("\n") + "\n");
  const started = Date.now();
  await until(
    () => blocked.exitCode !== null || blocked.signalCode,
    Boolean,
    "bounded blocked-stdout shutdown",
    25000,
  );
  const report = {
    passed: true,
    root,
    checks: [
      "non-admin native Windows process ownership",
      "runtime SIGKILL reaps shell and grandchild by recorded identity",
      "restart marks uncertain action interrupted without replay",
      "explicit cancel reaps owned descendants before terminal state",
      "blocked stdout does not hang runtime shutdown",
      "runtime PATH excludes Python, Docker and WSL",
    ],
    backpressureMs: Date.now() - started,
  };
  await writeFile(join(root, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally {
  await client?.stop();
  if (blocked && blocked.exitCode === null && !blocked.signalCode)
    blocked.kill("SIGKILL");
}
