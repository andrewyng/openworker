import type { BoardItem } from "./api";
export type TokenKinds = {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
};
export type Timing = {
  model_ms: number;
  tool_ms: number;
  waited_ms: number;
  queued_ms: number;
};
export type TeamGroup =
  | "waiting"
  | "review"
  | "working"
  | "queued"
  | "done"
  | "canceled";
export interface TeamTask extends BoardItem {
  group: TeamGroup;
  updated: number;
  last_event?: string;
  worker_session?: string;
  step?: { done: number; total: number };
  pr?: string;
  elapsed_s: number;
  tokens: TokenKinds;
  timing?: Timing | null;
  timing_partial?: boolean;
  usage_partial?: boolean;
}
export interface TeamWorker {
  usage_partial?: boolean;
  actor: string;
  role: string;
  session_id: string;
  model: string;
  workspace: string;
  running: boolean;
  tokens: TokenKinds;
  items: number[];
  step?: { done: number; total: number };
}
export interface TeamSummary {
  team_id: string;
  space: string;
  lead_session: string;
  items: TeamTask[];
  workers: TeamWorker[];
  lead: TeamWorker;
  pending_requests?: { id: string; session_id: string; worker: string; title: string; kind: string; represented_by_task: boolean }[];
  counts: Record<Exclude<TeamGroup, "canceled">, number>;
  totals: {
    done: number;
    total: number;
    elapsed_s: number;
    tokens: number;
    tokens_by_kind: TokenKinds;
    asks_answered: number;
    asks_waiting: number;
  };
  timing?: Timing | null;
  timing_partial?: boolean;
  usage_partial?: boolean;
  asks: Record<string, { answered: number; waiting: number }>;
  median_answer_s?: number | null;
  usage_points: { ts: number; role: string; tokens: number }[];
  generated_at: number;
}
export const tokenTotal = (u: TokenKinds) =>
  u.input + u.output + u.cache_read + u.cache_write;
export const GROUPS: Exclude<TeamGroup, "canceled">[] = [
  "waiting",
  "review",
  "working",
  "queued",
  "done",
];
