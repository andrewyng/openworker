import i18n from "i18next";
import {
  approvalMessage,
  connectorResponseMessage,
  directoryResponseMessage,
  itemsResponseMessage,
  planResponseMessage,
  questionResponseMessage,
  teamResponseMessage,
  toolResponseMessage,
} from "./cardPayloads";
import type { GroupedQuestion, QuestionOption, SessionInfo, WsEvent } from "./types";

declare const __COWORKER_DEV_TOKEN__: string;

// Endpoint resolution order: runtime-injected globals (Tauri sets `window.__COWORKER_HTTP__`
// for its dynamically-chosen sidecar port) → Vite env → same-origin when the page itself is
// served over https (the hosted dashboard: the service serves both SPA and API) → the
// 127.0.0.1:8765 dev default. This keeps a single codebase: browser `npm run dev` hits 8765;
// the desktop shell hits its sidecar; machines.openworker.com talks to itself.
const servedOverHttps = (): boolean =>
  typeof location !== "undefined" && location.protocol === "https:";
export const httpBase = (): string =>
  (globalThis as any).__COWORKER_HTTP__ ||
  (import.meta as any).env?.VITE_COWORKER_HTTP ||
  (servedOverHttps() ? location.origin : "http://127.0.0.1:8765");
const wsBase = (): string =>
  (globalThis as any).__COWORKER_WS__ ||
  (import.meta as any).env?.VITE_COWORKER_WS ||
  (servedOverHttps() ? `wss://${location.host}` : "ws://127.0.0.1:8765");
const apiToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  (typeof __COWORKER_DEV_TOKEN__ === "string" ? __COWORKER_DEV_TOKEN__ : "");

// The org this browser acts in (hosted multi-tenant only; empty elsewhere). Set at cloud
// boot from /v1/me and by the org switcher; rides every request so the backend's resolver
// needs no per-endpoint plumbing. HTTP carries it as a header; a browser WebSocket cannot
// set headers, so there it is a query parameter (org ids are tenant labels, not secrets).
let activeOrg = "";
export function setActiveOrg(orgId: string): void {
  activeOrg = orgId;
}
export function getActiveOrg(): string {
  return activeOrg;
}

// All local REST calls pass through this module, so a module-local wrapper applies launch
// authentication without asking every endpoint helper to remember the security header.
/** Fired (once per burst) when OUR OWN backend answers 401: the launch token this
 * window carries no longer matches the running service — a sidecar restarted under a
 * dev GUI that baked the old token, or a stale tab. The App renders a plain signed-out
 * state instead of crashing on the error body (ledger 2026-09-01: `undefined.includes`). */
export const API_UNAUTHORIZED = "openworker:api-unauthorized";
let unauthorizedAnnounced = 0;
const announceUnauthorized = () => {
  const now = Date.now();
  if (now - unauthorizedAnnounced < 2000) return;
  unauthorizedAnnounced = now;
  window.dispatchEvent(new CustomEvent(API_UNAUTHORIZED));
};

const fetch = (
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> => {
  const headers = new Headers(init.headers);
  const token = apiToken();
  if (token) headers.set("X-OpenWorker-Token", token);
  if (activeOrg) headers.set("X-OCW-Org", activeOrg);
  return globalThis.fetch(input, { ...init, headers }).then((res) => {
    // Only the local sidecar's own 401 means "this window is signed out". A machine or
    // cloud call (/m/… proxies, cloud routes) returning 401 is that machine's problem
    // and is handled where the call is made.
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (res.status === 401 && url.startsWith(httpBase()) && !url.includes("/v1/machines/") && !url.includes("/v1/cloud/")) {
      announceUnauthorized();
    }
    return res;
  });
};

// A Sec-WebSocket-Protocol entry must be an RFC 6455 token — no '@', '=', etc.
// Desktop tokens (hex) and Auth0 JWTs (base64url + dots) qualify; anything else
// (dev/test tokens) rides a base64url envelope the backend unwraps. Passing an
// invalid subprotocol would THROW from the constructor and blank the render.
const WS_TOKEN_SAFE = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const wsSafeToken = (token: string): string =>
  WS_TOKEN_SAFE.test(token)
    ? token
    : "ow.b64." +
      btoa(token).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const openWebSocket = (url: string): WebSocket => {
  const token = apiToken();
  const target = activeOrg
    ? `${url}${url.includes("?") ? "&" : "?"}org=${encodeURIComponent(activeOrg)}`
    : url;
  return token
    ? new WebSocket(target, ["openworker", wsSafeToken(token)])
    : new WebSocket(target);
};

// -- remote homes: machine-aware session routing (remote-home-design.md P1c) ---
// A session that lives on a joined machine is reached through the controller's
// proxy prefix — "a machine is just a different base URL". This module-local map
// (fed by getAllSessions and session creation) lets every session-scoped helper
// below route transparently; unknown sessions resolve to the local sidecar.
const sessionMachines = new Map<string, string>();

export function registerSessionMachine(sessionId: string, machineId: string | null | undefined): void {
  if (machineId) sessionMachines.set(sessionId, machineId);
  else sessionMachines.delete(sessionId);
}

export function machineOfSession(sessionId: string): string | null {
  return sessionMachines.get(sessionId) ?? null;
}

/** Union view (spec §"Union view on the signed-in desktop"): machines from
 * the hosted cloud registry ride the sidecar's cloud proxy. In the GUI their
 * ids wear a `cloud:` prefix, so ONE mapping — here — decides which base a
 * machine id resolves to, and every consumer keeps passing opaque ids. */
export const CLOUD_ID_PREFIX = "cloud:";
export const isCloudMachineId = (mid: string): boolean => mid.startsWith(CLOUD_ID_PREFIX);

/** Admin base for one machine (rename/remove/sessions/secrets live under it). */
export const machineApi = (mid: string): string =>
  isCloudMachineId(mid)
    ? `${httpBase()}/v1/cloud/machines/${mid.slice(CLOUD_ID_PREFIX.length)}`
    : `${httpBase()}/v1/machines/${mid}`;

const machineWsPath = (mid: string): string =>
  isCloudMachineId(mid)
    ? `/ws/cloud/machines/${mid.slice(CLOUD_ID_PREFIX.length)}/p`
    : `/ws/machines/${mid}/p`;

/** Base URL of an ENGINE: the local sidecar, or a joined machine through the
 * controller's proxy — "a machine is just a different base URL". Pages scoped
 * by the Settings machine picker pass the picked machine id through. */
export const engineBase = (machineId?: string | null): string =>
  machineId ? `${machineApi(machineId)}/p` : httpBase();

const sessionApiBase = (sessionId: string): string => {
  const mid = sessionMachines.get(sessionId);
  return mid ? `${machineApi(mid)}/p` : httpBase();
};

export interface Health {
  status: string;
  default_workspace: string | null;
  model: string;
}

export interface RecentWorkspace {
  path: string;
  name: string;
  exists: boolean;
}

export interface WorkspaceCommandTrust {
  workspace: string;
  requested_commands: string[];
  trusted: boolean;
  required: boolean;
  exists?: boolean;
}

export async function getHealth(): Promise<Health> {
  const res = await fetch(`${httpBase()}/v1/health`);
  return res.json();
}

export async function getRecentWorkspaces(machineId?: string | null): Promise<RecentWorkspace[]> {
  // Remote homes: a machine's recents are ITS recents (paths on that machine).
  const base = engineBase(machineId);
  const res = await fetch(`${base}/v1/workspaces/recent`);
  return (await res.json()).workspaces ?? [];
}

/** Ask the LOCAL sidecar to open the OS folder picker — the browser GUI can't obtain absolute
 * paths from web file dialogs. Blocks until the user picks or cancels; null on cancel/unavailable. */
export async function pickFolderViaServer(): Promise<string | null> {
  try {
    const res = await fetch(`${httpBase()}/v1/workspaces/pick`, { method: "POST" });
    const d = await res.json();
    return d.ok && d.path ? d.path : null;
  } catch {
    return null;
  }
}

export async function openWorkspace(
  path: string,
  create = false,
  machineId?: string | null,
): Promise<{
  path: string;
  ok: boolean;
  error?: string;
  git_branch?: string | null;
  command_trust?: WorkspaceCommandTrust;
}> {
  // Validation happens on the machine the path lives on — the box answers
  // exists/not-a-dir/git-branch for ITS filesystem.
  const base = engineBase(machineId);
  const res = await fetch(`${base}/v1/workspaces/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create }),
  });
  return res.json();
}

/** UX-029 "Start in a temporary folder": create the conversation's temp dir at send time
 * (git-init'd for code-family work). Idempotent. */
export async function createTempWorkspace(
  sessionId: string,
  git = true,
  machineId?: string | null,
): Promise<{ ok: boolean; path?: string; git?: boolean; error?: string }> {
  // Remote homes: the temp dir must exist on the machine the session RUNS on
  // (explicit id — the session may be too new for the routing map).
  const base = engineBase(machineId);
  const res = await fetch(`${base}/v1/workspaces/temp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, git }),
  });
  return res.json();
}

/** UX-029 "Save as project…": move a session's temporary folder to a real location.
 * Callers reconnect afterwards so the engine rebinds to the new path. */
export async function saveSessionAsProject(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/save-as-project`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
  return res.json();
}

export async function getTrustedWorkspaces(): Promise<WorkspaceCommandTrust[]> {
  const res = await fetch(`${httpBase()}/v1/workspaces/trusted`);
  return (await res.json()).workspaces ?? [];
}

export async function setWorkspaceTrusted(
  path: string,
  trusted: boolean,
): Promise<{ ok: boolean; error?: string } & WorkspaceCommandTrust> {
  const res = await fetch(`${httpBase()}/v1/workspaces/trust`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, trusted }),
  });
  return res.json();
}

export async function getSessions(workspace?: string): Promise<SessionInfo[]> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(`${httpBase()}/v1/sessions${q}`);
  return (await res.json()).sessions ?? [];
}

/** Sessions of one machine's list, tagged and registered for machine-aware
 * routing. Works for joined and `cloud:` machines alike — machineApi is the
 * routing seam. */
async function machineSessions(m: Machine): Promise<SessionInfo[]> {
  try {
    const res = await fetch(`${machineApi(m.id)}/sessions`);
    const data = await res.json();
    const rows: SessionInfo[] = data.sessions ?? [];
    for (const s of rows) {
      s.machine = m.id;
      s.machine_name = m.name;
      if (!data.live) s.machine_offline = true;
      registerSessionMachine(s.session_id, m.id);
    }
    return rows;
  } catch {
    return [];
  }
}

/** Local sessions + every machine's sessions — joined AND cloud (union v1.5)
 * — tagged with machine id/name and registered for machine-aware routing.
 * The controller answers from the live box while it's connected and from its
 * stored snapshot while it's offline (`machine_offline` marks those rows) —
 * greyed, never vanished. A signed-out/expired cloud session only hides the
 * cloud rows, never the rest. */
export async function getAllSessions(): Promise<SessionInfo[]> {
  const local = await getSessions();
  for (const s of local) registerSessionMachine(s.session_id, null);
  let remote: SessionInfo[] = [];
  try {
    const joined = getMachines()
      .then(({ machines }) => machines)
      .catch(() => [] as Machine[]);
    const cloud = getCloudMachines()
      .then((info) => (info.session === "ok" ? info.machines : []))
      .catch(() => [] as Machine[]);
    const machines = (await Promise.all([joined, cloud])).flat();
    remote = (await Promise.all(machines.map(machineSessions))).flat();
  } catch {
    /* machines API unavailable — local list is still the truth for This Mac */
  }
  return [...local, ...remote];
}

// A structured connector-delivered inbound message (§3.1). Attached to the user message it framed,
// for display only — the model still sees the framed `content`; this drives the ConnectorMessageCard.
export interface MessageSource {
  connector: string; // platform id, e.g. "slack"
  kind: "channel" | "dm";
  channel_id: string; // e.g. "C0BD7KZ1AH5"
  channel_name: string; // resolved; may equal the id (e.g. "#ocw-test")
  sender_id: string;
  sender_name: string; // resolved; may equal the id
  ts: number; // epoch seconds
  text: string; // the RAW message (what the card shows)
  // Board wakes only (connector === "board"): the digest as structured rows, so
  // the BoardWakeCard renders collapsed summaries instead of re-parsing prose.
  board?: { rows: BoardWakeRow[]; check_in?: boolean };
}

// One digest event on a board wake. `note` is a UI-clamped excerpt of a hand-off
// comment (the full text lives on the board).
export interface BoardWakeRow {
  kind: "assigned" | "claimed" | "moved" | "filed" | "comment" | "chat" | "waiting" | string;
  item?: number | null;
  title?: string;
  actor?: string;
  to?: string;
  from?: string;
  assignee?: string;
  refs?: string[];
  note?: string;
  // `waiting` only: a worker is waiting on the lead's decision for this tool call.
  tool?: string;
  prompt_id?: string;
}

// A transcript message from GET /v1/sessions/{id}/messages. Kept permissive (open shape) because
// itemsFromMessages reads several role-specific fields; `source` is the optional connector sidecar.
export interface ConversationMessage {
  role: string;
  content?: any;
  tool_calls?: any[];
  tool_call_id?: string;
  source?: MessageSource;
  // Token counts for the round-trip that produced an assistant message
  // ({model, input, output, cache_read, cache_write}); absent on older servers.
  usage?: import("./types").TurnUsage;
  [key: string]: any;
}

export async function getSessionMessages(sessionId: string): Promise<ConversationMessage[]> {
  const mid = sessionMachines.get(sessionId);
  if (mid) {
    // Controller-view endpoint: live (and re-cached) while the box is
    // connected, the last synced copy — read-only — while it's offline.
    const res = await fetch(
      `${machineApi(mid)}/sessions/${encodeURIComponent(sessionId)}/messages`,
    );
    return (await res.json()).messages ?? [];
  }
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${sessionId}/messages`);
  return (await res.json()).messages ?? [];
}

