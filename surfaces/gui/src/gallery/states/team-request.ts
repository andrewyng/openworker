// `team_proposed` payloads (and the parked `gate: "team"` Inbox item's data — same fields).
import type { CardState } from "../types";
import { launchTeam } from "./proposal-examples";

const MODELS = [
  { id: "anthropic:claude-opus-4-8", label: "Claude Opus 4.8" },
  { id: "anthropic:claude-sonnet-4-8", label: "Claude Sonnet 4.8" },
  { id: "openai:gpt-5.2", label: "GPT-5.2" },
  { id: "ollama:qwen3-coder:30b", label: "Qwen3 Coder 30B (local)" },
];
const OPUS = MODELS[0].id;
const SONNET = MODELS[1].id;

// The machine the workers run on: what it can run, and what each persona recommends there.
const MACHINE = {
  lead_mode: "auto-approve",
  lead_model: SONNET,
  runnable_models: MODELS,
  model_options: {
    "swe-worker": [OPUS, SONNET],
    "test-worker": [SONNET],
    "review-worker": [OPUS, SONNET],
    "docs-worker": [SONNET],
  },
};

const nia = { persona: "swe-worker", name: "nia", reason: "Builds the PDF endpoint and the download button", resolved_model: OPUS };
const checks = { persona: "test-worker", name: "checks", reason: "Writes the API and UI tests, runs the suite", resolved_model: SONNET };

// Large rosters, for designing the grouped card (owner ask 2026-09-17: teams of 10-20+).
const BUILD_JOBS = [
  "invoice PDF route", "statement renderer", "rate limiting on the PDF route", "download button",
  "error toasts", "meter import job", "usage rollups", "tariff table migration", "account search",
  "CSV export", "audit log for downloads", "retry policy for S3 reads",
];
function roster(builders: number, testers: number, reviewers: number, docs: number) {
  const names = ["nia", "omar", "lena", "kofi", "mei", "ravi", "sara", "tomas", "yuki", "amara", "dev", "elif"];
  const gh = (why: string) => ({ connectors: ["github"], connector_reasons: { github: why } });
  return [
    ...Array.from({ length: builders }, (_, i) => ({
      persona: "swe-worker", name: names[i % names.length] + (i >= names.length ? `-${i + 1}` : ""),
      reason: `Builds the ${BUILD_JOBS[i % BUILD_JOBS.length]}`, resolved_model: OPUS, ...gh("pushes its branch"),
    })),
    ...Array.from({ length: testers }, (_, i) => ({
      persona: "test-worker", name: `checks-${i + 1}`, reason: `Tests builders ${i * 2 + 1} and ${i * 2 + 2}`,
      resolved_model: SONNET,
    })),
    ...Array.from({ length: reviewers }, (_, i) => ({
      persona: "review-worker", name: `second-look-${i + 1}`, reason: "Reviews finished branches before the PR",
      resolved_model: OPUS, ...gh("comments on the branch diff"),
    })),
    ...Array.from({ length: docs }, (_, i) => ({
      persona: "docs-worker", name: i ? `scribe-${i + 1}` : "scribe", reason: "Keeps the runbook and the API spec current",
      resolved_model: SONNET,
    })),
  ];
}
const BIG_OFFER = { "swe-worker": ["github"], "test-worker": ["github"], "review-worker": ["github"], "docs-worker": [] };

