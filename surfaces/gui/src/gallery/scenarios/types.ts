// A scenario is one saved moment of a session: enough data for the REAL app to open on it
// with no server, socket or model (card-gallery spec §5). Hand-written, or exported from a
// real run with scripts/export-scenario.py.
import type { Board, ConversationMessage, InboxItem } from "../../api";

export interface ScenarioWorker {
  name: string;
  persona: string;
  model?: string;
  /** Board status shown on the Team panel: idle | in_progress | blocked | review… */
  status?: string;
  current_item?: string;
  /** Persisted per-model token totals, as /v1/sessions reports them. */
  usage?: Record<string, { input: number; output: number; cache_read: number; cache_write: number; turns: number }>;
}

export interface Scenario {
  id: string;
  title: string;
  /** One line on what this moment shows. */
  note?: string;
  session: {
    session_id: string;
    title: string;
    agent: string;
    model: string;
    mode: string;
    workspace?: string;
    /** Approvals go to the Inbox; the waiting card then comes from `pending`. */
    unattended?: boolean;
    machine?: string;
  };
  /** The stored transcript, exactly as GET /v1/sessions/{id}/messages returns it. */
  messages: ConversationMessage[];
  /** The session's wait queue: pending Inbox items (approval, gate, question…). */
  pending?: InboxItem[];
  /** Websocket events replayed after `ready` — how an ATTENDED session shows a live card. */
  events?: { type: string; data: Record<string, unknown> }[];
  /** Right-rail data. */
  team?: { team_id: string; chat_enabled?: boolean; workers: ScenarioWorker[] };
  board?: Board;
  team_summary?: import("../../teamView").TeamSummary;
  worker_messages?: Record<string, ConversationMessage[]>;
}