export async function renameSession(sessionId: string, title: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return res.json();
}

export async function setSessionFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(flags),
  });
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  return res.json();
}

// Agent teams (OPE-96): the session's board — items on the workspace-keyed space.
export interface BoardItem {
  id: number;
  title: string;
  description: string;
  criteria: string;
  state: "open" | "in_progress" | "blocked" | "review" | "done" | "canceled" | string;
  assignee: string;
  creator: string;
  refs: string[];
  links: { kind: string; item: number }[];
  // Blocked rows only: the latest blocker comment, clamped ("need tfvars…").
  blocker?: string;
  waiting?: { prompt_id: string; tool: string; preview: string };
  status?: string;
  status_ts?: string;
  created_ts?: string;
}

export interface Board {
  space: string | null;
  name: string;
  items: BoardItem[];
}

export interface JournalCase {
  case: string;
  entries: number;
  last_ts: string;
}

export async function getBoard(sessionId: string): Promise<Board> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/board`);
  return res.json();
}

export async function getTeamSummary(sessionId: string, teamId: string): Promise<import("./teamView").TeamSummary> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/teams/${encodeURIComponent(teamId)}/summary`);
  if (!res.ok) throw new Error("Team summary unavailable");
  const data = await res.json();
  if (!Array.isArray(data.items) || !Array.isArray(data.workers) || !data.lead || !data.totals || !data.counts) throw new Error("Team summary unavailable");
  return data;
}

// One event in an item's merged timeline (the detail pane renders the item's
// whole story: filed → assigned/claimed → moves → comments, with attachments).
export interface BoardTimelineEvent {
  seq: number;
  ts: string;
  actor: string;
  kind: "created" | "assigned" | "claimed" | "moved" | "comment" | string;
  to?: string;
  assignee?: string;
  body?: string;
  refs?: string[];
}

export type BoardItemDetail = BoardItem & { timeline?: BoardTimelineEvent[] };

export async function getBoardItem(
  sessionId: string,
  id: number,
): Promise<BoardItemDetail | { error: string }> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/board/item?id=${id}`,
  );
  return res.json();
}

// Attachment bytes → an object URL for <img>. The module fetch wrapper carries
// the sidecar token, which a bare <img src> cannot.
export async function fetchBoardAttachment(
  sessionId: string,
  stored: string,
): Promise<string | null> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/board/attachment?name=${encodeURIComponent(stored)}`,
  );
  if (!res.ok) return null;
  return URL.createObjectURL(await res.blob());
}

// A pure note on an item — never changes state; the assignee hears it via its feed.
export async function boardComment(
  sessionId: string,
  item: number,
  body: string,
): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/board/comment`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item, body }),
    },
  );
  return res.json();
}

export async function boardTransition(
  sessionId: string,
  item: number,
  to: string,
  comment = "",
): Promise<BoardItem | { error: string }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/board/transition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item, to, comment }),
  });
  return res.json();
}

export interface ChatMessage {
  seq: number;
  ts: string;
  author: string;
  author_role: "user" | "lead" | "worker" | string;
  text: string;
  mentions: string[];
}

export interface TeamChat {
  enabled: boolean;
  team_id?: string;
  members: { name: string; persona: string; role: string }[];
  messages: ChatMessage[];
}

// Team calls ride the machine of the session they belong to (connectors-across-machines
// spec §6): a team lives where its lead session lives, so its chat and the box's journal
// resolve through the same seam sessions use. No session id = the local sidecar.
const teamApiBase = (sessionId?: string): string =>
  sessionId ? sessionApiBase(sessionId) : httpBase();

export async function getTeamChat(teamId: string, sessionId?: string): Promise<TeamChat> {
  const res = await fetch(`${teamApiBase(sessionId)}/v1/teams/${encodeURIComponent(teamId)}/chat`);
  return res.json();
}

export async function postTeamChat(
  teamId: string,
  text: string,
  sessionId?: string,
): Promise<ChatMessage | { error: string }> {
  const res = await fetch(`${teamApiBase(sessionId)}/v1/teams/${encodeURIComponent(teamId)}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return res.json();
}

export async function getJournalCases(sessionId?: string): Promise<JournalCase[]> {
  const res = await fetch(`${teamApiBase(sessionId)}/v1/teams/journal`);
  return (await res.json()).cases ?? [];
}

export interface ArtifactInfo {
  path: string; // workspace-relative (the display/API identifier)
  abs_path?: string; // absolute — what "Copy path" copies
  name: string;
  kind: "markdown" | "html" | "image" | "code" | "text" | string;
  size: number;
  modified_at: number;
  // Which rail surface opened it — drives the viewer's breadcrumb ("Artifacts" vs
  // "Files"). Absent = artifacts (UX-037).
  origin?: "artifacts" | "files";
}

export interface ArtifactContent {
  ok: boolean;
  error?: string;
  path: string;
  kind: string;
  content?: string;
  data_url?: string;
  truncated?: boolean;
  // kind === "folder": a directory listing (models sometimes link a whole package dir).
  entries?: { name: string; dir: boolean; size: number }[];
}

export async function getArtifacts(sessionId: string): Promise<ArtifactInfo[]> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts`);
  return (await res.json()).artifacts ?? [];
}

export async function readArtifact(sessionId: string, path: string): Promise<ArtifactContent> {
  const q = new URLSearchParams({ path });
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/read?${q.toString()}`);
  return res.json();
}

/** Show the artifact in the OS file manager ("reveal") or open it with its default app ("open"). */
export async function revealArtifact(
  sessionId: string,
  path: string,
  mode: "reveal" | "open" = "reveal",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, mode }),
  });
  return res.json();
}

// -- session roots (orphan Cowork: scratch + added folders) -------------------
export interface RootInfo {
  path: string;
  writable: boolean;
  label: string;
  primary: boolean;
  exists: boolean;
}

export async function getRoots(sessionId: string): Promise<RootInfo[]> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/roots`);
  return (await res.json()).roots ?? [];
}

export async function addRoot(
  sessionId: string,
  path: string,
  writable: boolean,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/roots`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, writable }),
  });
  return res.json();
}

export async function removeRoot(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  const q = new URLSearchParams({ path });
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/roots?${q.toString()}`,
    { method: "DELETE" },
  );
  return res.json();
}

// -- MCP servers --------------------------------------------------------------
export interface McpServer {
  name: string;
  enabled: boolean;
  transport: string;
  requires_approval: boolean;
  // "connected" | "configured" | "disabled" | and for auth:"oauth" servers:
  // "needs_auth" (no tokens yet) | "authorizing" (browser sign-in in flight)
  status: string;
  auth?: "oauth" | null;
  // http server whose anonymous connect hit a 401/403 — offer OAuth sign-in.
  auth_hint?: boolean;
  // Epoch seconds of the last successful explicit Test (persisted server-side).
  last_test_at?: number | null;
  last_error?: string | null;
  tool_count: number | null;
  config: Record<string, any>;
}

export async function getMcpServers(): Promise<McpServer[]> {
  const res = await fetch(`${httpBase()}/v1/mcp`);
  return (await res.json()).servers ?? [];
}

export async function addMcpServer(name: string, config: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/mcp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, config }),
  });
  return res.json();
}

export async function patchMcpServer(name: string, changes: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function deleteMcpServer(name: string) {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
  return res.json();
}

export async function getMcpTools(
  name: string,
): Promise<{ ok: boolean; error?: string; tools: { name: string; description: string }[] }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/tools`);
  return res.json();
}

// OPE-136 §4/§5: the server's standing trust — which tools carry a durable "don't ask"
// rule, plus whether the legacy server-wide requires_approval:false is still present.
export async function getMcpTrust(
  name: string,
): Promise<{ ok: boolean; tools: string[]; legacy_dont_ask: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/trust`);
  return res.json();
}

export async function revokeMcpTrust(name: string, tool: string) {
  const res = await fetch(
    `${httpBase()}/v1/mcp/${encodeURIComponent(name)}/trust/${encodeURIComponent(tool)}`,
    { method: "DELETE" },
  );
  return res.json();
}

/** Migrate the legacy server-wide don't-ask flag to named per-tool trust rules. */
export async function convertMcpTrust(
  name: string,
): Promise<{ ok: boolean; error?: string; trusted?: string[] }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/trust/convert`, {
    method: "POST",
  });
  return res.json();
}

/** Reveal the global mcp.json in the OS file manager — the ONE file every custom
 * server lives in (the per-server Configuration mirror was removed in its favor). */
export async function revealMcpConfig(): Promise<{ ok: boolean; error?: string; path?: string }> {
  const res = await fetch(`${httpBase()}/v1/mcp/config/reveal`, { method: "POST" });
  return res.json();
}

export async function reloadMcp() {
  const res = await fetch(`${httpBase()}/v1/mcp/reload`, { method: "POST" });
  return res.json();
}

/** Connect one MCP server now. For OAuth servers this opens the system browser;
 * poll getMcpServers() for the status flip (authorizing → connected / needs_auth). */
export async function connectMcp(name: string): Promise<{ ok: boolean; started?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/connect`, {
    method: "POST",
  });
  return res.json();
}

/** Drop the connection and forget the stored OAuth tokens. */
export async function signoutMcp(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/signout`, {
    method: "POST",
  });
  return res.json();
}

// -- connectors ---------------------------------------------------------------
export interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
}

// A message from a sender not (yet) on the allow-list — parked instead of dropped (§19).
export interface ParkedMessage {
  id: string;
  platform: string;
  chat_id: string;
  chat_name: string | null;
  user_id: string;
  user_name: string | null;
  chat_type: string;
  text: string;
  ts: number;
  team_id?: string | null; // workspace (managed Slack relay); null on manual Socket Mode
}

// One connected Slack workspace (managed relay is multi-workspace; ids are workspace-scoped,
// so each workspace carries its OWN allow-list).
export interface SlackWorkspace {
  team_id: string;
  account: string;
  domain?: string; // slack.com subdomain — unique even when display names collide
  allowed_users: string[];
  allow_all: boolean;
  allowed_user_names?: Record<string, string | null>;
  approval_owner_ids?: string[];
  approval_owner_names?: Record<string, string | null>;
  // Who installed this workspace (authed_user) — pre-added to the allow-list on
  // connect (UX-027); the GUI marks their chip "you" and keys the setup card copy.
  installer_user_id?: string;
  installer_name?: string;
}

// One connected GitHub App installation (managed relay is multi-installation;
// sender logins are global but each installation keeps its OWN allow-list).
export interface GithubInstallation {
  installation_id: string;
  account_login: string; // the org/user the App is installed on
  account_type: string; // "Organization" | "User"
  repo_selection: string; // "all" | "selected"
  github_login: string; // the connecting user's own login
  allowed_users: string[]; // sender logins allowed to trigger work
  allow_all: boolean;
}

// One connected HubSpot portal (multi-portal: `hubspot:portal:<hub_id>` profiles).
export interface HubSpotPortal {
  hub_id: string;
  name: string;
  sandbox: boolean;
  default: boolean;
  managed: boolean;
  access: "read" | "write" | ""; // consent tier granted ("" = manual token, unknown)
}

// One connected Google account (multi-account: `gmail:account:<email>` /
// `google_calendar:account:<email>` profiles — same shape for both).
export interface GmailAccount {
  email: string;
  default: boolean;
  managed: boolean;
  scopes: string;
  needs_reauth: boolean;
}

// "Never show agents" — enforced locally in the tool layer; agents see silent
// omissions, the user sees counts on tool cards + Activity rows.
export interface GmailFilters {
  senders: string[];
  labels: string[];
}

// One account of a generic multi-account connector (`<name>:account:<id>`
// profiles — Notion workspaces, PostHog projects, …). Gmail/Calendar predate
// the generic layer and keep their email-keyed shape above.
export interface AccountRow {
  account_id: string;
  name: string; // display identity captured at connect (workspace name, email, …)
  default: boolean;
  managed: boolean;
}

export interface Connector {
  name: string;
  title: string;
  icon: string;
  blurb: string;
  // Pre-connect detail page copy (UX-DECISIONS §38): optional About paragraph
  // (empty → group omitted) + honest Access bullets.
  about?: string;
  access?: string[];
  auth: string;
  two_way: boolean;
  // Chat-platform capability, narrower than two_way: sessions can subscribe to channels.
  channels: boolean;
  available: boolean;
  fields: ConnectorField[];
  instructions: string[];
  connected: boolean;
  account: string | null;
  enabled: boolean;
  brand_color: string; // hex brand color, e.g. "#611f69" (fallback gray "#6b7280")
  logo: string; // stable logo id keyed into the frontend registry (empty → fallback glyph)
  aliases?: string[]; // extra typeahead terms ("calendar" surfaces Outlook)
  mcp?: boolean; // MCP-backed one-click (vendor-hosted MCP + local OAuth — no cloud sign-in)
  allowed_users: string[]; // the allow-list (managed inline in the Connectors tab)
  allowed_user_names?: Record<string, string | null>; // id → display name (people directory)
  approval_owner_ids?: string[]; // Manual Slack: humans allowed to resolve approvals
  approval_owner_names?: Record<string, string | null>;
  recent?: RecentSender[]; // recently-seen senders on a connected two-way connector
  unauthorized?: ParkedMessage[]; // parked messages from unallowed senders (§19)
  tools: ConnectorTool[];
  managed: boolean; // one-click managed OAuth available (needs cloud sign-in)
  managed_paused?: boolean; // one-click temporarily off (e.g. Google CASA pending) — badge "Coming soon"
  managed_profile: boolean; // current profile came from managed OAuth (vs manual paste)
  mode?: string; // "relay" for the managed cloud path; "" for manual/token connect
  workspaces?: SlackWorkspace[]; // Slack only: connected workspaces (managed relay)
  // Gmail/Calendar: email-keyed rows; generic account connectors (notion,
  // attio, posthog, …): AccountRow. The detail pages narrow by connector.
  accounts?: GmailAccount[] | AccountRow[];
  filters?: GmailFilters; // Gmail only: "Never show agents" senders/labels
  portals?: HubSpotPortal[]; // HubSpot only: connected portals (multi-portal)
  hidden_fields?: string[]; // HubSpot only: properties stripped from agent reads
  installations?: GithubInstallation[]; // GitHub only: App installations (managed relay)
}

