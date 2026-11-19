// Optional Node-side adapter for managed SDK apps. The harness uses its normal
// model transport; only this authenticated loopback endpoint speaks IMA.
import { createServer, type ServerResponse } from "node:http";
import { once } from "node:events";
import { randomUUID } from "node:crypto";
import type {
  Harness,
  ProviderSessionEvent,
  ProviderListener,
} from "../index.js";

type Api = "chat-completions" | "anthropic";
type Route =
  { mode: "auto"; target?: never } | { mode: "pin" | "prefer"; target: string };
export interface IntelImaOptions {
  provider: string;
  baseUrl: string;
  api: Api;
  apiKey: string;
  route: Route;
  allowEgress?: boolean;
  /** Set a stable loopback port for a persistent harness state directory. */
  port?: number;
  onState?: ProviderListener;
}
function requestUrl(baseUrl: string): URL {
  const url = new URL(baseUrl);
  const local = ["127.0.0.1", "[::1]", "localhost"].includes(url.hostname);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !(url.protocol === "https:" || (local && url.protocol === "http:"))
  ) {
    throw new Error("IMA requires HTTPS or a loopback HTTP endpoint");
  }
  return url;
}

async function bounded(stream: AsyncIterable<Uint8Array>, limit: number) {
  let size = 0;
  const chunks: Uint8Array[] = [];
  for await (const chunk of stream) {
    size += chunk.length;
    if (size > limit) throw new Error("IMA payload too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function binding(id: string, state: string) {
  return {
    agent_type: "main_agent",
    agent_session_id: id,
    task_session_id: id,
    parent_session_id: null,
    has_subagents: false,
    agent_status: state,
  };
}

interface Receipt {
  local?: boolean;
  target?: string;
  registered: boolean;
}
function checkMetadata(
  kind: string,
  value: any,
  options: IntelImaOptions,
  receipt: Receipt,
) {
  if (kind === "route") {
    const local = value?.locus === "local";
    const target = value?.target;
    if (
      (!local && value?.locus !== "remote") ||
      (!local && !options.allowEgress) ||
      typeof target !== "string" ||
      !target ||
      target === "auto" ||
      target.length > 256 ||
      (options.route.mode === "pin" && target !== options.route.target) ||
      (receipt.target && (receipt.target !== target || receipt.local !== local))
    )
      throw new Error("Invalid IMA route");
    receipt.local = local;
    receipt.target = target;
  } else if (kind === "lifecycle" && value?.agent_type === "main_agent") {
    receipt.registered = true;
  } else if (
    kind === "guard" ||
    (kind === "negotiation" &&
      (value?.ignored?.length || value?.degraded?.length))
  ) {
    throw new Error("IMA rejected the requested policy");
  }
}

// Stream deltas after the first validated route. Hold terminal frames until
// all metadata is checked so a conflicting late receipt cannot authorize a tool.
async function forwardStream(
  source: ReadableStream<Uint8Array>,
  res: ServerResponse,
  options: IntelImaOptions,
  bound: boolean,
  signal: AbortSignal,
) {
  const receipt: Receipt = { registered: false };
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let partial = "",
    pending = "",
    total = 0,
    terminal = false;
  async function flush() {
    if (res.destroyed || signal.aborted) throw new Error("Client disconnected");
    if (!res.headersSent)
      res.writeHead(200, { "content-type": "text/event-stream" });
    if (pending && !res.write(pending)) await once(res, "drain", { signal });
    pending = "";
  }
  function line(raw: string) {
    const input = raw.replace(/\r$/, "");
    const comment = /^:\s*ima-(route|lifecycle|guard|negotiation)\s+(.+)$/.exec(
      input,
    );
    if (comment) {
      checkMetadata(comment[1]!, JSON.parse(comment[2]!), options, receipt);
      return;
    }
    if (input.startsWith(":")) return;
    if (!input.startsWith("data:")) {
      pending += input + "\n";
      return;
    }
    const data = input.slice(5).trim();
    if (data === "[DONE]") {
      terminal = true;
      pending += input + "\n";
      return;
    }
    const value = JSON.parse(data);
    if (value.x_meta) {
      checkMetadata("negotiation", value.x_meta, options, receipt);
      for (const [key, item] of Object.entries(value.x_meta))
        checkMetadata(key, item, options, receipt);
      delete value.x_meta;
      if (!Object.keys(value).length) return;
    }
    if (
      value.type === "message_stop" ||
      value.choices?.some((c: { finish_reason?: string }) => c.finish_reason)
    )
      terminal = true;
    pending += "data: " + JSON.stringify(value) + "\n";
  }
  for await (const chunk of source as unknown as AsyncIterable<Uint8Array>) {
    total += chunk.length;
    if (total > 8 * 1024 * 1024) throw new Error("IMA stream too large");
    partial += decoder.decode(chunk, { stream: true });
    const lines = partial.split("\n");
    partial = lines.pop()!;
    for (const raw of lines) line(raw);
    if (!terminal && receipt.target && (!bound || receipt.registered))
      await flush();
  }
  partial += decoder.decode();
  if (partial) line(partial);
  if (!terminal || !receipt.target || (bound && !receipt.registered))
    throw new Error("Missing IMA receipt");
  await flush();
}
export async function attachIntelIma(
  harness: Harness,
  options: IntelImaOptions,
) {
  if (
    !["auto", "pin", "prefer"].includes(options.route.mode) ||
    (options.route.mode !== "auto" &&
      (!options.route.target || options.route.target === "auto"))
  ) {
    throw new Error("Invalid IMA route configuration");
  }
  options = { ...options, route: { ...options.route } };
  const base = requestUrl(options.baseUrl).href.replace(/\/$/, "");
  const key = randomUUID();
  const route = {
    ...options.route,
    constraints: { allow_egress: options.allowEgress ?? false },
    on_router_error: "fail",
  };
  const path = options.api === "anthropic" ? "messages" : "chat/completions";
  const headers: Record<string, string> = {
    "content-type": "application/json",
  };
  if (options.api === "anthropic") {
    headers["anthropic-version"] = "2023-06-01";
    headers["x-api-key"] = options.apiKey;
  } else headers.authorization = "Bearer " + options.apiKey;
  const active = new Map<
    string,
    { sequence: number; model: string; registered: boolean }
  >();
  const server = createServer(async (req, res) => {
    const authorized =
      req.headers.authorization === "Bearer " + key ||
      req.headers["x-api-key"] === key;
    if (!authorized || req.method !== "POST" || req.url !== "/v1/" + path) {
      res.writeHead(403).end();
      return;
    }
    const requestId = randomUUID();
    const abort = new AbortController();
    let sent = false;
    res.on("close", () => {
      if (!sent || res.writableEnded) return;
      abort.abort();
      void fetch(base + "/requests/" + requestId, {
        method: "DELETE",
        headers,
        redirect: "error",
        signal: AbortSignal.timeout(3000),
      })
        .then((r) => r.body?.cancel())
        .catch(() => {});
    });
    try {
      const body = JSON.parse(await bounded(req, 16 * 1024 * 1024));
      const id = req.headers["x-openworker-session-id"];
      if (typeof id === "string") {
        if (
          options.route.mode !== "pin" ||
          options.allowEgress ||
          body.model !== options.route.target
        ) {
          throw new Error(
            "IMA state notifications require a pinned local model",
          );
        }
        const sequence = Number(req.headers["x-openworker-event-sequence"]);
        if (!Number.isSafeInteger(sequence) || sequence < 1)
          throw new Error("Invalid sequence");
        // A client can disappear without closing its session. Bound idle entries.
        active.delete(id);
        if (active.size >= 8192) active.delete(active.keys().next().value!);
        active.set(id, { sequence, model: body.model, registered: false });
        body.x_agent = binding(id, "start");
      } else delete body.x_agent;
      Object.assign(body, {
        x_request_id: requestId,
        x_route: route,
        x_on_unsupported: "error",
      });
      sent = true;
      const upstream = await fetch(base + "/" + path, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        redirect: "error",
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(180000)]),
      });
      if (!upstream.ok || !upstream.body) {
        await upstream.body?.cancel();
        throw new Error("IMA request failed");
      }
      await forwardStream(
        upstream.body,
        res,
        options,
        typeof id === "string",
        abort.signal,
      );
      if (typeof id === "string") {
        const current = active.get(id);
        if (current) current.registered = true;
      }
      res.end();
    } catch {
      // Refuse blind retries of an ambiguous provider attempt.
      if (res.headersSent && !res.destroyed)
        res.end(
          '\ndata: {"type":"error","error":{"type":"invalid_request_error","code":"adapter_request_failed"}}\n\n',
        );
      else if (!res.destroyed)
        res
          .writeHead(422, { "content-type": "application/json" })
          .end('{"error":{"code":"adapter_request_failed"}}');
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port ?? 0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  const close = () =>
    new Promise<void>((resolve) => {
      server.close(() => resolve());
      server.closeAllConnections();
    });
  try {
    const addr = server.address();
    if (!addr || typeof addr === "string")
      throw new Error("Missing listener address");
    await harness.setModelProvider({
      name: options.provider,
      api: options.api,
      baseUrl: "http://127.0.0.1:" + addr.port + "/v1",
      credentials: { apiKey: key, storage: "memory" },
    });
    await harness.registerProviderObserver(
      options.provider,
      async (event, signal) => {
        await options.onState?.(event, signal);
        const state = active.get(event.sessionId);
        if (!state || event.sequence <= state.sequence) return;
        const status = statusFor(event);
        if (!status) return;
        state.sequence = event.sequence;
        if (status === "done") active.delete(event.sessionId);
        if (!state.registered) return;
        const response = await fetch(base + "/chat/completions", {
          method: "POST",
          headers,
          redirect: "error",
          signal: AbortSignal.any([signal, AbortSignal.timeout(3000)]),
          body: JSON.stringify({
            model: state.model,
            messages: [],
            stream: false,
            x_route: route,
            x_request_id: randomUUID(),
            x_on_unsupported: "error",
            x_agent: binding(event.sessionId, status),
          }),
        });
        if (!response.ok || !response.body) {
          await response.body?.cancel();
          throw new Error("IMA status delivery failed");
        }
        const ack = JSON.parse(
          await bounded(
            response.body as unknown as AsyncIterable<Uint8Array>,
            65536,
          ),
        );
        if (
          !ack.id?.startsWith("status-") ||
          !Array.isArray(ack.choices) ||
          ack.choices.length ||
          ack.usage?.total_tokens !== 0
        )
          throw new Error("Invalid IMA status acknowledgement");
      },
    );
    return { close };
  } catch (error) {
    await close();
    throw error;
  }
}

function statusFor(event: ProviderSessionEvent) {
  switch (event.state) {
    case "waiting_tool_completion":
      return "waiting_tool";
    case "waiting_user_response":
    case "waiting_user_approval":
      return "waiting_confirm";
    case "session_closed":
      return "done";
    default:
      return undefined;
  }
}
