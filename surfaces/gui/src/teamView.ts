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
  coverage?: TaskCoverage | null;
  findings?: TaskFinding[];
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
  // OB-17 (Phase 3): why each team turn started, from the member sessions' recordings (their
  // total equals their pills), and the team events that used to be silent. Absent on older engines.
  wakes?: TeamWake[];
  events?: TeamEvent[];
  wake_explained?: { turns: number; explained: number };
}

export interface TeamWake {
  reason: string;
  wakes: number;
  total: number;
  cache_read: number;
  input: number;
  at_least?: boolean;
  by_role: Record<string, { wakes: number; total: number }>;
}

export interface TeamEvent {
  kind: "wake_capped" | "deferred" | "watchdog_nudged" | "lead_polling" | "same_approval_twice"
    | "reviewed_before_prereq" | "wake_cap_reached" | string;
  severity: "warn" | "info";
  at: number | null;
  who?: string;
  session_id?: string;
  turn_id?: string | null;
  seq?: number | null;
  deferred_ms?: number | null;
  by?: string;
  count?: number;
  item_ids?: string[];
  tool?: string;
  first_by?: string;
  again_by?: string;
  item?: number;
  prerequisite?: number;
  to?: string;
  hour?: string;
  held?: string[];
  board_seq?: number;
}

// OB-16 (Phase 3): a security task's coverage and its findings' evidence, when the engine
// gives them on the summary's items. Absent: nothing is shown.
export interface CoverageScan {
  scanner: string;
  area: string;
  exit_code: number | null;
  ok: boolean;
  found_something?: boolean;
  session_id?: string;
  turn_id?: string;
  turn_seq?: number | null;
  step_seq?: number | null;
  step_id?: string;
  actor?: string;
}
export interface TaskCoverage {
  promised: string[];
  scans: CoverageScan[];
  issues: { check: string; text: string; facts?: Record<string, any> }[];
  fine?: { check: string; text: string }[];
  not_checked?: boolean;
}
export interface TaskFinding {
  id: number | string;
  severity?: string;
  title?: string;
  evidence?: { session_id: string; call_id: string }[];
  step_call_id?: string | null;
  fixed_by?: string | null;
  verified_by?: string | null;
  // each scanner's "found something" exit codes (signals.yaml `scanners`)
  findings_exit?: Record<string, number[]>;
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