// --- OpenWorker Cloud (optional sign-in; manual token paste always works) ---

export interface CloudStatus {
  signed_in: boolean;
  account: string;
  user_id: string;
  telemetry_enabled?: boolean; // Phase 5 opt-out; signed-out users send nothing regardless
}

/** Flip the product-telemetry preference (local; only meaningful when signed in). */
export async function setCloudTelemetry(
  enabled: boolean,
): Promise<{ ok: boolean; telemetry_enabled?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/cloud/telemetry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

export async function getCloudStatus(): Promise<CloudStatus> {
  const res = await fetch(`${httpBase()}/v1/cloud/status`);
  return res.json();
}

export async function cloudLogin(): Promise<{ ok: boolean }> {
  // The sidecar opens the system browser; the GUI just polls status after.
  const res = await fetch(`${httpBase()}/v1/cloud/login`, { method: "POST" });
  return res.json();
}

/** Poll cloud status until the browser sign-in lands (or the bound runs out).
 *
 * Fast 500ms polls for the first 20s — the moment the user finishes in the
 * browser they're staring at the app waiting for it to flip, and a 2s interval
 * reads as "sign-in is slow" (owner complaint, 2026-07-16) — then relaxes to 2s
 * for the long tail (~2min total). Calls `onDone` with the signed-in status, or
 * null when it timed out. Returns a cancel function (call on unmount). */
export function waitForCloudSignIn(
  onDone: (s: CloudStatus | null) => void,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let polls = 0;
  const tick = async () => {
    polls += 1;
    const s = await getCloudStatus().catch(() => null);
    if (cancelled) return;
    if (s?.signed_in) return onDone(s);
    if (polls >= 90) return onDone(null); // 40×500ms + 50×2s ≈ 2min
    timer = setTimeout(tick, polls < 40 ? 500 : 2000);
  };
  timer = setTimeout(tick, 500);
  return () => {
    cancelled = true;
    if (timer) clearTimeout(timer);
  };
}

export async function cloudLogout(): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/cloud/logout`, { method: "POST" });
  return res.json();
}

export async function connectManaged(
  name: string,
  options?: { access?: "read" | "write"; flow?: "install" },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/connect-managed`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `access` names a broker-defined consent tier (hubspot read | write).
      // Normal connect links existing grants; explicit Add installation opens
      // GitHub's account/repository consent picker even when grants already exist.
      body: JSON.stringify({
        ...(options?.access ? { access: options.access } : {}),
        ...(name === "github" && options?.flow ? { flow: options.flow } : {}),
      }),
    },
  );
  return res.json();
}

/** One-click connect for an MCP-backed connector (monday, asana, jira): the sidecar
 * opens the vendor's sign-in in the browser (local OAuth, no cloud account needed);
 * poll getConnectors until the card flips to connected. */
export async function connectMcpBacked(name: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/mcp-connect`,
    { method: "POST" },
  );
  return res.json();
}

export interface ConnectorTool {
  name: string;
  label: string;
  kind: "read" | "write" | string;
  description: string;
  enabled: boolean;
  requires_approval: boolean;
}

export async function getConnectors(machineId?: string | null): Promise<Connector[]> {
  const res = await fetch(`${engineBase(machineId)}/v1/connectors`);
  return (await res.json()).connectors ?? [];
}

export async function connectConnector(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; account?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  return res.json();
}

/** Machine-scope connector list, STRICT: a non-ok answer throws so the
 * remote panel renders "unreachable" — never an empty list that reads as
 * "no connectors exist". Lives here so the module's auth wrapper applies
 * (a component-level fetch bypasses the sidecar token — learned live). */
export async function getMachineConnectorsStrict(machineId: string): Promise<Connector[]> {
  const res = await fetch(`${engineBase(machineId)}/v1/connectors`);
  if (!res.ok) throw new Error("machine unreachable");
  return (await res.json()).connectors ?? [];
}

export async function disconnectConnector(
  name: string,
  machineId?: string | null,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${engineBase(machineId)}/v1/connectors/${encodeURIComponent(name)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

/** Remote manual connect (union view): the connector FIELDS are sealed in
 * this tab to the machine's pinned key — every relay in between (the
 * desktop's acceptor, or our cloud) carries ciphertext only. The box unseals
 * and runs its own connect, validation included. */
export async function connectConnectorSealed(
  machineId: string,
  name: string,
  sealedB64: string,
): Promise<{ ok: boolean; account?: string; error?: string }> {
  const res = await fetch(
    `${machineApi(machineId)}/connectors/${encodeURIComponent(name)}/connect-sealed`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sealed_b64: sealedB64 }),
    },
  );
  return res.json();
}

export async function updateConnectorTools(
  name: string,
  enabled: Record<string, boolean>,
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string; tools?: Record<string, boolean> }> {
  const res = await fetch(
    `${engineBase(machineId)}/v1/connectors/${encodeURIComponent(name)}/tools`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  return res.json();
}

export interface AuditEvent {
  id: number;
  timestamp: string;
  session_id: string;
  agent: string;
  workspace: string;
  connector: string;
  tool: string;
  stage: string;
  status: string;
  approval: string;
  args: Record<string, any>;
  result_preview: string;
  reason: string;
  resource: string;
}

export async function getAudit(params: {
  limit?: number;
  session_id?: string;
  connector?: string;
  tool?: string;
} = {}): Promise<AuditEvent[]> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.session_id) q.set("session_id", params.session_id);
  if (params.connector) q.set("connector", params.connector);
  if (params.tool) q.set("tool", params.tool);
  const res = await fetch(`${httpBase()}/v1/audit${q.toString() ? "?" + q.toString() : ""}`);
  return (await res.json()).events ?? [];
}

export interface BrowserState {
  open: boolean;
  url: string;
  title: string;
  status: string;
  last_action: string;
  last_result: string;
  last_error: string;
  screenshot_data_url: string;
  updated_at: string | null;
  controls: any[];
}

export async function getBrowserState(): Promise<BrowserState> {
  const res = await fetch(`${httpBase()}/v1/browser/state`);
  return res.json();
}

export async function takeBrowserScreenshot(): Promise<BrowserState & { ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/screenshot`, { method: "POST" });
  return res.json();
}

export async function closeBrowser(): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/close`, { method: "POST" });
  return res.json();
}

// -- settings (model API key, default model, onboarding) ----------------------
export interface SurfaceVisibility {
  cowork: boolean; // always true
  chat: boolean;
  code: boolean;
}

/** Visible governance (remote-home-design.md §Audit export): what this machine
 * exports, where, and how far along. Off everywhere unless an org policy turned it on. */
export interface AuditExportStatus {
  enabled: boolean;
  sink: "" | "cloud" | "http";
  url?: string;
  exported: number;
  pending: number;
  last_error?: string;
}

export interface ModelSettings {
  provider: string;
  model: string;
  models: string[];
  audit_export?: AuditExportStatus;
  has_key: boolean;
  model_ready: boolean; // can the default model's provider actually run (any provider)?
  source: "env" | "store" | null;
  onboarded: boolean;
  surfaces: SurfaceVisibility;
  scratch_base: string;
  secrets_path: string;  // OS-native on-disk location the server reports (not hardcoded)
  // Sidebar layout preference (§7): "flat" = the persona accordions / today's list; "grouped" =
  // bounded per-persona cards. Defaults to "flat" (absent → flat) so the GUI is robust to an older
  // backend that hasn't shipped the field yet.
  nav_layout?: "flat" | "grouped" | "machine";
  // Sidebar: sessions shown per group before "Show more" (default 5, 1–50).
  sessions_peek?: number;
  // Composer: show the context-window fill bar (default FALSE; absent → the chip shows
  // the session total). The usage popover keeps both numbers regardless.
  context_bar?: boolean;
  // Auto-Approve mode (spec §1.5): the feature flag that offers the reviewer mode, and its
  // shadow-eval sibling. Both default FALSE and are absent on older backends — the composer
  // hides the Auto-Approve mode entry unless auto_approve is explicitly true.
  auto_approve?: boolean;
  auto_approve_shadow?: boolean;
  // Curated-matrix display names ({full id → "GLM-5.2 · via Together"}); custom models absent.
  model_labels?: Record<string, string>;
  // {full id → context window in tokens}, verified matrix entries only — drives the
  // composer's context-fill meter (absent id → the meter hides). Optional for older backends.
  model_context_windows?: Record<string, number>;
  // Token savings (PDF attachments): fallback for models without native PDF support,
  // and attach-time thresholds. Optional so the GUI is robust to an older backend.
  pdf_fallback?: "text" | "images";
  pdf_max_pages?: number; // default 20, 1–100
  pdf_max_mb?: number; // default 10, 1–10
  // Auto-compaction of long histories (OPE-27): trigger = min(threshold% × context
  // window, cap tokens); model pins the summarizer ("" → the session's own model).
  // Optional so the GUI is robust to an older backend.
  compaction_threshold_pct?: number; // default 0.8, 0.10–0.95
  compaction_cap_tokens?: number; // default 250000
  compaction_model?: string;
}

export interface PdfSettings {
  pdf_fallback: "text" | "images";
  pdf_max_pages: number;
  pdf_max_mb: number;
}

/** Persist the Token-savings PDF settings (fallback mode + attach thresholds). */
export async function setPdfSettings(
  patch: Partial<PdfSettings>,
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string } & Partial<PdfSettings>> {
  const res = await fetch(`${engineBase(machineId)}/v1/settings/pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

export interface CompactionSettings {
  compaction_threshold_pct: number;
  compaction_cap_tokens: number;
  compaction_model: string;
}

/** Persist the auto-compaction overrides (threshold %, token cap, summarizer model). */
export async function setCompactionSettings(
  patch: Partial<CompactionSettings>,
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/settings/compaction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

/** Local page/size probe for a PDF data URL — the composer's attach-time threshold check. */
export async function inspectPdf(
  dataUrl: string,
): Promise<{ ok: boolean; pages?: number; bytes?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/attachments/inspect-pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_url: dataUrl }),
  });
  return res.json();
}

/** Persist whether the composer shows the context-window fill bar. */
export async function setContextBar(
  shown: boolean,
): Promise<{ ok: boolean; context_bar?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/context-bar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context_bar: shown }),
  });
  return res.json();
}

type AutoApproveResult = {
  ok: boolean;
  auto_approve?: boolean;
  auto_approve_shadow?: boolean;
  error?: string;
};

/** Toggle the Auto-Approve feature flag (spec §1.5); applies to the next session build. */
export async function setAutoApprove(on: boolean): Promise<AutoApproveResult> {
  const res = await fetch(`${httpBase()}/v1/settings/auto-approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_approve: on }),
  });
  return res.json();
}

/** Toggle shadow evaluation (Part 6 step 3): the reviewer records but never decides. */
export async function setAutoApproveShadow(on: boolean): Promise<AutoApproveResult> {
  const res = await fetch(`${httpBase()}/v1/settings/auto-approve-shadow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_approve_shadow: on }),
  });
  return res.json();
}

/** Persist how many sessions a sidebar group shows before "Show more". */
export async function setSessionsPeek(
  n: number,
): Promise<{ ok: boolean; sessions_peek?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/sessions-peek`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessions_peek: n }),
  });
  return res.json();
}

export async function setScratchBase(
  path: string,
): Promise<{ ok: boolean; error?: string; scratch_base?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/scratch-base`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return res.json();
}

export async function setSurfaces(
  flags: { chat?: boolean; code?: boolean },
): Promise<{ ok: boolean; surfaces: SurfaceVisibility }> {
  const res = await fetch(`${httpBase()}/v1/settings/surfaces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(flags),
  });
  return res.json();
}

/** Persist the sidebar layout preference (flat ↔ grouped-by-persona); read back from getSettings. */
export async function setNavLayout(
  layout: "flat" | "grouped" | "machine",
): Promise<{ ok: boolean; nav_layout?: "flat" | "grouped" | "machine"; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/nav-layout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nav_layout: layout }),
  });
  return res.json();
}

// Fired after a cloud sign-in/out completes so the account row (§26) refreshes without
// waiting for the next window focus.
export const CLOUD_CHANGED = "coworker:cloud-changed";
export function announceCloudChanged() {
  window.dispatchEvent(new CustomEvent(CLOUD_CHANGED));
}

// Fired the first time Inbox machinery is engaged (an item parks, or a session goes
// Unattended) — the account row's inbox chip unlocks stickily on it (§26).
export const INBOX_UNLOCK = "coworker:inbox-unlock";
export function announceInboxUnlock() {
  window.dispatchEvent(new CustomEvent(INBOX_UNLOCK));
}

// -- Personas -----------------------------------------------------------------

// Fired after any persona mutation (enable/disable/install/delete) so always-mounted
// consumers (the sidebar's new-session picker) refetch instead of going stale.
export const PERSONAS_CHANGED = "coworker:personas-changed";
function announcePersonasChanged() {
  window.dispatchEvent(new CustomEvent(PERSONAS_CHANGED));
}

export interface TeamMemberDecision {
  persona: string;
  name?: string;
  connectors: string[];
  approval_guidance?: string;
  // The human's FINAL model choice for this worker — sent only when the gate offered
  // a model picker (the server supplied `runnable_models`).
  model?: string;
}

export interface Persona {
  id: string;
  name: string;
  icon: string;
  tagline: string;
  requires_folder: boolean; // folder gate — drives project-scoping
  builtin: boolean;
  tools: string[];
  enabled: boolean;
  surfaced: boolean;
  default: boolean;
  // Distribution flag (ships:false = internal builds only) + settings-page group.
  ships?: boolean;
  group?: string; // "general" | "security"
  version?: string;
  installed_at?: string;
  // Ordered allowed models (spec §4). Empty/absent = any model. `models_available` =
  // the entries THIS machine can run (its keys, its Ollama), annotated by the server.
  models?: string[];
  models_available?: string[];
}

export interface PersonaConsent {
  id: string;
  name: string;
  description: string;
  tools: string[];
  risk: string[];
  // "all" (general builtins) or the declared allowlist — [] means no connector access.
  connectors: "all" | string[];
  mcp: string[];
  messaging: boolean;
  recommended_mode: string;
  models?: string[];
  recommended_models: string[]; // old name of `models`, one release
  recommends?: { kind: string; ref: string; reason: string; tier: string }[];
  version?: string;
  replaces?: { version: string; installed_at: string; capabilities_grew: boolean } | null;
  source: string | null;
  builtin: boolean;
}

export async function getPersonas(): Promise<Persona[]> {
  const res = await fetch(`${httpBase()}/v1/personas`);
  return (await res.json()).personas;
}

/** Personas plus the build flag: `internal` builds may show unshipped coworkers + the Gallery. */
/** Session list of ONE machine (controller view): live rows when connected,
 * the stored snapshot otherwise. */
export async function getMachineSessionsList(machineId: string): Promise<SessionInfo[]> {
  const res = await fetch(`${machineApi(machineId)}/sessions`);
  return (await res.json()).sessions ?? [];
}

export async function getPersonasIndex(machineId?: string | null): Promise<{ personas: Persona[]; internal: boolean }> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas`);
  const body = await res.json();
  return { personas: body.personas ?? [], internal: !!body.internal };
}

