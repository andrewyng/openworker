// The in-memory API behind a scenario (dev only). Replaces window.fetch and WebSocket for
// the app's own server, answering from one Scenario; the real <App/> then boots on top,
// so what you see IS the session view, not a copy of it. Anything the scenario does not
// cover fails like an unreachable server, which the app already tolerates.
import type { Scenario } from "./scenarios/types";

const SERVER = /^(https?|wss?):\/\/(127\.0\.0\.1|localhost):8765/;

const PERSONA = (id: string, name: string, group: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  icon: "cowork",
  tagline: "",
  requires_folder: false,
  builtin: true,
  tools: ["files", "search"],
  enabled: true,
  surfaced: true,
  ships: true,
  group,
  ...extra,
});

export interface SentRecord {
  at: string;
  channel: string;
  body: unknown;
}

export function installScenario(scenario: Scenario, onSent: (r: SentRecord) => void): void {
  const sid = scenario.session.session_id;
  const now = new Date().toISOString();
  const pending = [...(scenario.pending ?? [])];
  let unattended = !!scenario.session.unattended;
  const record = (channel: string, body: unknown) =>
    onSent({ at: new Date().toLocaleTimeString(), channel, body });

  const leadRow = {
    session_id: sid,
    title: scenario.session.title,
    workspace: scenario.session.workspace ?? "",
    agent: scenario.session.agent,
    model: scenario.session.model,
    mode: scenario.session.mode,
    updated_at: now,
    messages: scenario.messages.length,
    pinned: false,
    archived: false,
    attention: pending.length,
    liveness: pending.length ? "waiting" : "idle",
    subscriptions: [],
    unattended,
    ...(scenario.team
      ? { team: { role: "lead", team_id: scenario.team.team_id, chat_enabled: !!scenario.team.chat_enabled, chat_unread: 0 } }
      : {}),
  };
  const workerRows = (scenario.team?.workers ?? []).map((w) => ({
    session_id: `${sid}-${w.name}`,
    title: w.name,
    workspace: scenario.session.workspace ?? "",
    agent: w.persona,
    model: w.model ?? scenario.session.model,
    mode: scenario.session.mode,
    updated_at: now,
    messages: 0,
    pinned: false,
    archived: false,
    attention: 0,
    liveness: "idle",
    subscriptions: [],
    team: {
      role: "worker",
      team_id: scenario.team!.team_id,
      lead_session: sid,
      actor: w.name,
      status: w.status ?? "idle",
      current_item: w.current_item ?? "",
    },
    usage: w.usage ?? {},
  }));
  const personaIds = [...new Set([scenario.session.agent, ...workerRows.map((w) => w.agent)])];
  const models = [...new Set([scenario.session.model, ...workerRows.map((w) => w.model)])];

  const route = (method: string, path: string, query: URLSearchParams, body: any): unknown => {
    if (path.endsWith("/v1/capabilities")) return { mode: "desktop" };
    if (path.endsWith("/v1/health")) return { status: "ok", default_workspace: null, model: scenario.session.model };
    if (path.endsWith("/v1/settings"))
      return {
        provider: "anthropic",
        auto_approve: true,
        model: scenario.session.model,
        models,
        has_key: true,
        model_ready: true,
        source: "store",
        onboarded: true,
        experimental_connectors: false,
        surfaces: { cowork: true, chat: false, code: true },
        nav_layout: "grouped",
        scratch_base: "~/OpenWorker",
        sessions_peek: 5,
        // Curated names, as the real /v1/settings sends them (the cards show the name part).
        model_labels: {
          "anthropic:claude-opus-4-8": "Claude Opus 4.8 · Anthropic",
          "anthropic:claude-sonnet-4-8": "Claude Sonnet 4.8 · Anthropic",
        },
        model_context_windows: {},
      };
    if (path.endsWith("/v1/personas"))
      return {
        internal: true,
        personas: [
          PERSONA("cowork", "OpenWorker", "general", { default: true }),
          ...personaIds.filter((id) => id !== "cowork").map((id) => PERSONA(id, id, "teams")),
        ],
      };
    if (/\/v1\/teams\/[^/]+\/summary$/.test(path)) return scenario.team_summary;
    if (path.endsWith("/board/item")) {
      const item = scenario.board?.items.find(i => i.id === Number(query.get("id")));
      return item ? { ...item, timeline: [{ seq: 1, ts: new Date().toISOString(), kind: "created", actor: "lead" }] } : { error: "not found" };
    }
    if (path.endsWith("/v1/sessions")) return { sessions: [leadRow, ...workerRows] };
    if (path.endsWith("/v1/machines")) return { machines: [] };
    if (new RegExp(`/v1/sessions/${sid}/messages$`).test(path)) return { messages: scenario.messages };
    if (/\/v1\/sessions\/[^/]+\/messages$/.test(path)) return { messages: scenario.worker_messages?.[path.split("/").slice(-2)[0]] || [] };
    if (/\/v1\/sessions\/[^/]+\/unattended$/.test(path)) {
      if (method === "POST") unattended = !!body?.unattended;
      return { ok: true, unattended: path.includes(`/${sid}/`) ? unattended : false };
    }
    if (/\/v1\/sessions\/[^/]+\/board$/.test(path))
      return path.includes(`/${sid}/`) && scenario.board ? scenario.board : { space: null, name: "", items: [] };
    if (/\/v1\/inbox\/[^/]+\/resolve$/.test(path)) {
      const id = path.split("/").slice(-2)[0];
      const i = pending.findIndex((p) => p.id === id);
      if (i >= 0) pending.splice(i, 1);
      let parsed: unknown = body?.resolution;
      try {
        parsed = JSON.parse(body?.resolution);
      } catch {
        /* plain-string resolutions stay strings */
      }
      record(`POST /v1/inbox/${id}/resolve`, parsed);
      return { ok: true };
    }
    if (path.endsWith("/v1/inbox")) {
      const want = query.get("session_id");
      return { items: pending.filter((p) => !want || p.session_id === want) };
    }
    if (/\/v1\/teams\/[^/]+\/chat$/.test(path)) return { team_id: scenario.team?.team_id ?? "", messages: [] };
    if (/\/v1\/sessions\/[^/]+\/(roots|connections|bindings|skills)$/.test(path)) return {};
    return undefined;
  };

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!SERVER.test(url)) return realFetch(input as any, init);
    const u = new URL(url);
    const method = (init?.method || "GET").toUpperCase();
    let body: any = undefined;
    try {
      body = init?.body ? JSON.parse(String(init.body)) : undefined;
    } catch {
      /* non-JSON bodies are not interpreted */
    }
    const answer = route(method, u.pathname, u.searchParams, body);
    if (answer === undefined) {
      console.debug(`[scenario] not covered: ${method} ${u.pathname}`);
      throw new TypeError("scenario: endpoint not covered"); // same as an unreachable server
    }
    return new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  // The socket: says `ready`, replays the scenario's events on the lead's session socket,
  // and prints what the app sends instead of delivering it.
  class ScenarioSocket extends EventTarget {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    readyState = 0;
    url: string;
    protocol = "";
    binaryType = "blob";
    onopen: ((e: Event) => void) | null = null;
    onmessage: ((e: MessageEvent) => void) | null = null;
    onclose: ((e: CloseEvent) => void) | null = null;
    onerror: ((e: Event) => void) | null = null;
    constructor(url: string) {
      super();
      this.url = url;
      setTimeout(() => {
        this.readyState = 1;
        const open = new Event("open");
        this.onopen?.(open);
        this.dispatchEvent(open);
        if (!/\/ws\/session\//.test(url)) return;
        if (new URL(url).pathname !== `/ws/session/${sid}`) return this.emit({ type: "ready", data: {} });
        // Like the server: `ready` carries the session's model, mode and workspace.
        const { model, mode, workspace } = scenario.session;
        this.emit({ type: "ready", data: { model, mode, workspace: workspace ?? "", temp_workspace: false, running: false } });
        for (const e of scenario.events ?? []) this.emit(e);
      }, 0);
    }
    private emit(payload: unknown) {
      const e = new MessageEvent("message", { data: JSON.stringify(payload) });
      this.onmessage?.(e);
      this.dispatchEvent(e);
    }
    send(data: string) {
      try {
        const msg = JSON.parse(data);
        if (msg?.type && msg.type !== "ping") record("websocket " + new URL(this.url).pathname, msg);
      } catch {
        /* not JSON: nothing to show */
      }
    }
    close() {
      this.readyState = 3;
    }
  }
  const RealSocket = window.WebSocket;
  (window as any).WebSocket = function (url: string, protocols?: string | string[]) {
    return SERVER.test(url) ? new ScenarioSocket(url) : new RealSocket(url, protocols);
  };
  Object.assign((window as any).WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
}
