export type EventType =
  | "ready"
  | "inbound"
  | "turn_start"
  | "assistant_delta"
  | "reasoning_delta"
  | "assistant_message"
  | "tool_proposed"
  | "permission_required"
  | "directory_requested"
  | "tool_requested"
  | "connector_requested"
  | "question_requested"
  | "plan_proposed"
  | "team_proposed"
  | "items_proposed"
  | "tool_started"
  | "tool_finished"
  | "iteration_end"
  | "turn_end"
  | "error"
  | "input_rejected"
  | "interrupted"
  | "model_changed"
  | "mode_notice"
  | "memory_saved"
  | "compacting"
  | "compacted"
  | "continuation"
  | "turn_done";

export interface WsEvent {
  type: EventType;
  data: any;
}

// Re-exported for transcript items below. Lives in api.ts (the REST/WS contract source of truth);
// type-only import, so there's no runtime cycle with api.ts's `import type { ... } from "./types"`.
import type { MessageSource } from "./api";

// "always_task" persists to the owning automation's task record (standing scoped
// approval, UX-DECISIONS §25) — offered only on automation-run approval cards, in-app.
export type ApprovalDecision =
  | "once"
  | "deny"
  | "always_tool"
  | "always_command"
  | "always_domain"
  | "always_task"
  // OPE-136 §4: durable per-MCP-tool trust — writes a rule to the user-local
  // override store; survives sessions; revocable on the server's detail page.
  | "always_trust"
  // OPE-136 run grant: covers the exact tool for the remainder of the current
  // answer only; in-memory, cleared at the run boundary. EXTERNAL family only.
  | "this_run"
  | "readonly_session";

export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "done";
}