export async function updatePersona(
  id: string,
  body: { enabled?: boolean; surfaced?: boolean; default?: boolean },
  machineId?: string | null,
): Promise<{ ok: boolean; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await res.json();
  if (out.ok !== false) announcePersonasChanged();
  return out;
}

/** Uninstall a non-builtin persona (its snapshot + state). Local; works signed out. */
export async function deletePersona(
  id: string,
  machineId?: string | null,
): Promise<{ ok: boolean; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  const out = await res.json();
  if (out.ok) announcePersonasChanged();
  return out;
}

// A curated persona card from the cloud gallery (metadata only — the manifest
// is fetched server-side at install and runs through the normal consent flow).
export interface GalleryPersona {
  slug: string;
  version: number;
  name: string;
  icon: string;
  tagline: string;
  description: string;
  family: string;
  workspace: string;
  publisher: string;
  recommended_connectors: string[];
  risk_summary: string;
  featured?: boolean; // publisher-flagged for the gallery's featured carousel
}

export async function getCloudGallery(): Promise<{
  ok: boolean;
  personas: GalleryPersona[];
  error?: string;
}> {
  const res = await fetch(`${httpBase()}/v1/cloud/gallery`);
  return res.json();
}

// Solo page for one gallery coworker. `capabilities` is the desktop's own
// consent summary derived from the manifest (same parser as install), so the
// page shows exactly what installing would ask the user to approve.
export interface GalleryDetail {
  ok: boolean;
  error?: string;
  card?: GalleryPersona & { pitch_markdown: string };
  capabilities?: {
    tools: string[];
    risk: string[];
    connectors: boolean;
    mcp: string[];
    messaging: boolean;
    recommended_mode: string;
    recommended_models: string[];
  };
  recommends?: { kind: string; ref: string; reason: string; tier: string }[];
}

export async function getCloudGalleryDetail(slug: string): Promise<GalleryDetail> {
  const res = await fetch(`${httpBase()}/v1/cloud/gallery/${encodeURIComponent(slug)}`);
  return res.json();
}

/** Sharing v1 (OPE-7): zip a coworker's bundle into `dir`; the zip is the import format. */
export async function exportPersona(
  id: string,
  dir: string,
): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir }),
  });
  return res.json();
}

export async function installPersona(
  body: { dir?: string; git_url?: string; gallery_slug?: string; zip_b64?: string; filename?: string },
  machineId?: string | null,
): Promise<{ ok: boolean; consent?: PersonaConsent[]; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await res.json();
  if (out.ok) announcePersonasChanged();
  return out;
}

// -- Persona detail + connection defaults (§5) --------------------------------
// A persona's declared recommendation (manifest `recommends`): a connector or MCP server it works
// best with, with a reason + tier (core/optional). `connected` is annotated server-side from the
// connector list so the detail page can show connect state without a second round-trip.
export interface PersonaRecommendation {
  kind: string; // "connector" | "mcp" | …
  ref: string; // connector id (e.g. "github") or mcp/server name
  reason: string;
  tier: string; // "core" | "optional"
  connected: boolean;
}

// A persona-default connection (the middle of the §4 hierarchy): for a connected connector, whether
// new sessions of this persona get it enabled by default.
export interface PersonaDefaultConnection {
  connector: string; // connector id
  enabled: boolean; // persona-default on/off
  connected: boolean; // is the account actually connected (else the toggle is disabled)
}

export interface PersonaDetail {
  id: string;
  name: string;
  icon: string;
  tagline: string;
  description: string;
  media: string[]; // bundle media/ screenshots, served via /v1/personas/{id}/media/{name}
  builtin: boolean;
  group: string;
  enabled: boolean; // persona on/off (shown in the picker)
  surfaced: boolean;
  default: boolean;
  tools: string[];
  models?: string[]; // ordered allowed models (spec §4); [] = any
  models_available?: string[]; // the ones this machine can run
  recommended_models: string[]; // old name of `models`, one release
  default_permission_mode: string;
  requires_folder: boolean; // folder gate (workspace-scratch-design.md)
  recommends: PersonaRecommendation[];
  default_connections: PersonaDefaultConnection[];
}

/** Fetch one bundle screenshot with launch auth and hand back an object URL. */
export async function getPersonaMediaUrl(id: string, name: string, machineId?: string | null): Promise<string> {
  const res = await fetch(
    `${engineBase(machineId)}/v1/personas/${encodeURIComponent(id)}/media/${encodeURIComponent(name)}`,
  );
  if (!res.ok) throw new Error(`media ${name}: ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

export async function getPersonaDetail(id: string, machineId?: string | null): Promise<PersonaDetail> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas/${encodeURIComponent(id)}`);
  return res.json();
}

/** Set a persona-default connection (new sessions of this persona get it on/off by default). */
export async function setPersonaConnection(
  id: string,
  connector: string,
  enabled: boolean,
  machineId?: string | null,
): Promise<{ ok: boolean; default_connections?: PersonaDefaultConnection[]; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas/${encodeURIComponent(id)}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector, enabled }),
  });
  return res.json();
}

/** Enable/disable the persona (whether it surfaces in the new-session picker). */
export async function setPersonaEnabled(
  id: string,
  enabled: boolean,
  machineId?: string | null,
): Promise<{ ok: boolean; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/personas/${encodeURIComponent(id)}/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const out = await res.json();
  if (out.ok) announcePersonasChanged();
  return out;
}

// -- Per-session connections (Sources bar + drawer, §6) -----------------------
// An effective-enabled connector for a session, with a short human detail (e.g. "#ocw-test · DMs").
// `enabled` reflects the session override/persona default so the drawer toggle shows correct state.
export interface SessionConnectedConnector {
  connector: string;
  enabled: boolean;
  detail: string;
}

// A persona-recommended connector not yet connected (drives the `⚠ N` attention count).
export interface SessionRecommendedConnector {
  connector: string;
  reason: string;
  tier: string;
  connected: boolean;
}

export interface SessionConnections {
  connected: SessionConnectedConnector[];
  recommended: SessionRecommendedConnector[];
  attention: number; // ⚠ count = recommended connectors not yet connected
}

/** `persona` = the active persona hint — required for brand-new sessions (no server-side
 * record yet), otherwise the view resolves to the default persona's defaults/recommends. */
export async function getSessionConnections(
  sessionId: string,
  persona?: string,
): Promise<SessionConnections> {
  const q = persona ? `?persona=${encodeURIComponent(persona)}` : "";
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/connections${q}`,
  );
  return res.json();
}

/**
 * Set a per-session connection override (mute/unmute a connector for THIS session). Pass
 * `clear: true` to drop the override and inherit the persona default again.
 */
export async function setSessionConnection(
  sessionId: string,
  connector: string,
  enabled: boolean,
  clear = false,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector, enabled, ...(clear ? { clear: true } : {}) }),
  });
  return res.json();
}

// -- Skills (SKILLS-SPEC §4) ----------------------------------------------------
// Scope = folder location: "global" (every session) or "project" (one workspace).
// The session endpoints resolve the effective menu (Settings disables + session mutes).

export interface SkillRow {
  name: string;
  description: string;
  instructions: string;
  scope: "global" | "project";
  source: string; // "local" | "uploaded"
  enabled: boolean;
  path: string;
  files?: number; // bundled resources beyond SKILL.md (§6 — rich skills are visible)
}

export interface SessionSkillRow {
  name: string;
  description: string;
  scope: "global" | "project";
  enabled: boolean; // false = muted for this session only
}

export interface SkillUploadPreview {
  ok: boolean;
  error?: string;
  token?: string;
  name?: string;
  description?: string;
  instructions?: string;
  files?: string[];
}

const skillUrl = (path = "", machineId?: string | null) =>
  `${engineBase(machineId)}/v1/skills${path}`;
const jsonPost = (body: unknown, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function listSkills(workspace?: string, machineId?: string | null): Promise<SkillRow[]> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(skillUrl(qs, machineId));
  return (await res.json()).skills ?? [];
}

export async function createSkill(body: {
  name: string;
  description: string;
  instructions: string;
  scope?: "global" | "project";
  workspace?: string;
}, machineId?: string | null): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl("", machineId), jsonPost(body));
  return res.json();
}

export async function updateSkill(
  name: string,
  patch: { description?: string; instructions?: string; enabled?: boolean; workspace?: string },
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}`, machineId), jsonPost(patch, "PATCH"));
  return res.json();
}

export async function revealSkill(name: string): Promise<{ ok: boolean; error?: string }> {
  // §6 "Show folder": the backend opens the skill's folder in the OS file manager.
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}/reveal`), jsonPost({}));
  return res.json();
}

export async function deleteSkill(
  name: string,
  workspace?: string,
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}${qs}`, machineId), { method: "DELETE" });
  return res.json();
}

export async function moveSkill(
  name: string,
  scope: "global" | "project",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}/move`), jsonPost({ scope, workspace }));
  return res.json();
}

export async function stageSkillUpload(
  dataB64: string,
  filename = "",
  machineId?: string | null,
): Promise<SkillUploadPreview> {
  const res = await fetch(skillUrl("/upload", machineId), jsonPost({ data_b64: dataB64, filename }));
  return res.json();
}

export async function confirmSkillUpload(
  token: string,
  scope: "global" | "project" = "global",
  workspace?: string,
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl("/upload/confirm", machineId), jsonPost({ token, scope, workspace }));
  return res.json();
}


export async function sessionSkills(
  sessionId: string,
  workspace?: string,
): Promise<SessionSkillRow[]> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/skills${qs}`,
  );
  return (await res.json()).skills ?? [];
}

export async function setSessionSkill(
  sessionId: string,
  skill: string,
  enabled: boolean,
  opts: { clear?: boolean; workspace?: string } = {},
): Promise<{ skills?: SessionSkillRow[]; ok?: boolean; error?: string }> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/skills`,
    jsonPost({
      skill,
      enabled,
      ...(opts.clear ? { clear: true } : {}),
      ...(opts.workspace ? { workspace: opts.workspace } : {}),
    }),
  );
  return res.json();
}

// -- Inbox + Unattended -------------------------------------------------------
export interface InboxItem {
  id: string;
  tool_call_id?: string;
  session_id: string;
  kind: "approval" | "question" | "notification" | "directory" | "plan" | "tool" | "connector";
  title: string;
  body: string;
  state: "pending" | "resolved";
  resolution: string | null;
  inbox: string;
  created_at: string;
  resolved_at: string | null;
  visibility?: "inline" | "inbox";
  // Question metadata (ask_user): quick-reply choices + a free-text escape. Options may be rich
  // {label, description, recommended, preview} objects (OPE-51); `questions` is the grouped form
  // (stepper), whose resolution is a JSON object string keyed by header-or-question.
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
  header?: string;
  questions?: GroupedQuestion[];
  // Kind-specific payload (directory: {path, writable}; …).
  data?: Record<string, any>;
  // Originating-session context (server-joined) so the Inbox is self-contained.
  session_title?: string;
  session_agent?: string | null;
  session_workspace?: string | null;
  session_exists?: boolean;
  // Remote homes: set when the item came from a joined machine's inbox — the
  // Inbox aggregates every connected machine (⌂ tag; resolve routes back).
  machine?: string;
  machine_name?: string;
}

// Which machine each aggregated inbox item came from, so resolve routes back
// to the box that parked it. Fed by getInbox; ids are uuids, so cross-machine
// collisions are not a practical concern.
const inboxItemMachines = new Map<string, string>();

/** A worker's waiting call lives on the same machine as the lead's item that names it, but
 *  was never fetched here. Route it like that item (or like that session) before resolving. */
export function routeInboxItemLike(id: string, like: { itemId?: string; sessionId?: string }): void {
  const mid =
    (like.itemId && inboxItemMachines.get(like.itemId)) || (like.sessionId && machineOfSession(like.sessionId)) || "";
  if (id && mid) inboxItemMachines.set(id, mid);
}

export async function getInbox(sessionId?: string, state?: string): Promise<InboxItem[]> {
  const q = new URLSearchParams();
  if (sessionId) q.set("session_id", sessionId);
  if (state) q.set("state", state);
  // Per-session view: query the session's OWN home (local or its machine).
  if (sessionId) {
    const base = sessionApiBase(sessionId);
    const res = await fetch(`${base}/v1/inbox?${q.toString()}`);
    const items: InboxItem[] = (await res.json()).items ?? [];
    const mid = machineOfSession(sessionId);
    if (mid) for (const it of items) inboxItemMachines.set(it.id, mid);
    return items;
  }
  // Cross-session Inbox: aggregate this Mac + every CONNECTED machine —
  // joined AND cloud (union v1.5: a cloud box's parked approval was invisible
  // before this, a silent stall the machine-events drill hit live). An
  // offline box's parked items are unreachable AND unanswerable — listing them
  // would only offer dead buttons; they return with the machine.
  const local = fetch(`${httpBase()}/v1/inbox?${q.toString()}`)
    .then(async (r) => ((await r.json()).items ?? []) as InboxItem[])
    .catch(() => [] as InboxItem[]);
  const machineInbox = async (m: Machine): Promise<InboxItem[]> => {
    try {
      const r = await fetch(`${engineBase(m.id)}/v1/inbox?${q.toString()}`);
      const items: InboxItem[] = (await r.json()).items ?? [];
      for (const it of items) {
        it.machine = m.id;
        it.machine_name = m.name;
        inboxItemMachines.set(it.id, m.id);
      }
      return items;
    } catch {
      return [] as InboxItem[];
    }
  };
  const joined = getMachines()
    .then(({ machines }) => machines)
    .catch(() => [] as Machine[]);
  const cloud = getCloudMachines()
    .then((info) => (info.session === "ok" ? info.machines : []))
    .catch(() => [] as Machine[]);
  const remote = Promise.all([joined, cloud])
    .then((lists) =>
      Promise.all(lists.flat().filter((m) => m.connected).map(machineInbox)),
    )
    .then((lists) => lists.flat())
    .catch(() => [] as InboxItem[]);
  const [mine, theirs] = await Promise.all([local, remote]);
  // Oldest-first within the merge, matching the server's ordering instinct.
  return [...mine, ...theirs].sort((a, b) =>
    (a.created_at || "").localeCompare(b.created_at || ""),
  );
}

export async function resolveInboxItem(
  id: string,
  resolution: string,
): Promise<{ ok: boolean }> {
  const mid = inboxItemMachines.get(id);
  const base = engineBase(mid);
  const res = await fetch(`${base}/v1/inbox/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution }),
  });
  return res.json();
}

