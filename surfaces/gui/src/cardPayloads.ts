// One place where a server payload becomes a card's props, and where a card's answer
// becomes the message on the wire. The session view (App), the Inbox card and the dev
// card gallery all go through here, so a payload renders the same wherever it lands
// (the staffing card diverged between the session and the Inbox on 2026-09-16).
import type { InboxItem, TeamMemberDecision } from "./api";
import type { Item } from "./types";

export type ApprovalItem = Extract<Item, { kind: "approval" }>;
export type PlanItem = Extract<Item, { kind: "planreq" }>;
export type TeamRequestItem = Extract<Item, { kind: "teamreq" }>;
export type ConnectorRequestItem = Extract<Item, { kind: "connreq" }>;
export type WorkItemsItem = Extract<Item, { kind: "itemsreq" }>;
export type DirectoryRequestItem = Extract<Item, { kind: "dirreq" }>;
export type ToolRequestItem = Extract<Item, { kind: "toolreq" }>;
export type QuestionItem = Extract<Item, { kind: "question" }>;

// -- server payload → card item -------------------------------------------------
// `d` is the websocket event's `data` for an inline card, or the Inbox item's `data`
// for a parked one: the server sends the same fields in both places.

/** `permission_required`. */
export function approvalItemFromPayload(d: any): ApprovalItem {
  return {
    kind: "approval",
    toolCallId: typeof d.tool_call_id === "string" ? d.tool_call_id : undefined,
    name: d.name,
    args: d.arguments,
    reason: d.reason,
    category: d.category,
    standingTarget: d.standing_target || undefined,
    searchProvider: d.search_provider || undefined,
    provenance: d.provenance || undefined,
    reviewerUnsure: d.reviewer_unsure || undefined,
    escalation: d.escalation || undefined,
    readonlyOk: !!d.readonly_ok,
    mcpDestination: d.mcp_destination || undefined,
    workerCall: d.worker_call && typeof d.worker_call === "object" && d.worker_call.tool ? d.worker_call : undefined,
  };
}

/** `plan_proposed`. */
export function planItemFromPayload(d: any): PlanItem {
  return { kind: "planreq", plan: d.plan || "" };
}

/** `team_proposed`, or a parked `gate: "team"` item. Fields an older server does not
 *  send stay undefined, so the card can tell "unknown" from "empty". */
export function teamItemFromPayload(d: any): TeamRequestItem {
  return {
    kind: "teamreq",
    title: d.title, summary: d.summary, groups: d.groups, planned_items: d.planned_items,
    toolCallId: typeof d.tool_call_id === "string" ? d.tool_call_id : undefined,
    members: Array.isArray(d.members) ? d.members : [],
    enable_chat: !!d.enable_chat,
    note: d.note || "",
    offer: d.offer && typeof d.offer === "object" ? d.offer : {},
    other_connected: Array.isArray(d.other_connected) ? d.other_connected : [],
    lead_mode: typeof d.lead_mode === "string" ? d.lead_mode : undefined,
    model_options: d.model_options && typeof d.model_options === "object" ? d.model_options : undefined,
    runnable_models: Array.isArray(d.runnable_models) ? d.runnable_models : undefined,
    lead_model: typeof d.lead_model === "string" ? d.lead_model : undefined,
  };
}

/** `connector_requested`. */
export function connectorItemFromPayload(d: any): ConnectorRequestItem {
  return {
    kind: "connreq",
    request: d.request === "grant" ? "grant" : "connect",
    connector: d.connector || "",
    worker: d.worker || "",
    reason: d.reason || "",
  };
}

/** `items_proposed`, or a parked `gate: "items"` item. */
export function workItemsItemFromPayload(d: any): WorkItemsItem {
  return { kind: "itemsreq", toolCallId: typeof d.tool_call_id === "string" ? d.tool_call_id : undefined,
    title: d.title, summary: d.summary, targets: d.targets, external_actions: d.external_actions,
    activities: d.activities, workstreams: d.workstreams, final_acceptance: d.final_acceptance,
    items: Array.isArray(d.items) ? d.items : [], note: d.note || "" };
}

/** `directory_requested`. */
export function directoryItemFromPayload(d: any): DirectoryRequestItem {
  return { kind: "dirreq", reason: d.reason || "", path: d.path || "", writable: !!d.writable, primary: !!d.primary };
}

/** `tool_requested`. */
export function toolItemFromPayload(d: any): ToolRequestItem {
  return {
    kind: "toolreq",
    tool: d.name || "",
    reason: d.reason || "",
    // Fail CLOSED: only offer Install when the event says a pinned build exists.
    installable: d.installable === true,
    version: d.version || "",
    summary: d.summary || "",
    source: d.source || "",
  };
}

/** `question_requested` (ask_user in an attended session). */
export function questionItemFromPayload(d: any): QuestionItem {
  return {
    kind: "question",
    question: d.question || "",
    options: d.options || [],
    allow_text: d.allow_text !== false,
    multi: !!d.multi,
    header: d.header || "",
    questions: d.questions || [],
  };
}

/** A live question wears the Inbox card (one question UI); this is the item it is given. */
export function liveQuestionInboxItem(q: QuestionItem, sessionId: string): InboxItem {
  return {
    id: "live-question",
    session_id: sessionId,
    kind: "question",
    title: q.question,
    body: "",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    options: q.options,
    allow_text: q.allow_text,
    multi: q.multi,
    header: q.header,
    questions: q.questions,
  };
}

// -- card answer → websocket message ----------------------------------------------

export function approvalMessage(decision: string) {
  return { type: "approval", decision };
}

export function planResponseMessage(approved: boolean, mode?: string, feedback?: string) {
  return { type: "plan_response", approved, ...(mode ? { mode } : {}), ...(feedback ? { feedback } : {}) };
}

// Spec §11.6: the human's per-worker decisions ride the response — the connectors ticked
// on the card and the chosen model; by roster index.
export function teamResponseMessage(
  approved: boolean,
  feedback?: string,
  enableChat?: boolean,
  members?: TeamMemberDecision[],
) {
  return {
    type: "team_response",
    approved,
    ...(feedback ? { feedback } : {}),
    ...(enableChat !== undefined ? { enable_chat: enableChat } : {}),
    ...(members ? { members } : {}),
  };
}

export function connectorResponseMessage(approved: boolean) {
  return { type: "connector_response", approved };
}

export function itemsResponseMessage(approved: boolean, feedback?: string) {
  return { type: "items_response", approved, ...(feedback ? { feedback } : {}) };
}

export function directoryResponseMessage(granted: boolean, path?: string, writable?: boolean) {
  return { type: "directory_response", granted, ...(path ? { path } : {}), writable: !!writable };
}

export function toolResponseMessage(approved: boolean) {
  return { type: "tool_response", approved };
}

export function questionResponseMessage(answer: string) {
  return { type: "question_response", answer };
}

// -- card answer → Inbox resolution (parked gates) ----------------------------------

export function teamGateResolution(
  approved: boolean,
  feedback?: string,
  enableChat?: boolean,
  members?: TeamMemberDecision[],
): string {
  return JSON.stringify({ approved, feedback: feedback ?? "", enable_chat: !!enableChat, members: members ?? [] });
}

export function itemsGateResolution(approved: boolean, feedback?: string): string {
  return JSON.stringify({ approved, feedback: feedback ?? "" });
}
