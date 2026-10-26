// Synthetic Acme data only. Shared by the gallery, scenarios and UI tests.
import type { TeamSummary, TeamTask, TeamWorker } from "../../teamView";
import type { CardState } from "../types";
const usage = (n: number) => ({
  input: n * 10,
  cache_read: n * 40,
  output: n * 8,
  cache_write: n * 2,
});
export function sampleTeam(
  size = 5,
  waiting = true,
  idle = false,
): TeamSummary {
  const now = 1790000000,
    sid = "scn-team-view-" + size;
  const names = ["sam", "maya", "builder-2", "reviewer-1", "writer-1"];
  const roles = [
    "swe-worker",
    "test-worker",
    "swe-worker",
    "reviewer",
    "design-worker",
  ];
  const workers: TeamWorker[] = Array.from({ length: size }, (_, i) => ({
    actor: names[i] || "worker-" + (i + 1),
    role: roles[i % roles.length],
    session_id: sid + "-" + (names[i] || "worker-" + (i + 1)),
    model: "sample-model",
    workspace: "/workspace/acme/billing-service",
    running: !idle && i % 5 < 3,
    tokens: usage(i + 20),
    items: [i + 1],
  }));
  const titles = [
    "Fix refund rounding",
    "Verify refund paths",
    "Backfill affected invoices",
    "Review the rounding fix",
    "Update the refund runbook",
  ];
  const items: TeamTask[] = workers.map((w, i) => {
    const group = idle
      ? "done"
      : waiting && i === 1
        ? "waiting"
        : i % 5 === 4
          ? "done"
          : i % 5 === 3
            ? "queued"
            : i % 5 === 2
              ? "review"
              : "working";
    return {
      id: i + 1,
      title: titles[i] || "Verify billing module " + (i + 1),
      description:
        "Check the invoice refund behavior against the approved criteria.",
      criteria: "Regression tests pass; the reviewer verifies the result.",
      state:
        group === "waiting"
          ? "blocked"
          : group === "working"
            ? "in_progress"
            : group === "queued"
              ? "open"
              : group,
      group,
      assignee: w.actor,
      creator: "lead",
      refs: i === 2 ? ["https://github.com/acme/billing-service/pull/42"] : [],
      links: [],
      status:
        group === "working" ? "Running regression tests · 18 of 24 passed" : "",
      blocker:
        group === "waiting"
          ? "Should the verification run against a copy or staging?"
          : undefined,
      waiting:
        group === "waiting"
          ? {
              prompt_id: "sample-prompt",
              tool: "run_shell",
              preview: "Run on a copy first, or go straight to staging?",
            }
          : undefined,
      updated: now - i * 60,
      created_ts: new Date((now - 2520) * 1000).toISOString(),
      elapsed_s: 2520,
      worker_session: w.session_id,
      step: group === "working" ? { done: 1, total: 4 } : undefined,
      pr:
        i === 2 ? "https://github.com/acme/billing-service/pull/42" : undefined,
      tokens: w.tokens,
      timing: {
        model_ms: 540000,
        tool_ms: 840000,
        waited_ms: 180000,
        queued_ms: 960000,
      },
    };
  });
  const lead: TeamWorker = {
    actor: "lead",
    role: "lead",
    session_id: sid,
    model: "sample-model",
    workspace: "/workspace/acme/billing-service",
    running: false,
    tokens: usage(60),
    items: [],
  };
  const kinds = { input: 0, cache_read: 0, output: 0, cache_write: 0 };
  for (const w of [lead, ...workers])
    for (const k of Object.keys(kinds) as (keyof typeof kinds)[])
      kinds[k] += w.tokens[k];
  const counts = { waiting: 0, review: 0, working: 0, queued: 0, done: 0 };
  for (const i of items) if (i.group !== "canceled") counts[i.group]++;
  return {
    team_id: "sample-team-" + size,
    space: "/workspace/acme/billing-service",
    lead_session: sid,
    workers,
    lead,
    items,
    counts,
    totals: {
      done: counts.done,
      total: size,
      elapsed_s: 2520,
      tokens: Object.values(kinds).reduce((a, b) => a + b, 0),
      tokens_by_kind: kinds,
      asks_answered: 2,
      asks_waiting: waiting && !idle ? 1 : 0,
    },
    timing: {
      model_ms: size * 540000,
      tool_ms: size * 840000,
      waited_ms: size * 180000,
      queued_ms: size * 960000,
    },
    asks: {
      approvals: { answered: 1, waiting: waiting && !idle ? 1 : 0 },
      staffing: { answered: 1, waiting: 0 },
      questions: { answered: 0, waiting: 0 },
      reviews: { answered: 0, waiting: 0 },
      grants: { answered: 0, waiting: 0 },
    },
    median_answer_s: 240,
    generated_at: now,
    usage_points: [lead, ...workers]
      .flatMap((w) =>
        [0, 1, 2].map((i) => ({
          ts: now - 2400 + i * 800,
          role: w.role,
          tokens: Object.values(w.tokens).reduce((a, b) => a + b, 0) / 3,
        })),
      )
      .sort((a, b) => a.ts - b.ts),
  };
}
export const teamViewStates: CardState[] = [
  {
    id: "worker-request", title: "Worker request without a task", payload: { summary: {
      ...sampleTeam(5, false),
      totals: { ...sampleTeam(5, false).totals, asks_waiting: 1 },
      asks: { ...sampleTeam(5, false).asks, approvals: { answered: 1, waiting: 1 } },
      pending_requests: [{ id: "sample-request", session_id: "sample-worker", worker: "Sam", title: "Run local regression tests", kind: "approvals", represented_by_task: false }],
    } },
  },
  {
    id: "working",
    title: "Five workers working",
    payload: { summary: sampleTeam(5, false) },
  },
  {
    id: "needs-you",
    title: "A task needs an answer",
    payload: { summary: sampleTeam() },
  },
  {
    id: "idle",
    title: "Work completed",
    payload: { summary: sampleTeam(5, false, true) },
  },
  {
    id: "twelve-workers",
    title: "Twelve workers",
    payload: { summary: sampleTeam(12) },
  },
  {
    id: "hundred-workers",
    title: "A hundred workers",
    payload: { summary: sampleTeam(100) },
  },
];