// -- channel subscriptions (view-only) ----------------------------------------
export interface Subscription {
  session_id: string;
  session_title: string;
  agent: string;
  channel: string;
  channel_name?: string | null; // resolved display name ("ocw-test"); address stays the id
  routing_target: string | null;
  collision: boolean; // inbound subscription == outbound Inbox routing on the same channel
}

export interface RecentChannel {
  channel: string;
  name?: string | null; // resolved display name, e.g. "ocw-test" (falls back to the address)
  last_from: string | null;
  last_text: string | null;
}

/** A machine's channel subscriptions (session → channel). Session-scoped
 * callers pass the session's machine: the list lives on the engine that owns
 * the session, and the dashboard's own origin answers with something else. */
export async function getSubscriptions(machineId?: string | null): Promise<Subscription[]> {
  const res = await fetch(`${engineBase(machineId)}/v1/subscriptions`);
  return (await res.json()).subscriptions ?? [];
}

// -- inbox routing (where Unattended approvals/questions get mirrored) ---------
export interface InboxBinding {
  name: string;
  channel: string | null; // platform, e.g. "slack" (null = in-app Inbox only)
  target: string; // chat_id, e.g. "C0BEJNCQQ8Y"
}

export async function getInboxRouting(machineId?: string | null): Promise<InboxBinding[]> {
  const res = await fetch(`${engineBase(machineId)}/v1/inbox/routing`);
  return (await res.json()).bindings ?? [];
}

/** Approvals routing is per machine (UX-049): pass the machine whose
 * sessions' approvals this binding governs; default = the local engine. */
export async function setInboxBinding(
  name: string,
  channel: string | null,
  target: string,
  machineId?: string | null,
): Promise<{ ok: boolean; bindings?: InboxBinding[]; error?: string }> {
  const res = await fetch(`${engineBase(machineId)}/v1/inbox/routing/binding`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, channel, target }),
  });
  return res.json();
}

export interface UnroutedItem {
  source: string;
  sender: string;
  text: string;
  reason: string;
  ts: number;
}

export async function getUnrouted(): Promise<UnroutedItem[]> {
  const res = await fetch(`${httpBase()}/v1/unrouted`);
  return (await res.json()).items ?? [];
}

export async function getRecentChannels(machineId?: string | null): Promise<RecentChannel[]> {
  const res = await fetch(`${engineBase(machineId)}/v1/channels/recent`);
  return (await res.json()).channels ?? [];
}

/** The session already answering a source, as the cloud reports it (one
 * session across all of the user's machines answers a channel or repo). */
export interface SubscriptionHolder {
  session_id: string;
  title: string;
  machine_id: string; // "desktop" = This Mac
  source: string;
}

export interface SubscribeResult {
  ok: boolean;
  channel?: string;
  error?: string; // "held" when another session answers it — see held_by
  held_by?: SubscriptionHolder;
  also?: SubscriptionHolder[];
  move_allowed?: boolean;
  registered?: boolean; // false = local-only (nothing managed to register)
}

/** Subscribe goes to the machine that owns the session (its box registers the
 * claim at the cloud before writing locally); `move` takes over a held source. */
export async function subscribeChannel(
  sessionId: string,
  channel: string,
  opts: { move?: boolean } = {},
): Promise<SubscribeResult> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, channel, move: !!opts.move }),
  });
  return res.json();
}

export async function unsubscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; removed?: boolean }> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/subscriptions/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, channel }),
  });
  return res.json();
}

export async function getUnattended(sessionId: string): Promise<boolean> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/unattended`,
  );
  return (await res.json()).unattended;
}

export async function setUnattended(
  sessionId: string,
  unattended: boolean,
): Promise<{ ok: boolean; unattended: boolean }> {
  const res = await fetch(
    `${sessionApiBase(sessionId)}/v1/sessions/${encodeURIComponent(sessionId)}/unattended`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unattended }),
    },
  );
  return res.json();
}

// Auto-Approve metering (§1.7): per-session reviewer counts from the durable audit rows.
export interface ReviewerBucket {
  checks: number;
  allow: number;
  deny: number;
  unsure: number;
  tokens_in: number;
  tokens_out: number;
  // Cached-prefix share of the input, billed at ~10%. Without it the badge only ever
  // showed the FRESH tokens — a fraction of what a check really processes.
  cache_read: number;
  cache_write: number;
}
export interface ReviewerStats {
  live: ReviewerBucket;   // the mode actually deciding (Mode.AUTO_APPROVE)
  shadow: ReviewerBucket; // shadow evaluation: recorded next to the human's own decisions
}

export async function getReviewerStats(sessionId: string): Promise<ReviewerStats> {
  const res = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${sessionId}/reviewer-stats`);
  return res.json();
}

// -- Settings ▸ Sandbox (UX-051 A): machine-level provider, network profile, credential grants --
export interface SandboxCredentialEntry {
  name: string;
  title?: string;
  path?: string;
  hosts?: string[];
  does?: string;
  enabled: boolean;
}
export interface SandboxSettings {
  platform: string;
  provider: string; // "" = the default rule
  effective_provider: string;
  refused: string;
  providers: { name: string; usable: boolean; why: string }[];
  network_profile: string;
  network_profiles: { name: string; hosts: string[] }[];
  credentials: SandboxCredentialEntry[];
  config_path: string;
}

export async function getSandboxSettings(machineId?: string | null): Promise<SandboxSettings> {
  const res = await fetch(`${engineBase(machineId)}/v1/settings/sandbox`);
  return res.json();
}

export async function setSandboxSettings(
  patch: Partial<Pick<SandboxSettings, "provider" | "network_profile" | "credentials">>,
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string } & Partial<SandboxSettings>> {
  const res = await fetch(`${engineBase(machineId)}/v1/settings/sandbox`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

export async function getSettings(machineId?: string | null): Promise<ModelSettings> {
  const res = await fetch(`${engineBase(machineId)}/v1/settings`);
  return res.json();
}

export async function setModelKey(
  apiKey: string,
): Promise<{ ok: boolean; error?: string; has_key?: boolean; source?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/model-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  return res.json();
}

export async function setDefaultModel(
  model: string,
): Promise<{ ok: boolean; error?: string; model?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/default-model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return res.json();
}

export async function addModel(model: string): Promise<ModelSettings & { ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/models/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return res.json();
}

export async function removeModel(model: string): Promise<ModelSettings & { ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/models/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return res.json();
}

export async function setOnboarded(value: boolean): Promise<{ ok: boolean; onboarded: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/onboarded`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  return res.json();
}

// -- Memory (MEMORY-SPEC §5.3/§6: the memory screen, user rules, toast Undo) ----

export interface MemoryEntry {
  id: number;
  scope: string;
  content: string;
  summary: string;
  created_at: string;
}

export interface MemorySettings {
  enabled: boolean;
  user_rules: string;
}

// Fired whenever memory changes from OUTSIDE the memory screen — today the agent
// saving or editing one mid-conversation. The screen only loads its list on mount, so
// without this it sits there stale and the user reads "Nothing yet" seconds after a
// save actually landed (owner-hit 2026-07-28).
export const MEMORY_CHANGED = "coworker:memory-changed";
export function announceMemoryChanged() {
  window.dispatchEvent(new CustomEvent(MEMORY_CHANGED));
}

export async function getMemory(machineId?: string | null): Promise<MemoryEntry[]> {
  const res = await fetch(`${engineBase(machineId)}/v1/memory`);
  return (await res.json()).memory ?? [];
}

export async function updateMemory(
  id: number,
  content: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return res.json();
}

export async function deleteMemory(id: number): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/${id}`, { method: "DELETE" });
  return res.json();
}

export async function deleteAllMemory(): Promise<{ ok: boolean; deleted: number }> {
  const res = await fetch(`${httpBase()}/v1/memory`, { method: "DELETE" });
  return res.json();
}

export async function getMemorySettings(machineId?: string | null): Promise<MemorySettings> {
  const res = await fetch(`${engineBase(machineId)}/v1/memory/settings`);
  return res.json();
}

export async function setMemorySettings(
  patch: Partial<MemorySettings>,
): Promise<MemorySettings> {
  const res = await fetch(`${httpBase()}/v1/memory/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

// -- model providers (OpenAI, Ollama, …) --------------------------------------
export interface ProviderField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
  default?: string; // pre-filled editable value (e.g. an OpenAI-compatible vendor's endpoint)
  // Non-empty → segmented choice, not a text input. tag = tiny badge ("Easiest");
  // desc = one-liner atop the method panel; command = copyable terminal command.
  choices?: { value: string; label: string; tag?: string; desc?: string; command?: string }[];
  show_when?: Record<string, string> | null; // render only while these fields hold these values
}

export interface ProviderInfo {
  name: string;
  title: string;
  needs_key: boolean;
  fields: ProviderField[];
  configured: boolean;
  values: Record<string, string>; // non-secret stored values (e.g. base_url), for prefilling
  suggested_models: string[]; // bare model-name suggestions for the "add model" datalist
  recommended_model: string | null; // pre-filled default for this provider (e.g. qwen3-coder:30b)
  blurb?: string; // one-line note under the title ("Uses X's OpenAI-compatible API…")
  key_set_at?: string | null; // ISO date the key was last (re)saved — absent for env-only config
  last_used_at?: number | null; // epoch secs the provider last served a completion
  // OAuth providers (auth === "oauth"): browser sign-in instead of a key form.
  auth?: string | null;
  signed_in?: boolean;
  account?: string | null; // signed-in account label (email or id)
  authorizing?: boolean;
  last_error?: string | null;
}

// -- ChatGPT-subscription provider sign-in (OAuth; tokens never reach the GUI) ------
export interface CodexAuthStatus {
  signed_in: boolean;
  account?: string | null;
  authorizing: boolean;
  last_error?: string | null;
  authorize_url?: string | null;
}

export async function codexSignin(): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/providers/openai-codex/signin`, { method: "POST" });
  return res.json();
}

export async function codexAuthStatus(): Promise<CodexAuthStatus> {
  const res = await fetch(`${httpBase()}/v1/providers/openai-codex/status`);
  return res.json();
}

export async function codexSignout(): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/providers/openai-codex/signout`, { method: "POST" });
  return res.json();
}

export async function getProviders(): Promise<ProviderInfo[]> {
  const res = await fetch(`${httpBase()}/v1/providers`);
  const data = await res.json();
  // A backend without providers (the hosted control plane — models live on
  // each machine) answers 404 JSON; an object where an array belongs must
  // not reach render code (it crashed the whole app, owner-hit 2026-08-30).
  return Array.isArray(data) ? data : [];
}

export async function setProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string; provider?: string; recommended_model?: string | null }> {
  const res = await fetch(`${httpBase()}/v1/providers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, fields }),
  });
  return res.json();
}

/** Forget a provider's stored config (Settings ▸ Models "Remove key…"). */
export async function removeProvider(name: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  return res.json();
}

/** Live read-only credential check (does NOT save the key). Triggered by the user's "Test" click. */
export async function verifyProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, fields }),
  });
  return res.json();
}

/** Client-side provider guess from an API key's shape (mirrors the server's detect_provider). */
export function detectProvider(apiKey: string): string | null {
  const key = (apiKey || "").trim();
  if (!key) return null;
  if (key.startsWith("sk-ant-")) return "anthropic";
  if (key.startsWith("sk-or-")) return "openrouter";
  if (key.startsWith("AIza")) return "gemini";
  if (key.startsWith("sk-") || key.startsWith("sk_")) return "openai";
  return null;
}

// -- super-agent --------------------------------------------------------------
export interface RecentSender {
  user_id: string;
  user_name: string | null;
  chat_id: string;
  chat_type: string;
  target: string;
  authorized: boolean;
  team_id?: string | null; // workspace (managed relay); null on manual Socket Mode
}

// -- direct-message routing ---------------------------------------------------
export async function getDmRoute(): Promise<string | null> {
  const res = await fetch(`${httpBase()}/v1/messaging/dm-route`);
  return (await res.json()).dm_session ?? null;
}

