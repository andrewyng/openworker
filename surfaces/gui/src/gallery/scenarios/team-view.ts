import type { Scenario } from "./types";
import { sampleTeam } from "../states/team-view";

export const teamViewScenarios: Scenario[] = [5, 12, 100].map((size) => {
  const summary = sampleTeam(size);
  const source = (rows: Record<string, unknown>[], ts: number) => ({
    connector: "board",
    kind: "channel" as const,
    channel_id: summary.space,
    channel_name: "Acme",
    sender_id: "board",
    sender_name: "Board",
    ts,
    text: "Team update",
    board: { rows: rows as any },
  });
  return {
    id: "team-view-" + size,
    title: "Team View · " + size + " workers",
    note: "Synthetic Acme sample. No model runs and no messages are sent. Explore the team icon, task chips, worker conversation and all three tabs.",
    session: {
      session_id: summary.lead_session,
      title: "Refund rounding · Acme",
      agent: "swe-lead",
      model: "sample-model",
      mode: "interactive",
      workspace: summary.space,
    },
    team: {
      team_id: summary.team_id,
      workers: summary.workers.map((w) => ({
        name: w.actor,
        persona: w.role,
        model: w.model,
      })),
    },
    board: { space: summary.space, name: "Acme", items: summary.items },
    team_summary: summary,
    worker_messages: Object.fromEntries(
      summary.workers.map((w) => [
        w.session_id,
        [
          {
            role: "user",
            content:
              "Verify the refund calculation against the acceptance criteria.",
          },
          {
            role: "assistant",
            content:
              "Reproduced the rounding error with a three-line refund. Checking the original invoice rate now.",
          },
          {
            role: "assistant",
            content: "",
            tool_calls: [
              {
                id: "sample-read",
                function: {
                  name: "read_file",
                  arguments: JSON.stringify({ path: "billing/refunds.py" }),
                },
              },
            ],
          },
          {
            role: "tool",
            tool_call_id: "sample-read",
            content: "Sample refund calculation",
          },
          {
            role: "assistant",
            content:
              "The regression covers partial refunds and mixed tax rates. The next check is the invoice backfill.",
          },
        ],
      ]),
    ),
    messages: [
      {
        role: "user",
        content:
          "Take the refund rounding issue on acme/billing-service. Staff it and get a verified fix ready for review.",
      },
      {
        role: "assistant",
        content:
          "I’ll split the implementation, verification and review. I’ll flag anything that needs your answer.",
      },
      {
        role: "assistant",
        content: "",
        tool_calls: [
          {
            id: "staff",
            function: {
              name: "propose_team",
              arguments: JSON.stringify({ members: [] }),
            },
          },
        ],
      },
      {
        role: "tool",
        tool_call_id: "staff",
        content: JSON.stringify({ approved: true, team_id: summary.team_id }),
        _display: {
          team_created: {
            team_id: summary.team_id,
            workers: summary.workers.map((w) => ({
              actor: w.actor,
              persona: w.role,
            })),
          },
        },
      },
      {
        role: "user",
        content: "Team update",
        source: source(
          [
            {
              kind: "moved",
              item: 1,
              title: "Fix refund rounding",
              actor: "sam",
              from: "open",
              to: "in_progress",
            },
          ],
          summary.generated_at - 600,
        ),
      },
      {
        role: "assistant",
        content: "",
        tool_calls: [
          { id: "check", function: { name: "list_items", arguments: "{}" } },
        ],
      },
      { role: "tool", tool_call_id: "check", content: '{"items":[]}' },
      {
        role: "user",
        content: "Team update",
        source: source(
          [
            {
              kind: "moved",
              item: 3,
              title: "Backfill affected invoices",
              actor: "builder-2",
              from: "in_progress",
              to: "review",
            },
          ],
          summary.generated_at - 300,
        ),
      },
      {
        role: "assistant",
        content:
          "The backfill is [ready for review](task:3). [Refund rounding](task:1) is still being checked.",
      },
      {
        role: "user",
        content: "Keep refunds older than 90 days out of scope.",
      },
      {
        role: "assistant",
        content:
          "I’ll have the worker narrow [the backfill](task:3). Maya needs a call on where to run [verification](task:2).",
      },
    ],
  };
});
