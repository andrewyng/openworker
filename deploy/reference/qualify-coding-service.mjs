// Real-model local acceptance via the shipped TypeScript service SDK. Credentials
// remain in private state. This driver never repeats a task/tool after uncertainty.
import assert from "node:assert/strict";
import { readFile, writeFile, appendFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { Harness } from "../../harness/typescript-sdk/dist/index.js";
const [directory, mode = "start", attempt = "1"] = process.argv.slice(2);
assert(/^[1-9][0-9]?$/.test(attempt), "Explicit qualification attempt 1..99 required");
assert(
  directory && ["start", "observe", "followup", "recover"].includes(mode),
  "Usage: qualify-coding-service.mjs STATE start|observe|followup",
);
const root = resolve(directory),
  state = join(root, attempt === "1" ? "acceptance" : `acceptance-${attempt}`);
const m = JSON.parse(await readFile(join(root, "service/kubernetes.json")));
assert(
  m.context.startsWith("kind-") &&
    /^ow-service-m4-coding[0-9]{8}$/.test(m.namespace),
  "Explicit owned local coding fixture required",
);
await mkdir(state, { recursive: true, mode: 0o700 });
const service = await Harness.connect({
  url: m.url,
  namespace: "local",
  credentials: {
    getToken: async () =>
      String(await readFile(join(root, "service/input/admin-token"))).trim(),
  },
});
assert.equal(service.capabilities.serviceId, m.id);
const save = (name, value) =>
  writeFile(join(state, name), JSON.stringify(value, null, 2) + "\n", {
    mode: 0o600,
    flag: "wx",
  });
const task = `Build Daylight, an offline-first project planning web app. Make a complete working app, not a mock. Use an elegant white/slate Apple/Notion-like design, crisp typography, generous space and restrained blue accents. Use only locally available programs and Node standard libraries; no external npm packages, CDNs, fonts or network downloads.
Features: a responsive Kanban board with backlog/in-progress/done columns; add/edit/delete tasks; drag tasks between columns; title/description/priority/tags; text search and priority filters; localStorage persistence; meaningful summary counts and progress; accessible dialogs and keyboard interactions. Seed useful demo tasks. Include JSON export/import with validation and a reset-demo action.
Create maintainable index.html, styles.css, app.js, model.mjs, server.mjs, tests.mjs, package.json and README.md. Keep the domain module usable both in the browser and Node. Write meaningful node:test cases for task validation, filtering, transitions, counts and import/export. The HTTP server must serve only this project, bind 127.0.0.1 port 4000 and offer /health. It must need no dependency installation.
First inspect the workspace and program versions, then build incrementally. Use the coding file tools with hashes for edits. Run node --test and fix failures. Start the server with process_start (timeout_seconds 3600), check its status/logs and verify /health and the HTML route with curl from run_shell. Keep the server running if the runner lifetime permits. Save its opaque process_id and port in preview.json. Do not print credential/environment values. Model keys must never be in app files. Use your judgement and finish without asking questions. End with a concise summary of tests and any actual limitations.`;
let session, run;
try {
  if (mode === "start") {
    await save("submission-intent.json", {
      serviceId: m.id,
      agent: "daylight@1",
      idempotencyKey: `daylight-build-${attempt}`,
      task,
    });
    await service.registerRuntimeProfile(
      {
        id: "coding@1",
        provider: "openshell",
        cpu: 1,
        memoryMB: 512,
        sharing: "shared",
        approvalMode: "bypass",
        autoSkipQuestions: true,
      },
      { idempotencyKey: "coding-profile-1" },
    );
    await service.activateRuntimeProfile("coding@1", {
      alias: "coding",
      idempotencyKey: "coding-profile-activate-1",
    });
    await service.deployAgent(
      {
        id: "daylight@1",
        limits: {maxModelCalls:100,maxOutputTokens:128000,summaryTokens:512},
        instructions:
          "Build and verify the requested project only in its assigned OpenShell workspace. Use tools rather than describing hypothetical work. Inspect results and correct errors. Do not access host credentials or broaden sandbox policy.",
        tools: [
          "read_file",
          "write_file",
          "edit_file",
          "list_files",
          "search_files",
          "apply_patch",
          "run_shell",
          "process_start",
          "process_status",
          "process_logs",
          "process_stop",
          "git_status",
          "git_diff",
          "git_log",
        ],
        runtimeProfiles: ["coding@1"],
        defaultRuntimeProfile: "coding@1",
        defaultModel: { connection: "primary", model: "claude-sonnet-5" },
      },
      { idempotencyKey: "daylight-release-1" },
    );
    await service.activateAgent("daylight@1", {
      alias: "daylight",
      idempotencyKey: "daylight-activate-1",
    });
    const op = await service.submit("daylight", task, {
      project: "daylight",
      name: "Daylight web-app build",
      idempotencyKey: `daylight-build-${attempt}`,
    });
    await save("admission.json", op);
    console.log(
      JSON.stringify({
        phase: "admitted",
        operationId: op.id,
        sessionId: op.sessionId,
        runId: op.runId,
        environmentId: op.environmentId,
      }),
    );
    const admitted = await service.waitOperation(op.id, { timeoutMs: 180000 });
    await save("session.json", admitted.receipt);
    session = await service.resume(admitted.receipt);
    run = await session.resumeRun(op.submissionId);
  } else {
    const ref = JSON.parse(await readFile(join(state, "session.json")));
    session = await service.resume(ref);
    if (mode === "followup" || mode === "recover") {
      if (mode === "recover") {
        assert.equal(JSON.parse(await readFile(join(state,"followup-result.json"))).status,"failed","Inspect the original failed follow-up first");
      }
      const request = (mode === "recover" ? "The previous follow-up was interrupted during a run_shell call after some edits. Its outcome is unknown. Inspect existing files, process status/logs and current test results before deciding what remains; do not blindly repeat an uncertain action. The saved app server remains live and must not be restarted merely because the worker changed. Finish only the incomplete work. " : "") +
        "Improve Daylight with a compact table view toggle and due dates including overdue indicators. Preserve existing data. Add migration/default handling for older saved tasks and meaningful tests. Re-run the full test suite, check the existing managed server before deciding whether it needs restarting, and verify the HTTP routes. Keep the design consistent. Do not install packages or expose credentials. Update README and report actual results.";
      await save(mode+"-intent.json", {
        text: request,
        idempotencyKey: `daylight-${mode}-${attempt}`,
      });
      run = await session.send(request, {
        idempotencyKey: `daylight-${mode}-${attempt}`,
      });
      await save(mode+"-run.json", {
        runId: run.id,
        submissionId: run.submissionId,
      });
    } else {
      const op = JSON.parse(await readFile(join(state, "admission.json")));
      run = await session.resumeRun(op.submissionId);
    }
  }
  let forcedDisconnect;
  let cursor = 0,
    events = 0,
    disconnected = false;
  const deadline = setTimeout(
    async () => {
      // Watching never cancels a previously admitted task. A new qualification
      // turn has a bounded budget; submit cancellation once, never replay tools.
      forcedDisconnect = setTimeout(() => service.disconnect(), 10000);
      if (mode === "observe") {
        service.disconnect();
        console.error(
          "Observation deadline; inspect the original run before retry.",
        );
        return;
      }
      try {
        const receipt = await session.control(
          run.id,
          { kind: "cancel" },
          {
            idempotencyKey: "acceptance-timeout-" + run.id,
          },
        );
        await save(mode + "-deadline-control.json", receipt);
        console.error(
          "Acceptance deadline: cancellation submitted, not necessarily applied. Inspect its receipt.",
        );
      } catch {
        console.error(
          "Cancellation outcome unknown; inspect the original run before retry.",
        );
      }
    },
    15 * 60 * 1000,
  );
  try {
    while (true) {
      for await (const item of run.events({ after: cursor })) {
        const e = item.event;
        cursor = e.event_id;
        events++;
        await appendFile(
          join(state, mode + "-events.ndjson"),
          JSON.stringify(e) + "\n",
          { mode: 0o600 },
        );
        if (
          [
            "tool_started",
            "tool_finished",
            "turn_completed",
            "turn_recovered",
          ].includes(e.type)
        )
          console.log(
            JSON.stringify({
              phase: "progress",
              cursor,
              type: e.type,
              tool: e.data?.name ?? null,
            }),
          );
        if (!disconnected && mode === "start" && e.type === "tool_started") {
          disconnected = true;
          await save("viewer-disconnect.json", {
            runId: run.id,
            cursor,
            at: new Date().toISOString(),
          });
          break;
        }
      }
      const result = await run.snapshot();
      if (result.status !== "running") {
        if (result.status !== "completed") process.exitCode = 1;
        await save(mode + "-result.json", {
          ...result,
          eventsObserved: events,
          viewerReconnectTested: disconnected,
          lastCursor: cursor,
        });
        console.log(
          JSON.stringify({
            phase: "finished",
            status: result.status,
            sessionId: session.id,
            runId: run.id,
            eventsObserved: events,
            viewerReconnectTested: disconnected,
            finalOutput: result.finalOutput,
            failure: result.failure,
          }),
        );
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  } finally {
    clearTimeout(deadline);
    clearTimeout(forcedDisconnect);
  }
} finally {
  service.disconnect();
}
