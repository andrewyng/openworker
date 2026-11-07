#!/usr/bin/env node
// Protocol fault fixture only; no agent engine lives in this package.
import { createInterface } from "node:readline";
import { spawn } from "node:child_process";
import { readFileSync, appendFileSync } from "node:fs";
const config = JSON.parse(readFileSync(process.argv[3].slice(1), "utf8"));
const reply = (id, result) =>
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
const lines = createInterface({ input: process.stdin });
for await (const line of lines) {
  const { id, method, params } = JSON.parse(line);
  if (method === "initialize") {
    if (config.agents[0].instructions === "bad-version")
      reply(id, { protocol_version: "9", capabilities: { agent_runs: true } });
    else
      reply(id, {
        protocol_version: "0.1",
        capabilities: { agent_runs: true },
      });
  } else if (method === "agent/list")
    reply(id, { agents: [{ id: "assistant" }] });
  else if (method === "echo") reply(id, params);
  else if (method === "hints") {
    for (const method of [
      "session/event",
      "stream/gap",
      "unknown/notification",
    ])
      process.stdout.write(
        JSON.stringify({
          jsonrpc: "2.0",
          method,
          params: { session_id: "s", event_id: 1 },
        }) + "\n",
      );
    reply(id, {});
  } else if (method === "env")
    reply(id, {
      secret: process.env.OW_TS_SECRET ?? null,
      modelKey: process.env.OPENAI_API_KEY ?? null,
    });
  else if (method === "crash") {
    appendFileSync(params.marker, "called\n");
    process.exit(1);
  } else if (method === "crash_with_inherited_pipes") {
    const holder=spawn(process.execPath,["-e","setTimeout(()=>{},10000)"],{stdio:["ignore",1,2]});
    appendFileSync(params.marker,String(holder.pid));
    holder.unref();
    process.exit(1);
  } else if (method === "malformed") process.stdout.write("invalid\n");
  else if (method === "huge") process.stdout.write("x".repeat(1024 * 1024 + 1));
  else if (method === "hang") await new Promise(() => {});
  else if (method === "error")
    process.stdout.write(
      JSON.stringify({
        jsonrpc: "2.0",
        id,
        error: { code: -1, message: "sk-do-not-leak-this-value" },
      }) + "\n",
    );
  else if (method === "run/cancel" || method === "agent/close")
    reply(id, { errors: { worker: "busy" } });
  else if (method === "session/send") {
    // Dynamic team membership belongs to the host. This fixture has only s.
    if (params.input?.target_session_id !== "s")
      process.stdout.write(JSON.stringify({jsonrpc:"2.0",id,error:{code:-32602,message:"Target is not a session member"}})+"\n");
    else reply(id, {});
  }
  else if (method === "session/attach") {
    // First process dies during a safe read; the replacement observes the marker.
    if (params.session_id === "crash-once") {
      const marker = config.state_dir + "-read";
      let seen = false;
      try {
        readFileSync(marker);
        seen = true;
      } catch {}
      if (!seen) {
        appendFileSync(marker, "called");
        process.exit(1);
      }
    }
    reply(id, { cursor: 0 });
  } else if (method === "run/start") reply(id, { cursors: { s: 0 } });
  else if (method === "run/wait") {
    const after = params.cursors?.s ?? 0;
    const end = Math.min(after + 2, 5);
    reply(id, {
      status: "completed",
      cursor: 5,
      submission_id: params.submission_id,
      final_output: "done",
      interruptions: [],
      events: Array.from({ length: end - after }, (_, n) => ({
        agent: "assistant",
        session_id: "s",
        event: {
          event_id: after + n + 1,
          session_id: "s",
          turn_id: null,
          type: "message",
          data: {},
        },
        summary: { type: "message", text: `message-${after + n + 1}` },
      })),
      cursors: { s: end },
      more_events: end < 5,
    });
  } else reply(id, {});
}