export const teamRequestStates: CardState[] = [
  {
    id: "approval-guidance", title: "Approval guidance · Advanced",
    note: "Advanced is collapsed. Expand to review or edit the lead’s guidance before creating the team.",
    payload: {
      ...MACHINE,
      members: [
        { ...nia, name: "sam", approval_guidance: "Implement the assigned billing fix in your own worktree. Run local checks. Do not push, deploy or send messages." },
        { ...checks, name: "maya", approval_guidance: "Independently verify acceptance criteria in the local app. Save screenshots in your scratch directory and attach them to the board. No production access." },
      ],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: [], enable_chat: true,
    },
  },
  { id: "launch-team", title: "Launch team · ten workers", payload: launchTeam },
  {
    id: "lead-suggested-github",
    title: "Lead suggested GitHub",
    note: "nia arrives ticked for GitHub with the lead's reason; checks has GitHub offered but unticked.",
    payload: {
      ...MACHINE,
      members: [
        { ...nia, connectors: ["github"], connector_reasons: { github: "pushes the branch and opens the PR" } },
        checks,
      ],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: ["linear", "slack"],
      enable_chat: false,
      note: "Two workers: one builds, one verifies.",
    },
  },
  {
    id: "nothing-suggested",
    title: "Nothing suggested",
    note: "The lead named no connectors: everything in the usual set is offered, unticked.",
    payload: {
      ...MACHINE,
      members: [nia, checks],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: ["linear"],
      enable_chat: false,
      note: "",
    },
  },
  {
    id: "beyond-usual-set",
    title: "Beyond the usual set (Jira quoted)",
    note: "Jira is outside swe-worker's usual set; the lead may suggest it only with the human's own words.",
    payload: {
      ...MACHINE,
      members: [
        {
          ...nia,
          connectors: ["github", "jira"],
          connector_reasons: {
            github: "pushes the branch and opens the PR",
            jira: 'you asked: "track the work in Jira, ticket BILL-212"',
          },
        },
        checks,
      ],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: ["jira", "linear", "slack"],
      enable_chat: true,
      note: "nia updates BILL-212 as the work moves.",
    },
  },
  {
    id: "no-connectors-available",
    title: "No connectors available",
    note: "Nothing is connected on this machine: no offer, no expander.",
    payload: {
      ...MACHINE,
      members: [nia, checks],
      offer: { "swe-worker": [], "test-worker": [] },
      other_connected: [],
      enable_chat: false,
      note: "",
    },
  },
  {
    id: "model-fallback",
    title: "Model fallback warning (Manual lead)",
    note: "test-worker recommends only models this machine cannot run, so checks falls back to the lead's model. design-worker has no connectors in its usual set.",
    payload: {
      members: [
        { persona: "swe-worker", name: "nia", model: OPUS, resolved_model: OPUS, reason: "implementation", connectors: ["github"], connector_reasons: { github: "pushes the branch and opens the PR" } },
        { persona: "design-worker", name: "webb", model: SONNET, resolved_model: SONNET, reason: "UI polish" },
        {
          persona: "test-worker",
          name: "checks",
          model: SONNET,
          resolved_model: SONNET,
          model_warning:
            "None of test-worker's recommended models can run on this machine, so checks will use the lead's model (Claude Sonnet 4.8).",
          reason: "verifies against acceptance criteria",
        },
      ],
      enable_chat: false,
      note: "Three workers cover the plan; checks verifies before anything closes.",
      offer: { "swe-worker": ["github"], "design-worker": [], "test-worker": ["github"] },
      other_connected: ["linear"],
      lead_mode: "interactive",
      model_options: { "swe-worker": [OPUS, SONNET], "design-worker": [SONNET], "test-worker": [] },
      runnable_models: [
        { id: OPUS, label: "Claude Opus 4.8" },
        { id: SONNET, label: "Claude Sonnet 4.8" },
        { id: "anthropic:claude-haiku-4-8", label: "Claude Haiku 4.8" },
      ],
      lead_model: SONNET,
    },
  },
  {
    id: "non-recommended-model",
    title: "Non-recommended model",
    note: "The lead asked for a local model swe-worker does not recommend, so the server kept the recommendation. Choose one under “Other models” on nia's row to see the quiet note.",
    payload: {
      ...MACHINE,
      members: [{ ...nia, model: "ollama:qwen3-coder:30b", resolved_model: OPUS }, checks],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: [],
      enable_chat: false,
      note: "",
    },
  },
  {
    id: "old-server",
    title: "Old server (no model info)",
    note: "A box on an older build: no models, no other_connected, no lead mode. The card must still work.",
    payload: {
      members: [
        { persona: "swe-worker", name: "nia", reason: "Builds it" },
        { persona: "test-worker", name: "checks", reason: "Verifies it" },
      ],
      offer: { "swe-worker": ["github"], "test-worker": [] },
      enable_chat: false,
      note: "",
    },
  },
  {
    id: "old-server-raw-model-ids",
    title: "Old server, raw model ids",
    note: "What a card parked before 2026-09-17 carries: the lead's model as a provider:id string and nothing else. The card shows the name; the full id is in the hover text.",
    payload: {
      members: [
        { persona: "swe-worker", name: "nia", model: OPUS, reason: "Implements the api/ authorization fix and the web/ button gating for issue #3." },
        { persona: "test-worker", name: "checks", model: OPUS, reason: "Independently verifies the fix and proves the 403 + owner/staff paths with API tests." },
      ],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      enable_chat: true,
      note: "Two workers for issue #3, as requested: nia implements the api/ + web/ fix, checks independently proves the 403 and owner/staff paths with API tests. Chat enabled so nia and checks can align on handler status codes.",
    },
  },
  {
    id: "unknown-lead-mode",
    title: "Unknown lead mode",
    note: "Models are known but the lead's mode is not: the approvals line stays hidden.",
    payload: {
      ...MACHINE,
      lead_mode: undefined,
      members: [nia, checks],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: [],
      enable_chat: false,
      note: "",
    },
  },
  {
    id: "long-note",
    title: "Long note",
    note: "The lead's note clamps to two lines with More / Less.",
    payload: {
      ...MACHINE,
      members: [
        { ...nia, connectors: ["github"], connector_reasons: { github: "pushes the branch and opens the PR" } },
        checks,
      ],
      offer: { "swe-worker": ["github"], "test-worker": ["github"] },
      other_connected: ["linear"],
      enable_chat: true,
      note:
        "The issue asks for a PDF download on every invoice. nia adds GET /invoices/:id/pdf to the API, streams the stored statement from S3 with the billing role, and adds the button to the invoice drawer. checks writes API tests for the happy path, a missing file and a foreign account, plus a UI test for the button, then runs all three suites. I review both branches and open one pull request against main when the suite is green.",
    },
  },
  {
    id: "one-worker",
    title: "One worker",
    payload: {
      ...MACHINE,
      members: [{ ...nia, connectors: ["github"], connector_reasons: { github: "pushes the branch and opens the PR" } }],
      offer: { "swe-worker": ["github"] },
      other_connected: [],
      enable_chat: false,
      note: "Small change; one worker is enough.",
    },
  },
  {
    id: "six-workers",
    title: "Six workers",
    note: "Three builders share a line; the three single-worker roles keep their ordinary rows.",
    payload: {
      ...MACHINE,
      members: [
        { ...nia, connectors: ["github"], connector_reasons: { github: "pushes the API branch" } },
        { persona: "swe-worker", name: "omar", reason: "Builds the web client changes", resolved_model: OPUS, connectors: ["github"], connector_reasons: { github: "pushes the web branch" } },
        { persona: "swe-worker", name: "lena", reason: "Updates the statements worker", resolved_model: OPUS },
        checks,
        { persona: "review-worker", name: "second-look", reason: "Reviews the three branches before the PR", resolved_model: OPUS },
        { persona: "docs-worker", name: "scribe", reason: "Updates the runbook and the API spec", resolved_model: SONNET },
      ],
      offer: { "swe-worker": ["github"], "test-worker": ["github"], "review-worker": ["github"], "docs-worker": [] },
      other_connected: ["linear", "slack"],
      enable_chat: true,
      note: "Three builders in parallel, then tests, review and docs.",
    },
  },
  {
    id: "twelve-workers",
    title: "Twelve workers",
    note: "Four roles, twelve workers: one line per role, one model and one set of connectors for the role. “Show workers” opens the per-worker rows.",
    payload: {
      ...MACHINE,
      members: roster(6, 3, 2, 1),
      offer: BIG_OFFER,
      other_connected: ["linear", "slack"],
      enable_chat: true,
      note: "Six builders in parallel, one tester per two builders, two reviewers, one writer.",
    },
  },
  {
    id: "twenty-four-workers",
    title: "Twenty-four workers",
    note: "Twenty-four workers read as four lines. The testers' fallback warning sits on the role line.",
    payload: {
      ...MACHINE,
      // The fallback is a fact about the role on this machine, so every tester carries it.
      model_options: { ...MACHINE.model_options, "test-worker": [] },
      members: roster(12, 6, 4, 2).map((m) =>
        m.persona === "test-worker"
          ? { ...m, model_warning: "None of test-worker's recommended models can run on this machine. It will use the lead's model, Claude Sonnet 4.8." }
          : m,
      ),
      offer: BIG_OFFER,
      other_connected: ["linear", "slack"],
      enable_chat: true,
      note: "Twelve builders, six testers, four reviewers, two writers.",
    },
  },
  {
    id: "mixed-within-a-role",
    title: "Mixed within a role",
    note: "The lead split one role: two builders on another model, and one builder without GitHub. The role line says so without opening.",
    payload: {
      ...MACHINE,
      members: roster(6, 2, 0, 0).map((m, i) =>
        i === 4 || i === 5
          ? { ...m, resolved_model: SONNET, reason: `${m.reason} (small change)` }
          : i === 3
            ? { ...m, connectors: [], connector_reasons: {} }
            : m,
      ),
      offer: BIG_OFFER,
      other_connected: ["linear"],
      enable_chat: false,
      note: "Four builders on the larger model for the API work; two on the smaller one for copy changes.",
    },
  },
];
