// Synthetic sample events; no real-run notes or identifiers.
import type { CardState } from "../types";
const source = (rows: Record<string, unknown>[]) => ({
  connector: "board",
  kind: "channel",
  channel_id: "/workspace/acme/billing-service",
  channel_name: "Acme",
  sender_id: "board",
  sender_name: "Board",
  ts: 1790000000,
  text: "Team update",
  board: { rows },
});
const rows = [
  {
    kind: "assigned",
    item: 1,
    title: "Fix refund rounding",
    actor: "lead",
    assignee: "sam",
  },
  { kind: "claimed", item: 2, title: "Verify refund paths", actor: "maya" },
  {
    kind: "moved",
    item: 1,
    title: "Fix refund rounding",
    actor: "sam",
    from: "in_progress",
    to: "review",
    refs: ["https://github.com/acme/billing-service/pull/42"],
  },
  { kind: "filed", item: 3, title: "Add regression coverage", actor: "maya" },
  {
    kind: "comment",
    item: 2,
    title: "Verify refund paths",
    actor: "maya",
    note: "Sample note: full evidence stays on the task.",
  },
  { kind: "chat", actor: "sam", note: "Sample chat question" },
  {
    kind: "waiting",
    item: 2,
    title: "Verify refund paths",
    actor: "maya",
    tool: "run_shell",
    prompt_id: "sample-prompt",
  },
];
export const boardWakeStates: CardState[] = [
  {
    id: "collapsed",
    title: "Collapsed update",
    payload: { source: source(rows) },
  },
  {
    id: "expanded",
    title: "All row kinds, expanded",
    payload: { source: source(rows), expanded: true },
  },
  {
    id: "folded",
    title: "Two consecutive updates",
    payload: { sources: [source(rows.slice(0, 2)), source(rows.slice(2))] },
  },
  {
    id: "waiting-and-review",
    title: "Waiting and review",
    payload: {
      source: source(rows.filter((r) => ["waiting", "moved"].includes(r.kind))),
    },
  },
  {
    id: "review-and-filed",
    title: "Review and filing",
    payload: {
      source: source(rows.filter((r) => ["moved", "filed"].includes(r.kind))),
    },
  },
  {
    id: "minimal",
    title: "No notable changes",
    payload: { source: source([]) },
  },
];