export async function setDmRoute(sessionId: string): Promise<{ ok: boolean; dm_session: string | null }> {
  const res = await fetch(`${httpBase()}/v1/messaging/dm-route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return res.json();
}

// -- automations (scheduled tasks) --------------------------------------------
export interface Automation {
  id: string;
  title: string;
  instructions: string;
  schedule: string;
  schedule_raw?: { kind: string; cron?: string | null; fire_at?: string | null; timezone?: string };
  workspace: string;
  agent: string;
  enabled: boolean;
  next_run: number | null;
  last_run: number | null;
  last_status: string | null;
  run_count: number;
  notify_on_completion: boolean;
  // UX-023 sidebar badges: runs started since the user last opened this automation's
  // detail; `unseen_failed` = the newest unseen run errored (danger tint).
  unseen_runs?: number;
  unseen_failed?: boolean;
  seen_runs_at?: number;
  // Standing scoped approvals (§25): target-bound rules this automation may exercise
  // without asking. `entry` is the raw record entry — the revoke handle; `target` is
  // null for legacy name-only entries.
  always_allowed: { entry: string; tool: string; target: string | null }[];
}

export interface AutomationRun {
  run_id: string;
  task_id: string;
  session_id: string;
  started_at: number;
  finished_at: number | null;
  status: string;
  result_text: string | null;
  artifacts: string[];
  error: string | null;
  trigger: string;
}

export async function getAutomations(): Promise<Automation[]> {
  const res = await fetch(`${httpBase()}/v1/automations`);
  return (await res.json()).tasks ?? [];
}

// Fired after any automation mutation the sidebar should reflect immediately
// (mark-seen, create, delete) — its poll covers the rest.
export const AUTOMATIONS_CHANGED = "coworker:automations-changed";
export function announceAutomationsChanged() {
  window.dispatchEvent(new CustomEvent(AUTOMATIONS_CHANGED));
}

/** App-wide event stream (/ws/events): session-independent server pushes — today
 * automation_run_started (the UX-026 toast). Quietly reconnects while the app is
 * open; the returned cleanup stops it for good. */
export function connectEvents(
  onEvent: (msg: { type: string; data?: Record<string, unknown> }) => void
): () => void {
  let ws: WebSocket | null = null;
  let timer: number | null = null;
  let closed = false;
  const open = () => {
    if (closed) return;
    ws = openWebSocket(`${wsBase()}/ws/events`);
    ws.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    ws.onclose = () => {
      if (!closed) timer = window.setTimeout(open, 5000);
    };
  };
  open();
  return () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    ws?.close();
  };
}

/** Advance the automation's seen mark — clears its unseen-runs badge (UX-023). */
export async function markAutomationSeen(id: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/automations/${id}/seen`, { method: "POST" });
  return res.json();
}

export async function createAutomation(payload: {
  title: string;
  instructions: string;
  cron?: string;
  fire_at?: string;
  timezone?: string;
  // §25 standing grants (the creating surface rendered them; submit IS the consent).
  // Only target-bound write entries survive server-side validation.
  permissions?: { tool: string; target: string; access: "read" | "write" }[];
}): Promise<{ ok: boolean; error?: string; task?: Automation }> {
  const res = await fetch(`${httpBase()}/v1/automations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function getAutomation(id: string): Promise<{ task: Automation; runs: AutomationRun[] }> {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`);
  return res.json();
}

export async function updateAutomation(id: string, changes: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function deleteAutomation(id: string) {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`, { method: "DELETE" });
  return res.json();
}

export interface PreparedRun {
  ok: boolean;
  error?: string;
  run_id: string;
  session_id: string;
  workspace: string;
  agent: string;
  prompt: string;
}

/** Prepare a live manual run: returns the session to open + the opening prompt to send. */
export async function runAutomation(id: string): Promise<PreparedRun> {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}/run`, { method: "POST" });
  return res.json();
}

/** Mark a manual run complete after its first turn finished. */
export async function finalizeAutomationRun(id: string, runId: string) {
  const res = await fetch(
    `${httpBase()}/v1/automations/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}/finalize`,
    { method: "POST" },
  );
  return res.json();
}

export async function allowUser(
  name: string,
  userId: string,
  teamId?: string | null,
  displayName?: string,
) {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/allow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      ...(teamId ? { team_id: teamId } : {}),
      // Directory picks carry the display name so the chip is readable at once.
      ...(displayName ? { name: displayName } : {}),
    }),
  });
  return res.json();
}

// One workspace member from the roster (people picker; users:read, cached locally).
export interface SlackMember {
  id: string;
  name: string;
  handle: string;
  guest: boolean;
}

// One channel from the workspace roster. Private channels appear only where the
// bot is a member (Slack API constraint); is_member=false → "invite @OpenWorker" hint.
export interface SlackChannelEntry {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

/** Workspace member roster for the people picker (teamId "default" = manual Socket Mode). */
export async function getSlackDirectory(
  teamId: string,
  q = "",
): Promise<{ ok: boolean; error?: string; members?: SlackMember[] }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/slack/workspaces/${encodeURIComponent(teamId)}/directory?q=${encodeURIComponent(q)}`,
  );
  return res.json();
}

/** Channel roster for the channel typeahead (name → id resolution). */
export async function getSlackChannels(
  teamId: string,
  q = "",
  machineId?: string | null,
): Promise<{ ok: boolean; error?: string; channels?: SlackChannelEntry[] }> {
  const res = await fetch(
    `${engineBase(machineId)}/v1/connectors/slack/workspaces/${encodeURIComponent(teamId)}/channels?q=${encodeURIComponent(q)}`,
  );
  return res.json();
}

/** Resolve a parked unauthorized message (§19): dismiss / allow / allow_deliver. */
export async function resolveUnauthorized(
  name: string,
  itemId: string,
  action: "dismiss" | "allow" | "allow_deliver",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/unauthorized/${encodeURIComponent(itemId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    },
  );
  return res.json();
}

export async function disallowUser(name: string, userId: string, teamId?: string | null) {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/disallow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(teamId ? { user_id: userId, team_id: teamId } : { user_id: userId }),
  });
  return res.json();
}

export async function addSlackApprovalOwner(
  userId: string,
  displayName?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/approval-owners/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      ...(displayName ? { name: displayName } : {}),
    }),
  });
  return res.json();
}

export async function removeSlackApprovalOwner(
  userId: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/approval-owners/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  return res.json();
}

/** Stop relaying one managed Slack workspace (the app stays installed in Slack). */
export async function disconnectSlackWorkspace(teamId: string): Promise<{ ok: boolean; error?: string; remaining_workspaces?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/slack/workspaces/${encodeURIComponent(teamId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE Gmail mailbox; the default pointer moves to the next account. */
export async function disconnectGmailAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/gmail/accounts/${encodeURIComponent(email)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setGmailDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/gmail/accounts/${encodeURIComponent(email)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE Google Calendar account; the default pointer moves to the next one. */
export async function disconnectGcalAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/google_calendar/accounts/${encodeURIComponent(email)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setGcalDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/google_calendar/accounts/${encodeURIComponent(email)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE account of a generic multi-account connector (notion, attio,
 * posthog, …); the default pointer moves to the next account. */
export async function disconnectAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(connector)}/accounts/${encodeURIComponent(accountId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setDefaultAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(connector)}/accounts/${encodeURIComponent(accountId)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Replace the "Never show agents" lists (senders and/or labels; omit to keep). */
export async function setGmailFilters(filters: { senders?: string[]; labels?: string[] }): Promise<{ ok: boolean; filters?: GmailFilters; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/gmail/filters`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  return res.json();
}

// GitHub relay health, the Slack three-layer shape: shared relay socket /
// cloud sign-in / per-installation token health (+ missed-event counts).
export interface GithubStatus {
  ok: boolean;
  mode: string;
  relay: { state: string; reconnects: number; last_event_at: number | null; last_error: string };
  signed_in: boolean;
  installs: Record<string, { token_ok: boolean }>;
  missed: Record<string, number>;
}

export async function getGithubStatus(): Promise<GithubStatus> {
  const res = await fetch(`${httpBase()}/v1/connectors/github/status`);
  return res.json();
}

