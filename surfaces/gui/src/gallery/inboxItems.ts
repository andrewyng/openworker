// The Inbox item the server parks for each card, built from the same payload the inline
// card gets. Mirrors coworker/server/manager.py (inbox_team_approver, inbox_items_approver,
// inbox_connector_requester, approval_prompt_data, the plan gate) — the server-side
// contract check (spec §4) is what keeps this file honest.
import type { InboxItem } from "../api";
import type { CardState } from "./types";

function parked(kind: InboxItem["kind"], title: string, body: string, data?: Record<string, any>): InboxItem {
  return {
    id: "gallery-item",
    session_id: "gallery-session",
    kind,
    title,
    body,
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "2026-09-17T09:30:00Z",
    resolved_at: null,
    visibility: "inbox",
    session_title: "Mention #3 · Invoice PDF download",
    session_agent: "swe-lead",
    session_exists: true,
    ...(data ? { data } : {}),
  };
}

// Cards with no entry here never park in the Inbox, so they get no "In the Inbox" toggle.
export const inboxItemBuilders: Partial<Record<string, (s: CardState<any>) => InboxItem>> = {
  "team-request": ({ payload: p }) =>
    parked(
      "plan",
      "Create this team?",
      (p.members || []).map((m: any) => `- ${m.persona}${m.name ? ` (${m.name})` : ""}`).join("\n"),
      { gate: "team", ...p },
    ),
  "work-items": ({ payload: p }) =>
    parked(
      "plan",
      "Approve the proposed work items?",
      (p.items || []).map((i: any) => `- ${i.title}`).join("\n"),
      { gate: "items", ...p },
    ),
  approval: ({ payload: p, context }) =>
    parked("approval", `Run \`${p.name}\`?`, p.reason || "", {
      tool: p.name,
      arguments: p.arguments || {},
      escalation: p.escalation || null,
      provenance: p.provenance || "",
      ...(context?.runTask ? { task_id: context.runTask.id, task_title: context.runTask.title } : {}),
      ...(context?.runTask && p.standing_target ? { standing_target: p.standing_target } : {}),
    }),
  // A parked lead decision: the approval item plus the worker's call the server attaches.
  "worker-decision": ({ payload: p }) =>
    parked(
      "approval",
      `Run \`${p.name}\`?`,
      `requires approval\nworker: ${p.arguments.worker} · call_id: ${p.arguments.call_id} · decision: ${p.arguments.decision}`,
      { tool: p.name, arguments: p.arguments, escalation: p.escalation || null, provenance: p.provenance || "", ...(p.worker_call ? { worker_call: p.worker_call, worker_prompt_id: p.arguments?.call_id } : {}) },
    ),
  "connector-request": ({ payload: p }) =>
    parked(
      "connector",
      p.request === "grant" ? `Give ${p.worker} access to ${p.connector}?` : `Connect ${p.connector}?`,
      p.reason || "",
      { request: p.request, connector: p.connector, worker: p.worker || "" },
    ),
  plan: ({ payload: p }) => parked("plan", "Approve the plan?", p.plan || ""),
  // coworker/tools/ask.py question_item_fields: a grouped ask surfaces its FIRST question
  // as the title and options too, so older surfaces degrade to one question.
  question: ({ payload: p }) => {
    const first = (p.questions || [])[0];
    return {
      ...parked("question", first ? first.question : p.question || "", ""),
      options: first ? first.options || [] : p.options || [],
      allow_text: (first ? first.allow_text : p.allow_text) !== false,
      multi: !!(first ? first.multi : p.multi),
      header: (first ? first.header : p.header) || "",
      questions: p.questions || [],
    };
  },
  "directory-request": ({ payload: p }) =>
    parked("directory", "Grant access to a folder?", p.reason || "", {
      path: p.path || "",
      writable: !!p.writable,
      primary: !!p.primary,
    }),
  "tool-request": ({ payload: p }) =>
    parked("tool", p.name ? `Install ${p.name}?` : "Install a tool?", p.reason || "", {
      tool: p.name || "",
      installable: p.installable === true,
      version: p.version || "",
      summary: p.summary || "",
      url: "",
      source: p.source || "",
    }),
};