// Per-round-trip token counts, as attached by the server to assistant messages and
// the assistant_message event (`{model, input, output, cache_read, cache_write}`).
// Absent on older servers and on backends that don't report usage.
export interface TurnUsage {
  model?: string | null;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

// Per-session accumulation, keyed by model id (multiple models when the user
// switched mid-session). `context` = the latest round-trip's prompt-side total —
// what currently occupies the active model's context window.
export interface SessionUsage {
  byModel: Record<string, TurnUsage>;
  context: number;
}

export interface SessionInfo {
  session_id: string;
  title?: string;
  workspace: string;
  agent: string;
  model: string;
  mode: string;
  updated_at: string | null;
  messages: number;
  pinned?: boolean;
  archived?: boolean;
  // Inbox items awaiting this session (the amber attention count that bubbles up the sidebar).
  attention?: number;
  // working = in-flight turn; sleeping = a self-wake is pending; idle = neither. A count-less dot.
  liveness?: "working" | "sleeping" | "idle";
  // When sleeping: the next timer fire (ISO) — drives the "sleeping until…" strip.
  sleeping_until?: string | null;
  // Channels this session listens to (inbound subscriptions).
  subscriptions?: string[];
  // §31: set when the session was spawned by a platform mention rather than the user —
  // machine key ("slack") + display label ("#general · T0ABCD"). Drives the sidebar's
  // "From Slack" group and the row's platform icon.
  origin?: string;
  origin_label?: string;
  // Remote homes (UX-045): set client-side when the row came from a joined machine's
  // store (fetched through the proxy prefix). Absent = a This-Mac session. Drives the
  // ⌂ badge, the Machine grouping, and machine-aware routing of session API calls.
  machine?: string;
  machine_name?: string;
  // Set when the row came from the controller's stored snapshot because the
  // machine is offline — the sidebar greys it, and opening it explains why.
  machine_offline?: boolean;
  // Per-model token totals persisted by the box (spec §5) — {model: {input, output,
  // cache_read, cache_write, turns}}. The lead's Team panel rolls its workers up.
  usage?: Record<string, TurnUsage & { turns?: number }>;
  // Agent teams: {} / absent for plain sessions. Workers carry role/lead_session
  // (+ a computed current-item line); leads carry role/team_id. Drives the sidebar's
  // ONE expandable team entry (workers nest under their lead; plain rows never expand).
  team?: {
    role?: "lead" | "worker" | string;
    team_id?: string;
    lead_session?: string;
    actor?: string;
    current_item?: string;
    status?: string;
    chat_enabled?: boolean;
    chat_unread?: number;
  };
}

// Attachments (images, PDFs, text files) sent with a user message.
export interface Attachment {
  kind: "image" | "text" | "pdf";
  name: string;
  mime?: string;
  data_url?: string; // images + PDFs
  text?: string; // text files
}

// Transcript items
// `ts` = unix seconds (the server's canonical-message stamp; live items stamp locally).
// Optional: sessions saved before the server stamped timestamps have none.
export type Item =
  | { kind: "user"; text: string; attachments?: Attachment[]; ts?: number }
  // A connector-delivered inbound message (Slack/Salesforce/…), rendered as a structured card
  // (ConnectorMessageCard) instead of a plain user bubble. Generalizes to any connector via the
  // registry — no per-connector special-casing.
  | { kind: "connector"; source: MessageSource }
  | { kind: "teamcreated"; teamId: string; workers: { actor: string; persona: string }[]; ts?: number }
  | { kind: "assistant"; text: string; ts?: number; reasoning?: string }
  // `hidden` = results the user's privacy filters removed before the agent saw them
  // (from the tool message's `_display` sidecar; the agent-visible content has no trace).
  // `standingRule` = the task-scoped rule that auto-allowed this call ("tool → target").
  // `reviewerReason` + `allowAnyway` = an Auto-Approve reviewer deny (spec 8.4): the full
  // reason is user-facing only (the agent got a terse refusal), and allowAnyway offers the
  // one-shot exact-action override.
  // `approvalOrigin` = why the call ran without a card: "reviewer" (auto-approved by the
  // Auto-Approve reviewer; `approvalNote` carries its one-line reason) or "bypass"
  // (bypass-approvals mode). Rendered as a quiet debugging chip, deliberately subtle.
  | { kind: "tool"; id: string; name: string; args: any; status: string; preview?: string; hidden?: number; standingRule?: string; reviewerReason?: string; allowAnyway?: boolean; approvalOrigin?: string; approvalNote?: string; approvalGrant?: string }
  | {
      kind: "approval";
      toolCallId?: string;
      name: string;
      args: any;
      reason: string;
      category?: string;
      // The exact target a standing rule could pin (server-computed) — with a run
      // context, the card offers "Allow every time" (§25).
      standingTarget?: string;
      // web_search only (§1.9): the LIVE configured provider name, resolved server-side
      // when the card was raised — the grant description names the actual destination.
      searchProvider?: string;
      // OPE-114 §1: set when the action would run a file the agent itself created or
      // downloaded this session ("setup.py was created by the agent 3 steps ago"). The
      // one fact that cannot be read off the command text. Engine-authored, fixed
      // vocabulary — never file contents.
      provenance?: string;
      // The Auto-Approve reviewer answered `unsure` and raised this card: its one-line
      // reason, rendered quietly so "why am I being asked?" is answered in place.
      reviewerUnsure?: string;
      escalation?: import("./components/ApprovalEscalation").Escalation;
      // Server-classified: this shell command only reads locally, so the card may offer
      // the session-wide "Allow read-only commands" grant.
      readonlyOk?: boolean;
      // decide_worker_call only: the worker's waiting call this decision answers, looked
      // up by the server so the card can show it (absent from an older server).
      workerCall?: { worker?: string; tool: string; arguments?: any; reason?: string; state?: string; resolution?: string | null };
      // OPE-136 finding 4: where an MCP call actually goes, from the server DEF (the
      // user's own config, never the server's claims). Drives the honest scope chip —
      // "leaves this computer → host" (http) / "runs a local program" (stdio).
      mcpDestination?: { transport: string; host?: string };
      resolved?: ApprovalDecision;
    }
  | {
      kind: "dirreq";
      reason: string;
      path?: string;
      writable?: boolean;
      primary?: boolean; // root promotion: the folder becomes the session's workspace
      resolved?: "granted" | "denied";
    }
  | {
      kind: "toolreq";
      tool: string;
      reason: string;
      installable?: boolean;
      version?: string;
      summary?: string;
      source?: string;
      resolved?: "installed" | "skipped";
    }
  | {
      kind: "planreq";
      plan: string;
      resolved?: "approved" | "rejected";
    }
  | {
      // The staffing gate (agent teams): a lead proposes its worker roster.
      kind: "teamreq";
      title?: string;
      summary?: string;
      groups?: import("./proposals").ProposalGroup[];
      planned_items?: { id: number; title: string; final_acceptance?: { id: number; title: string; owner: "lead" | "assigned_worker" } }[];
      toolCallId?: string;
      // connectors = the LEAD'S SUGGESTION for this worker (arrives ticked on the card);
      // connector_reasons = why, per suggested connector.
      members: {
        persona: string;
        group?: string;
        item_ids?: number[];
        name?: string;
        model?: string;
        reason?: string;
        connectors?: string[];
        connector_reasons?: Record<string, string>;
        approval_guidance?: string;
        // The model this worker WILL run on if the human changes nothing on the card.
        resolved_model?: string;
        // Set when none of the persona's recommended models can run on this machine.
        model_warning?: string;
      }[];
      enable_chat?: boolean;
      note?: string;
      // Per worker persona, its RECOMMENDED models that can run on this machine, in order.
      model_options?: Record<string, string[]>;
      // Every model that can run on this machine. Present → the card offers a model
      // picker per worker; absent (older server) → the model stays plain text.
      runnable_models?: { id: string; label: string }[];
      lead_model?: string;
      // Per worker persona, its DEFAULT set that is connected on the machine.
      offer?: Record<string, string[]>;
      // Every other connector connected on the machine — a human may extend a worker
      // beyond its default set from this list (worker-connector-grants spec §2, §6).
      other_connected?: string[];
      // The lead's real approval mode, for a card rendered away from the composer (Inbox).
      lead_mode?: string;
      resolved?: "approved" | "rejected";
    }
  | {
      // Spec §11.6: connector access is a human decision. "connect" = the coworker asks
      // for a service to be connected; "grant" = a lead asks to give a worker a connector.
      kind: "connreq";
      request: "connect" | "grant";
      connector: string;
      worker?: string;
      reason: string;
      resolved?: "approved" | "declined";
    }
  | {
      // The decomposition gate: a lead proposes work items; approval creates them.
      kind: "itemsreq";
      title?: string;
      summary?: string;
      targets?: string[];
      external_actions?: import("./proposals").ExternalActions;
      activities?: import("./proposals").ProposalGroup[];
      workstreams?: import("./proposals").ProposalGroup[];
      final_acceptance?: { item_key: string; owner: "lead" | "assigned_worker" };
      toolCallId?: string;
      items: import("./proposals").ProposalTask[];
      note?: string;
      resolved?: "approved" | "rejected";
    }
  | {
      // A live ask_user prompt (attended sessions answer inline; unattended ones route to the Inbox).
      kind: "question";
      question: string;
      options?: QuestionOption[];
      allow_text?: boolean;
      multi?: boolean;
      header?: string;
      questions?: GroupedQuestion[];
      resolved?: string;
    }
  | {
      kind: "notice";
      tone: "info" | "warn";
      text: string;
      retriable?: boolean;
      // `title` switches the one-line status notice to a block: a heading plus
      // blank-line-separated paragraphs, left-aligned. Used for the Auto-Approve
      // banner, which is prose rather than a status line.
      title?: string;
      // mcp_error notices: the failing server's name + the full error, rendered as one
      // quiet line with the detail behind a disclosure and an Open-Connectors action.
      server?: string;
      detail?: string;
    }
  // MEMORY-SPEC §5.1: the save notice, inline in the conversation where the user is
  // already looking (a corner toast vanished before it could be read or undone —
  // owner-hit 2026-07-28). Stays put. `previous` is set when an existing memory was
  // EDITED rather than a new one added (the update-don't-duplicate rule sends many
  // saves that way) — Undo restores that text instead of deleting the memory.
  | { kind: "memory"; id: number; text: string; previous?: string; undone?: boolean };

// -- ask_user question metadata (OPE-51) --------------------------------------
// An option is a plain string (renders as today's pill) or a rich object: `label` is the answer
// value, `description` renders under it, `recommended` adds the green tag, `preview` is monospace
// text shown in the side pane (≥1 preview switches the card to the two-pane layout).
export type QuestionOption =
  | string
  | { label: string; description?: string; recommended?: boolean; preview?: string };

// One step of a grouped ask_user call (up to 4, rendered as a stepper). The answer map is keyed
// by `header` (falling back to `question`).
export interface GroupedQuestion {
  question: string;
  header?: string;
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
}