/** Stop relaying ONE GitHub App installation to this computer. */
export async function disconnectGithubInstallation(installationId: string): Promise<{ ok: boolean; error?: string; remaining_installs?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/github/installations/${encodeURIComponent(installationId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE HubSpot portal; the default pointer moves to the next portal. */
export async function disconnectHubSpotPortal(hubId: string): Promise<{ ok: boolean; error?: string; remaining_portals?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/hubspot/portals/${encodeURIComponent(hubId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setHubSpotDefaultPortal(hubId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/hubspot/portals/${encodeURIComponent(hubId)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Replace the hidden-fields denylist (properties stripped from agent reads). */
export async function setHubSpotHiddenFields(fields: string[]): Promise<{ ok: boolean; hidden_fields?: string[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/hubspot/hidden-fields`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hidden_fields: fields }),
  });
  return res.json();
}

/** Slack health, three honest layers: relay socket / cloud sign-in / per-team tokens. */
export interface SlackStatus {
  mode: string; // "relay" | "" (manual/off)
  relay: {
    state: "live" | "reconnecting" | "offline";
    reconnects: number;
    last_event_at: number | null;
    last_error: string;
  };
  signed_in: boolean;
  teams: Record<string, { token_ok: boolean }>;
}

export async function getSlackStatus(): Promise<SlackStatus> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/status`);
  return res.json();
}

export type Handlers = {
  onEvent: (event: WsEvent) => void;
  /** `reconnected` is true when this open follows an unexpected drop — the caller
   * should reload what it may have missed (transcript tail, parked prompts). */
  onOpen?: (reconnected: boolean) => void;
  onClose?: () => void;
};

/** Reconnect backoff for a dropped session socket: 1s, 2s, 4s, 8s, then 15s. */
export const SESSION_RECONNECT_MS = [1000, 2000, 4000, 8000, 15000];

export class Session {
  private ws!: WebSocket;
  // Payloads sent before the socket finished opening, replayed on `onopen`. Belt-and-suspenders
  // against the first message being dropped if the user sends in the connect window.
  private outbox: object[] = [];
  private readonly url: string;
  private readonly handlers: Handlers;
  private closed = false;
  private attempts = 0;
  private timer: number | null = null;

  constructor(
    sessionId: string,
    workspace: string,
    agent: string,
    handlers: Handlers,
    machine?: string | null,
  ) {
    const q = `?workspace=${encodeURIComponent(workspace)}&agent=${encodeURIComponent(agent)}`;
    // Remote homes: a session on a joined machine rides the bridged socket —
    // same protocol, different base path (the controller splices it to the box).
    const path = machine
      ? `${machineWsPath(machine)}/ws/session/${sessionId}`
      : `/ws/session/${sessionId}`;
    this.url = `${wsBase()}${path}${q}`;
    this.handlers = handlers;
    this.connect();
  }

  /** Open (or re-open) the socket. A drop the caller did not ask for schedules a retry
   * with capped backoff — a bridged machine session rides two hops (controller + box),
   * and either one restarting used to leave the view dead until the user navigated
   * away (ledger 2026-09-01: the v14 rollover blanked the bridged view). */
  private connect() {
    if (this.closed) return;
    const reconnected = this.attempts > 0;
    const ws = openWebSocket(this.url);
    this.ws = ws;
    ws.onmessage = (e) => {
      try {
        this.handlers.onEvent(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    ws.onopen = () => {
      this.attempts = 0;
      this.flush();
      this.handlers.onOpen?.(reconnected);
    };
    ws.onclose = () => {
      this.handlers.onClose?.();
      if (this.closed) return;
      const delay = SESSION_RECONNECT_MS[Math.min(this.attempts, SESSION_RECONNECT_MS.length - 1)];
      this.attempts += 1;
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }

  private flush() {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    const pending = this.outbox;
    this.outbox = [];
    for (const p of pending) this.ws.send(JSON.stringify(p));
  }

  private send(payload: object) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
    // Still connecting: queue and flush on open rather than silently dropping.
    else if (this.ws.readyState === WebSocket.CONNECTING) this.outbox.push(payload);
  }

  /** `model` = the composer's CURRENT selection, carried on every message so the turn uses
   * exactly what the user sees — immune to set_model races across reconnects (a new cowork
   * session always reconnects once to adopt its scratch dir, which could drop a queued
   * set_model and leave the engine on a stale/resumed model; found 2026-07-04). */
  userMessage(text: string, attachments?: unknown[], model?: string, skill?: string) {
    this.send({
      type: "user_message",
      text,
      ...(model ? { model } : {}),
      ...(attachments?.length ? { attachments } : {}),
      // Force-run (SKILLS-SPEC §4.1): the composer's /skill pick rides as its own field;
      // the server validates it against the session's effective menu and frames the turn.
      ...(skill ? { skill } : {}),
    });
  }

  approve(decision: string) {
    this.send(approvalMessage(decision));
  }

  /** §8.4 "Allow anyway": register a ONE-SHOT exact-action approval for a reviewer-denied
   *  tool call. The caller follows up with a normal user message so the agent retries. */
  allowAnyway(name: string, args: any) {
    this.send({ type: "allow_anyway", name, arguments: args ?? {} });
  }

  // Reply to a `request_directory` prompt: grant a folder (with access level) or decline.
  respondDirectory(granted: boolean, path?: string, writable?: boolean) {
    this.send(directoryResponseMessage(granted, path, writable));
  }

  // Reply to a `request_tool` prompt: install the pinned build, or skip the check.
  respondTool(approved: boolean) {
    this.send(toolResponseMessage(approved));
  }

  // Reply to a `propose_plan` prompt: approve (choosing the execution mode) or reject with feedback.
  respondPlan(approved: boolean, mode?: string, feedback?: string) {
    this.send(planResponseMessage(approved, mode, feedback));
  }

  // Spec §11.6: the human's per-worker decisions ride the response — connectors ticked
  // on the card (within the offered ceiling) and the approval mode; by roster index.
  respondTeam(approved: boolean, feedback?: string, enableChat?: boolean, members?: TeamMemberDecision[]) {
    this.send(teamResponseMessage(approved, feedback, enableChat, members));
  }

  // Reply to a `request_connector` / `grant_connector` prompt.
  respondConnector(approved: boolean) {
    this.send(connectorResponseMessage(approved));
  }

  respondItems(approved: boolean, feedback?: string) {
    this.send(itemsResponseMessage(approved, feedback));
  }

  // Answer a live `ask_user` prompt (attended sessions; unattended ones answer via the Inbox).
  respondQuestion(answer: string) {
    this.send(questionResponseMessage(answer));
  }

  interrupt() {
    this.send({ type: "interrupt" });
  }

  // Re-run a turn that ended in a provider error — no new user message; the server
  // guards on the history tail so a stray frame is a no-op.
  retry() {
    this.send({ type: "retry" });
  }

  setMode(mode: string) {
    this.send({ type: "set_mode", mode });
  }

  setModel(model: string) {
    this.send({ type: "set_model", model });
  }

  close() {
    // Detach before closing: this socket's async `close` event may land AFTER the
    // successor session's `open` (observed when switching into an automation-run
    // session), and a torn-down socket must not clobber the new one's connected state.
    this.closed = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.ws.onopen = null;
    this.ws.onmessage = null;
    this.ws.onclose = null;
    this.ws.close();
  }
}

// -- project bindings (pass 20 / UX-044) ---------------------------------------

export interface ProjectMenu {
  kind: "memory" | "board";
  bound: string | null;
  derived: { kind: "git" | "folder"; label: string; full: string; key: string } | null;
  named: { name: string; key: string }[];
}

export async function getProjectMenu(sessionId: string, kind: "memory" | "board"): Promise<ProjectMenu> {
  const r = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${sessionId}/project-menu?kind=${kind}`);
  return r.json();
}

export async function setProjectBinding(
  sessionId: string,
  kind: "memory" | "board",
  name: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${sessionId}/bindings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name }),
  });
  return r.json();
}

export async function nameCurrentProject(
  sessionId: string,
  kind: "memory" | "board",
  name: string,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${sessionApiBase(sessionId)}/v1/sessions/${sessionId}/project-name`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name }),
  });
  return r.json();
}

// -- remote homes: machines (remote-home-design.md P1b) ------------------------

export interface Machine {
  id: string;
  name: string;
  fingerprint: string;
  app_version: string;
  created_at: number;
  last_seen: number | null;
  connected: boolean;
  // Public halves only (older backends omit them): what a browser seals key
  // deploys to, and the fingerprint the user checks with `openworker machine status`.
  seal_pubkey?: string;
  seal_fingerprint?: string;
  // Union view: where this row lives. Absent = the local registry; "cloud"
  // rows carry the `cloud:` id prefix and ride the sidecar's cloud proxy.
  origin?: "local" | "cloud";
  // Where the machine came from: "" (absent) = the user brought it, "fly" =
  // one of our managed sandboxes (provenance_ref = its sandbox id).
  provenance?: string;
  provenance_ref?: string;
}

// -- managed sandboxes (spec §Fly sandboxes) -----------------------------------

export type SandboxPhase =
  | "provisioning"
  | "joining"
  | "online"
  | "offline"
  | "stuck"
  | "asleep"
  | "failed";

export interface Sandbox {
  id: string;
  name: string;
  owner: string;
  mine: boolean;
  state: string;
  phase: SandboxPhase;
  error: string;
  machine_id: string;
  connected: boolean;
  created_at: number;
  updated_at: number;
}

export interface SandboxesInfo {
  sandboxes: Sandbox[];
  // The caller's effective per-user cap (0 = the feature is off for them)
  // and how many of theirs count against it.
  cap: number;
  used: number;
}

/** Hosted only. A backend without the route (desktop, older service) answers
 * cap 0 — the page then shows nothing about sandboxes. */
export async function getSandboxes(): Promise<SandboxesInfo> {
  try {
    const r = await fetch(`${httpBase()}/v1/sandboxes`);
    if (!r.ok) return { sandboxes: [], cap: 0, used: 0 };
    const d = await r.json();
    return { sandboxes: d.sandboxes ?? [], cap: Number(d.cap ?? 0), used: Number(d.used ?? 0) };
  } catch {
    return { sandboxes: [], cap: 0, used: 0 };
  }
}

export async function createSandbox(
  name?: string,
): Promise<{ sandbox?: Sandbox; error?: string }> {
  const r = await fetch(`${httpBase()}/v1/sandboxes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(name ? { name } : {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = d?.detail;
    return { error: typeof detail === "string" ? detail : detail?.message || `HTTP ${r.status}` };
  }
  return { sandbox: d.sandbox };
}

export async function deleteSandbox(id: string): Promise<{ removed?: string; error?: string }> {
  const r = await fetch(`${httpBase()}/v1/sandboxes/${encodeURIComponent(id)}`, { method: "DELETE" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = d?.detail;
    return { error: typeof detail === "string" ? detail : detail?.message || `HTTP ${r.status}` };
  }
  return d;
}

export interface CloudMachinesInfo {
  // "signed_out" hides the cloud section entirely; "expired" shows a
  // sign-in-again row; "unreachable" = network trouble, shown like offline.
  session: "ok" | "signed_out" | "expired" | "unreachable";
  org?: { id: string; name: string } | null;
  machines: Machine[];
}

/** The hosted cloud registry, through the sidecar's proxy (desktop only —
 * the hosted service itself has no such route). Ids come back prefixed so
 * every existing consumer routes them through the proxy automatically. */
export async function getCloudMachines(): Promise<CloudMachinesInfo> {
  // Only an explicit session state from the proxy counts. A backend without
  // the route (older sidecar, hosted service) answers something else — that
  // is "no cloud attachment here", i.e. signed_out, never an error state.
  try {
    const r = await fetch(`${httpBase()}/v1/cloud/machines`);
    if (!r.ok) return { session: "signed_out", machines: [] };
    const data = await r.json();
    const session = ["ok", "signed_out", "expired", "unreachable"].includes(data.session)
      ? (data.session as CloudMachinesInfo["session"])
      : "signed_out";
    const machines: Machine[] = (data.machines ?? []).map((m: Machine) => ({
      ...m,
      id: CLOUD_ID_PREFIX + m.id,
      origin: "cloud" as const,
    }));
    return { session, org: data.org, machines };
  } catch {
    return { session: "signed_out", machines: [] };
  }
}

/** Fired on `window` by whoever learns the fleet changed (a machine joined,
 * was removed, a sandbox came up) so pickers elsewhere reload their list. */
export const MACHINES_CHANGED = "openworker:machines-changed";

export async function getMachines(): Promise<{ machines: Machine[]; armed: boolean }> {
  const r = await fetch(`${httpBase()}/v1/machines`);
  return r.json();
}

/** Opening the "Add a machine" card arms enrollment: mints ONE single-use
 * token with a 10-minute window and returns the join URL to show the user. */
export async function armEnrollment(): Promise<{ join_url: string; expires_at: number }> {
  const r = await fetch(`${httpBase()}/v1/remote/arm`, { method: "POST" });
  return r.json();
}

export async function disarmEnrollment(): Promise<void> {
  await fetch(`${httpBase()}/v1/remote/arm`, { method: "DELETE" });
}

export async function renameMachine(
  id: string,
  name: string,
): Promise<{ machine?: { id: string; name: string }; error?: string }> {
  const r = await fetch(machineApi(id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return r.json();
}

export async function removeMachine(id: string): Promise<{ removed?: string; error?: string }> {
  const r = await fetch(machineApi(id), { method: "DELETE" });
  return r.json();
}

/** The MACHINE's own settings (models it offers, its default) via the proxy —
 * what the composer's picker shows when a draft runs on that machine. */
export async function getMachineSettings(machineId: string): Promise<ModelSettings> {
  const r = await fetch(`${engineBase(machineId)}/v1/settings`);
  return r.json();
}

// -- deployment mode (remote-home-design.md §Cloud dashboard) ------------------
// The SAME bundle serves the desktop sidecar and the acceptor-only cloud
// service; the backend's capabilities flag — fetched once at boot — decides
// which surfaces exist. Module-level so leaf components can read it without
// prop-drilling; App sets it before the first post-boot render.
export type AppMode = "desktop" | "cloud";
let appMode: AppMode = "desktop";

export function setAppMode(mode: AppMode): void {
  appMode = mode;
}

export function isCloudMode(): boolean {
  return appMode === "cloud";
}

// Capability flags (spec §"Deployments and the UI"): features gate on NAMED
// flags, not on the mode — a new deployment is a different capabilities
// response, zero UI changes. `wallet` is the first flag migrated; the rest
// follow in the Settings rehaul.
let walletAvailable = true;
export function setWalletAvailable(available: boolean): void {
  walletAvailable = available;
}
/** Does this backend hold a key wallet? false → key deploys are sealed in
 * the BROWSER and relayed (OPE-149). */
export function hasWallet(): boolean {
  return walletAvailable;
}

/** Login config a hosted deployment advertises (server-driven, like the mode
 * flag itself — the bundle carries no deployment-specific identifiers). */
export interface CloudAuthConfig {
  kind: string; // "auth0"
  domain: string;
  client_id: string;
  audience: string;
}

// The broker (OpenWorker Cloud API) a hosted deployment pairs with — set from
// /v1/capabilities so the dashboard can start a managed OAuth connect for a
// machine from the browser (spec §Cloud-dashboard connect-direct).
let cloudBase = "";
export function getCloudBase(): string {
  return cloudBase;
}
export function setCloudBase(base: string): void {
  cloudBase = (base || "").replace(/\/$/, "");
}

export async function getCapabilities(): Promise<{
  mode: AppMode;
  wallet: boolean;
  auth?: CloudAuthConfig;
  cloud?: { base: string };
}> {
  try {
    const r = await fetch(`${httpBase()}/v1/capabilities`);
    const d = await r.json();
    if (d.cloud?.base) setCloudBase(String(d.cloud.base));
    return {
      mode: d.mode === "cloud" ? "cloud" : "desktop",
      wallet: d.wallet !== false, // absent (desktop sidecar) → has a wallet
      ...(d.auth ? { auth: d.auth as CloudAuthConfig } : {}),
      ...(d.cloud?.base ? { cloud: { base: String(d.cloud.base) } } : {}),
    };
  } catch {
    return { mode: "desktop", wallet: true }; // older sidecars have no endpoint
  }
}

/** How the hosted backend resolves this browser's token: identity + org
 * memberships (the switcher's data). Desktop has no such endpoint. */
export interface MeInfo {
  actor: string;
  email: string;
  org_id: string;
  admin: boolean;
  orgs: { id: string; name: string; role: string }[];
  // Per-tenant policy flags for the resolved org (deployment-wide flags ride
  // /v1/capabilities). Absent on older backends → everything allowed.
  policies?: { key_push?: boolean; sandbox_cap?: number };
}

export async function getMe(): Promise<MeInfo> {
  const r = await fetch(`${httpBase()}/v1/me`);
  if (!r.ok) throw new Error(`me: ${r.status}`);
  return r.json();
}

// -- device-flow approval (`openworker join` — typed-code, GitHub-style) --

export interface DeviceRequestDetail {
  user_code: string;
  name: string;
  fingerprint: string;
  expires_at: number;
}

/** Look up a pending join request by its typed code. 404 → null (unknown,
 * expired, or already decided). */
export async function getDeviceRequest(code: string): Promise<DeviceRequestDetail | null> {
  const r = await fetch(`${httpBase()}/v1/remote/device/${encodeURIComponent(code)}`);
  if (!r.ok) return null;
  return r.json();
}

export async function approveDeviceRequest(code: string): Promise<boolean> {
  const r = await fetch(
    `${httpBase()}/v1/remote/device/${encodeURIComponent(code)}/approve`,
    { method: "POST" },
  );
  return r.ok;
}

export async function denyDeviceRequest(code: string): Promise<boolean> {
  const r = await fetch(
    `${httpBase()}/v1/remote/device/${encodeURIComponent(code)}/deny`,
    { method: "POST" },
  );
  return r.ok;
}

export interface WalletProfile {
  profile: string;
  type?: string | null;
  account?: string | null;
  expired?: boolean;
}

/** Wallet contents by name/type only — values never reach the browser. */
export async function getWalletProfiles(): Promise<WalletProfile[]> {
  const r = await fetch(`${httpBase()}/v1/wallet`);
  return (await r.json()).profiles ?? [];
}

export interface MachineSecretRow {
  profile: string;
  deployed_at: number;
  stale: boolean;
  missing_from_wallet: boolean;
}

export async function getMachineSecrets(machineId: string): Promise<MachineSecretRow[]> {
  const r = await fetch(`${machineApi(machineId)}/secrets`);
  return (await r.json()).secrets ?? [];
}

/** Wallet deploy: NAMES only — the sidecar resolves values from its own store,
 * seals them to the machine's pinned key, and pushes. */
/** Grant handoff (spec §Remote OAuth): which profile keys a connector's
 * grant carries, and whether it may move to a machine at all. One grant,
 * one holder — the move deploys these sealed, then This Mac forgets. */
export async function getConnectorHandoffInfo(name: string): Promise<{
  ok: boolean;
  portable?: boolean;
  reason?: string;
  profiles?: string[];
  needs_delegation?: boolean;
}> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/handoff-info`,
  );
  return res.json();
}

/** Handoff step for managed grants: mark their broker connections
 * machine-held so the machine can renew by possession. Passing the target
 * machine's seal_pubkey also mints the machine credential (managed events):
 * the broker then routes the connection's events to that machine's sealed
 * queue, and the credential is stamped into the moving profiles. */
export async function delegateConnector(
  name: string,
  sealPubkey?: string,
  // Hosted machines only: the machines service's own id, so the broker can
  // wake a sleeping sandbox when an event arrives for this grant.
  machineId?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/delegate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(sealPubkey ? { seal_pubkey: sealPubkey } : {}),
        ...(machineId ? { machine_id: machineId } : {}),
      }),
    },
  );
  return res.json();
}

/** Connect-direct-to-machine (machines spec §Remote OAuth): OAuth completes
 * in this browser as usual, but the sidecar's callback ships the grant —
 * delegated and sealed — to the named machine and stores nothing locally.
 * Desktop only (the sidecar owns the OAuth loopback); needs cloud sign-in. */
// Which broker provider serves a connector (mirrors the sidecar's map).
const PROVIDER_FOR_CONNECTOR: Record<string, string> = {
  gmail: "google",
  google_calendar: "google",
  google_drive: "google",
  slack: "slack",
  notion: "notion",
  attio: "attio",
  hubspot: "hubspot",
  github: "github",
  outlook: "microsoft",
};

/** Browser connect-direct (hosted dashboard): ask the broker for the
 * consent URL in browser mode — the result comes back to THIS tab by
 * postMessage, delegated to the machine, and the tab seals it to the
 * machine's pinned key. Returns the authorize URL to open in a popup. */
export async function beginBrowserManagedConnect(
  name: string,
  machineId: string,
  sealPubkey: string,
  opts: { access?: string; flow?: string } = {},
): Promise<{ ok: boolean; authorize_url?: string; error?: string }> {
  const provider = PROVIDER_FOR_CONNECTOR[name];
  const base = getCloudBase();
  if (!provider) return { ok: false, error: i18n.t("misc.api.no_managed_oauth", { name }) };
  if (!base) return { ok: false, error: i18n.t("misc.api.no_cloud_api") };
  const token = apiToken();
  if (!token) return { ok: false, error: "not signed in" };
  const r = await globalThis.fetch(`${base}/v1/oauth/${encodeURIComponent(provider)}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      connector: name,
      mode: "browser",
      redirect: window.location.origin,
      machine_id: machineId.replace(/^cloud:/, ""),
      seal_pubkey: sealPubkey,
      ...(opts.access ? { access: opts.access } : {}),
      ...(opts.flow ? { flow: opts.flow } : {}),
    }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      detail = typeof d.detail === "string" ? d.detail : detail;
    } catch {
      /* keep */
    }
    return { ok: false, error: detail };
  }
  const d = await r.json();
  return { ok: true, authorize_url: d.authorize_url };
}

/** Relay a browser-sealed broker grant to the machine (the box stores it
 * through its own routine). Ciphertext only leaves this tab. */
export async function connectManagedGrantSealed(
  machineId: string,
  name: string,
  sealedB64: string,
): Promise<{ ok: boolean; account?: string; error?: string }> {
  const res = await fetch(
    `${machineApi(machineId)}/connectors/${encodeURIComponent(name)}/managed-grant-sealed`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sealed_b64: sealedB64 }),
    },
  );
  const d = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: d.error || d.message || `HTTP ${res.status}` };
  return d;
}

export async function beginManagedConnectOnMachine(
  machineId: string,
  name: string,
  machineName: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/connect-managed`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machine_id: machineId, machine_name: machineName }),
    },
  );
  return res.json();
}

