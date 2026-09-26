// `permission_required` payloads.
import type { CardState } from "../types";

export const approvalStates: CardState[] = [
  {
    id: "human-required", title: "A mandatory human decision", context: { mode: "auto-approve" },
    payload: { name: "write_file", arguments: { path: ".github/workflows/check.yml", content: "name: Check\non: push\n" },
      reason: "Protected project configuration", escalation: { kind: "human_required", reason: "This file executes in CI. A person must approve changes." } },
  },
  {
    id: "shell-command",
    title: "Shell command",
    note: "The common case in Manual mode: the command-scoped grant is offered, the tool-wide one is not.",
    payload: {
      name: "run_shell",
      arguments: { command: "npm test -- --run invoices" },
      reason: "Run the API tests for the new route",
      category: "shell",
    },
  },
  {
    id: "read-only-command",
    title: "Read-only command",
    note: "The server classified the command as a local read, so the session-wide read-only grant appears.",
    payload: {
      name: "run_shell",
      arguments: { command: "ls" },
      reason: "The coworker wants to run a command.",
      readonly_ok: true, // `ls` classifies read-only server-side
    },
  },
  {
    id: "reviewer-unsure",
    title: "Auto-Approve: reviewer was unsure",
    note: "Auto-Approve session: no session grants, and the reviewer's one-line reason is shown quietly.",
    context: { mode: "auto-approve" },
    payload: {
      name: "run_shell",
      arguments: { command: "python3 helper.py" },
      reason: "requires approval",
      reviewer_unsure: "This runs a newly created script whose effects cannot be determined from the command.",
      escalation: { kind: "reviewer_unsure", reason: "This runs a newly created script whose effects cannot be determined from the command." },
    },
  },
  {
    id: "provenance-warning",
    title: "Runs a file the agent wrote",
    note: "The provenance line is the one fact the command text cannot show.",
    payload: {
      name: "run_shell",
      arguments: { command: "python3 scripts/backfill_pdf_keys.py --apply" },
      reason: "Backfill the missing keys",
      category: "shell",
      provenance: "backfill_pdf_keys.py was created by the agent 3 steps ago",
    },
  },
  {
    id: "write-file",
    title: "File write (compact row)",
    note: "Routine workspace writes are one line; the preview opens inline.",
    payload: {
      name: "write_file",
      arguments: {
        path: "src/fetch_data.py",
        content: "import json\nimport urllib.request\n\ncompanies = [\"NVDA\", \"AMD\"]\nprint(len(companies))\ndone = True",
      },
      reason: "",
    },
  },
  {
    id: "web-fetch",
    title: "Web fetch (leaves this computer)",
    note: "Egress: the scope note names the host and the session grant is domain-shaped.",
    payload: {
      name: "web_fetch",
      arguments: { url: "https://docs.stripe.com/api/invoices/object" },
      reason: "Check the invoice object's PDF field",
      category: "network",
    },
  },
  {
    id: "connector-action",
    title: "Connector action",
    note: "Acts on a connected service: no session-wide grant.",
    payload: {
      name: "github_create_pull_request",
      arguments: { repo: "acme/billing-service", title: "Invoice PDF download", base: "main", head: "lead/invoice-pdf" },
      reason: "Open the pull request for issue #3",
      category: "connector",
    },
  },
  {
    id: "automation-run",
    title: "Inside an automation run",
    note: "A run context plus a standing target offers “Allow every time” in place of the session grant.",
    context: { runTask: { id: "task-7f2c", title: "Weekly digest" } },
    payload: {
      name: "send_message",
      arguments: { target: "slack:T1/C1", text: "Weekly digest ready" },
      reason: "",
      category: "messaging",
      standing_target: "slack:T1/C1",
    },
  },
  {
    id: "long-message",
    title: "Long message, no newlines",
    note: "A one-paragraph digest once ballooned the card to full-transcript height; the preview clamps by characters.",
    payload: {
      name: "send_message",
      arguments: {
        target: "slack:T1/C1",
        text:
          "aisuite — last 24 hours of work (through Jul 15): 5 PRs merged covering chat-completion streaming with unified chunks across providers, multimodal input conversion, Slack collaboration improvements, human attribution for outbound posts, and repo-wide formatting. ".repeat(
            6,
          ),
      },
      reason: "",
      category: "messaging",
    },
  },
  {
    id: "automation-consent",
    title: "Create an automation (consent card)",
    note: "The agent proposes the automation's permission set; the card lists what it grants.",
    payload: {
      name: "create_scheduled_task",
      arguments: {
        title: "Weekly digest",
        instructions: "Summarize the week and post it.",
        cron: "0 9 * * 1",
        permissions: [
          { tool: "send_message", target: "slack:T1/C1", access: "write" },
          { tool: "github_list_commits", target: "rohit/agent-platform", access: "read" },
        ],
      },
      reason: "",
      category: "automation",
    },
  },
  {
    id: "long-command",
    title: "Long command",
    note: "The preview clamps by lines and by characters.",
    payload: {
      name: "run_shell",
      arguments: {
        command:
          "set -euo pipefail\ncd infra\nterraform init -backend-config=envs/demo.backend.hcl\nterraform plan -var-file=envs/demo.tfvars -out=plan.bin\nterraform show -json plan.bin > plan.json\njq '[.resource_changes[] | select(.change.actions | index(\"delete\"))] | length' plan.json\nterraform apply plan.bin",
      },
      reason: "Apply the bucket policy change",
      category: "shell",
    },
  },
  {
    id: "minimal",
    title: "Minimal payload",
    note: "Name and arguments only, as an old server sends.",
    payload: { name: "run_shell", arguments: { command: "ls" } },
  },
];