/** Complete a machine-scope disconnect of a MANAGED grant: the box deleted
 * its copy but cannot reach the broker (no session), so the desktop revokes
 * the connector's broker connections — killing the delegation so events stop
 * queueing for a machine that no longer listens. Best-effort, idempotent. */
export async function revokeCloudConnections(
  name: string,
): Promise<{ ok: boolean; revoked?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/cloud/connections/${encodeURIComponent(name)}/revoke`,
    { method: "POST" },
  );
  return res.json();
}

/** The handoff's forget step: local deletion only — never the broker
 * disconnect, which would revoke the delegation the machine now lives on. */
export async function forgetConnectorLocal(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/forget-local`,
    { method: "POST" },
  );
  return res.json();
}

export async function deployMachineSecrets(
  machineId: string,
  profiles: string[],
): Promise<{ ok?: boolean; deployed?: string[]; error?: string }> {
  const r = await fetch(`${machineApi(machineId)}/secrets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profiles }),
  });
  return r.json();
}

/** Browser-sealed deploy (OPE-149): the payload was sealed IN THIS TAB to
 * the machine's pinned key; the backend is a blind relay. `hashes` feed the
 * staleness ledger only. */
export async function deploySealedSecrets(
  machineId: string,
  sealedB64: string,
  profiles: string[],
  hashes: Record<string, string>,
): Promise<{ ok?: boolean; deployed?: string[]; error?: string }> {
  const r = await fetch(`${machineApi(machineId)}/secrets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sealed_b64: sealedB64, profiles, hashes }),
  });
  return r.json();
}

export async function revokeMachineSecrets(
  machineId: string,
  profiles: string[],
): Promise<{ ok?: boolean; revoked?: string[]; error?: string }> {
  const r = await fetch(`${machineApi(machineId)}/secrets`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profiles }),
  });
  return r.json();
}


// --- Cloud views for connectors across machines (UX-049) ----------------------
// The dashboard talks to the broker directly with the user's token; the
// desktop goes through the sidecar's explicit /v1/cloud/... routes so the
// token never leaves it. Same shapes either way.

async function brokerFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (isCloudMode() && getCloudBase()) {
    const token = apiToken();
    return globalThis.fetch(`${getCloudBase()}${path}`, {
      ...init,
      headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
    });
  }
  return fetch(`${httpBase()}/v1/cloud${path.replace(/^\/v1/, "")}`, init);
}

const JSON_POST = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface CloudHolder {
  machine_id: string; // "" = the anonymous pre-machine-id delegation
  delegated_at: string;
  events: boolean;
}

export interface CloudConnection {
  connection_id: string;
  connector: string;
  provider: string;
  status: string;
  provider_account?: string | null;
  tenant_metadata?: Record<string, unknown> | null;
  holders: CloudHolder[];
  default_machine_id: string; // "" | "desktop" | machine id
  copyable: boolean; // false = the provider rotates refresh tokens: one holder only
  // Per workspace / installation (UX-049): which machine answers mentions nobody
  // subscribed to, and which coworker (a persona id on that machine) starts the session.
  routing: Record<string, { machine_id: string; persona: string }>;
}

/** Set one half or both of a scope's routing line. machineId "" keeps;
 * persona undefined keeps, "" clears. */
export async function setScopeRouting(
  connectionId: string,
  scope: string,
  machineId = "",
  persona?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await brokerFetch(
    `/v1/connections/${encodeURIComponent(connectionId)}/routing`,
    JSON_POST({ scope, machine_id: machineId, ...(persona === undefined ? {} : { persona }) }),
  );
  return res.ok ? { ok: true } : { ok: false, error: `http ${res.status}` };
}

export async function getCloudConnections(): Promise<CloudConnection[]> {
  const res = await brokerFetch("/v1/connections");
  if (!res.ok) return [];
  return (await res.json()).connections ?? [];
}

/** Add a holder for `connectionId` on a machine (Enable): the broker mints
 * that machine's credential, returned once. */
export async function delegateConnection(
  connectionId: string,
  sealPubkey: string,
  machineId: string,
): Promise<{ ok: boolean; user_id?: string; machine_credential?: string; error?: string }> {
  const res = await brokerFetch(
    `/v1/connections/${encodeURIComponent(connectionId)}/delegate`,
    JSON_POST({ seal_pubkey: sealPubkey, machine_id: machineId }),
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: body.detail || body.error || `http ${res.status}` };
  return { ok: true, user_id: body.user_id, machine_credential: body.machine_credential };
}

export async function setDefaultMachine(
  connectionId: string,
  machineId: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await brokerFetch(
    `/v1/connections/${encodeURIComponent(connectionId)}/default-machine`,
    JSON_POST({ machine_id: machineId }),
  );
  return res.ok ? { ok: true } : { ok: false, error: `http ${res.status}` };
}

/** Disconnect on ONE machine at the broker (the machine forgets its own copy). */
export async function forgetHolder(connectionId: string, machineId: string): Promise<boolean> {
  const res = await brokerFetch(
    `/v1/connections/${encodeURIComponent(connectionId)}/holders/${encodeURIComponent(machineId)}`,
    { method: "DELETE" },
  );
  return res.ok;
}

export interface CloudSubscription {
  source: string;
  connector: string;
  machine_id: string; // "desktop" | machine id
  session_id: string;
  connection_id: string;
  team_id: string;
  title: string;
  state: "active" | "orphan";
  created_at: string;
}

export async function getCloudSubscriptions(connector = ""): Promise<CloudSubscription[]> {
  const res = await brokerFetch(`/v1/subscriptions${connector ? `?connector=${encodeURIComponent(connector)}` : ""}`);
  if (!res.ok) return [];
  return (await res.json()).subscriptions ?? [];
}

export async function removeCloudSubscription(source: string): Promise<boolean> {
  const res = await brokerFetch("/v1/subscriptions/remove", JSON_POST({ source }));
  return res.ok;
}

export interface Person {
  source: string;
  connector: string;
  scope: string; // Slack team id | GitHub installation id
  member: string; // Slack user id | GitHub login
  name: string;
  added_at: string;
  pinned?: boolean; // the user themself: always listed, never removable
}

export async function getPeople(connector: string, scope = ""): Promise<Person[]> {
  const q = new URLSearchParams({ connector, ...(scope ? { scope } : {}) }).toString();
  const res = await brokerFetch(`/v1/people?${q}`);
  if (!res.ok) return [];
  return (await res.json()).people ?? [];
}

export async function addPerson(
  connector: string,
  scope: string,
  member: string,
  name = "",
): Promise<{ ok: boolean; error?: string }> {
  const res = await brokerFetch("/v1/people", JSON_POST({ connector, scope, member, name }));
  return res.ok ? { ok: true } : { ok: false, error: `http ${res.status}` };
}

export async function removePerson(connector: string, scope: string, member: string): Promise<boolean> {
  const res = await brokerFetch("/v1/people/remove", JSON_POST({ connector, scope, member }));
  return res.ok;
}

// Configurations (connectors-across-machines spec §10): repositories × event ×
// who × send to. One object per configuration (its rows grouped by the broker).
export type ConfigurationEvent = "mention" | "named_mention" | "pr_open" | "pr_merge" | "issue_open";

export interface ConfigurationTarget {
  kind: "existing" | "new";
  machine_id: string; // "desktop" | broker machine id
  session_id?: string;
  title?: string;
  persona?: string;
  models?: string[];
  base_dir?: string;
  worktree?: boolean;
  skills?: string[];
  instructions?: string;
  board?: string;
  memory?: string;
  // Spec §11.5: the spawned session's approval mode (default "auto-approve") and whether
  // its prompts go to the Inbox (default true) — decided when the configuration is written.
  approval_mode?: "interactive" | "auto-approve" | "bypass-approvals" | string;
  unattended?: boolean;
}

export interface Configuration {
  config_id: string;
  connector: string;
  installation_id: string;
  repos: string[]; // "owner/repo", or "owner" = all repositories
  event: ConfigurationEvent | string;
  name: string;
  who: string[];
  target: ConfigurationTarget;
  state: "active" | "orphan" | string;
  created_at: string;
  updated_at?: string;
}

export async function getConfigurations(connector = "github"): Promise<Configuration[]> {
  const res = await brokerFetch(`/v1/configurations?connector=${encodeURIComponent(connector)}`);
  if (!res.ok) return [];
  return (await res.json()).configurations ?? [];
}

export interface ConfigurationError {
  ok: false;
  error: string; // "held" | "name_taken" | "subscribed" | http message
  held_by?: Record<string, unknown>;
}

export async function addConfiguration(body: {
  connector?: string;
  installation_id?: string;
  repos: string[];
  event: string;
  name?: string;
  who?: string[];
  target: ConfigurationTarget;
}): Promise<{ ok: true; configuration: Configuration } | ConfigurationError> {
  const res = await brokerFetch("/v1/configurations", JSON_POST({ connector: "github", ...body }));
  const data = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, configuration: data.configuration };
  const detail = data.detail ?? data;
  return { ok: false, error: typeof detail === "string" ? detail : detail?.error || `http ${res.status}`, held_by: detail?.held_by };
}

export async function editConfiguration(
  configId: string,
  body: { who?: string[]; target?: ConfigurationTarget },
): Promise<{ ok: true; configuration: Configuration } | ConfigurationError> {
  const res = await brokerFetch(`/v1/configurations/${encodeURIComponent(configId)}/edit`, JSON_POST(body));
  const data = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, configuration: data.configuration };
  const detail = data.detail ?? data;
  return { ok: false, error: typeof detail === "string" ? detail : detail?.error || `http ${res.status}` };
}

export async function deleteConfiguration(configId: string): Promise<boolean> {
  const res = await brokerFetch(`/v1/configurations/${encodeURIComponent(configId)}`, { method: "DELETE" });
  return res.ok;
}

/** Box-to-box Enable: the SOURCE machine seals its profiles for `name`,
 * stamped with the target's grant, to the target's pinned key. Ciphertext
 * only; the caller relays it to the target's deploy route. */
export async function handoffSealedFromMachine(
  sourceMachineId: string,
  name: string,
  sealPubkey: string,
  grant: { user_id?: string; machine_credential?: string },
): Promise<{ ok: boolean; sealed_b64?: string; profiles?: string[]; error?: string; reason?: string }> {
  const res = await fetch(
    `${machineApi(sourceMachineId)}/p/v1/connectors/${encodeURIComponent(name)}/handoff-sealed`,
    JSON_POST({ seal_pubkey: sealPubkey, grant }),
  );
  return res.json();
}

/** Move's forget step on a source MACHINE (the desktop has forgetConnectorLocal). */
export async function forgetConnectorOnMachine(sourceMachineId: string, name: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${machineApi(sourceMachineId)}/p/v1/connectors/${encodeURIComponent(name)}/forget-local`,
    { method: "POST" },
  );
  return res.json();
}
