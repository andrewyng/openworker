// Synthetic, executable tool payloads. The gallery never classifies tasks from prose.
export const launchProposal = {
  title: "Give every customer a launch plan",
  summary:
    "Turn a won deal into an owned, dated plan—and show what changes when a milestone slips.",
  targets: ["acme/billing-service · local workspace"],
  external_actions: {
    status: "none",
    actions: [],
    exclusions: ["Pushes, deployments, and outbound messages"],
    explanation: "This rehearsal is limited to local changes and verification.",
  },
  activities: [
    { id: "build", title: "Build", summary: "Implement the customer journey" },
    {
      id: "verify",
      title: "Verify",
      summary: "Independently check the results",
    },
    { id: "accept", title: "Accept", summary: "Review the complete outcome" },
  ],
  workstreams: [
    {
      id: "foundation",
      title: "Plan, schedule & dependencies",
      summary: "Shared data and scheduling foundation",
    },
    {
      id: "experience",
      title: "Workspace, timeline & portfolio",
      summary: "Independent customer-facing surfaces",
    },
    {
      id: "followthrough",
      title: "Templates & reminders",
      summary: "Reusable plans and local follow-through",
    },
    {
      id: "quality",
      title: "Verification & acceptance",
      summary: "Independent evidence and final judgment",
    },
  ],
  items: [
    {
      key: "plan",
      title: "Launch plan data & API",
      activity: "build",
      workstream: "foundation",
      depends_on: [],
      verifies: [],
      criteria:
        "A won deal creates an owned plan; existing accounts remain intact.",
    },
    {
      key: "graph",
      title: "Dependencies & impact preview",
      activity: "build",
      workstream: "foundation",
      depends_on: [],
      verifies: [],
      criteria:
        "A three-day slip previews affected successors before applying changes; dependency cycles are rejected.",
    },
    {
      key: "workspace",
      title: "Launch workspace",
      activity: "build",
      workstream: "experience",
      depends_on: [],
      verifies: [],
      criteria: "Owners can manage tasks without bypassing required work.",
    },
    {
      key: "timeline",
      title: "Interactive timeline",
      activity: "build",
      workstream: "experience",
      depends_on: [],
      verifies: [],
      criteria: "Dates match the workspace after a schedule change.",
    },
    {
      key: "portfolio",
      title: "Portfolio attention",
      activity: "build",
      workstream: "experience",
      depends_on: [],
      verifies: [],
      criteria:
        "Teams can find overdue launches with correct access boundaries.",
    },
    {
      key: "templates",
      title: "Versioned launch templates",
      activity: "build",
      workstream: "followthrough",
      depends_on: [],
      verifies: [],
      criteria:
        "New launches use the chosen version; existing plans are unchanged.",
    },
    {
      key: "reminders",
      title: "Local reminders",
      activity: "build",
      workstream: "followthrough",
      depends_on: [],
      verifies: [],
      criteria: "Due-date changes update in-app reminders without duplicates.",
    },
    {
      key: "api-check",
      title: "API & permissions",
      activity: "verify",
      workstream: "quality",
      depends_on: ["plan", "graph"],
      verifies: ["plan", "graph"],
      criteria:
        "Tests cover tenant boundaries, roles, completion rules, and scheduling.",
    },
    {
      key: "browser-check",
      title: "Browser journey & evidence",
      activity: "verify",
      workstream: "quality",
      depends_on: ["workspace", "timeline", "portfolio"],
      verifies: ["workspace", "timeline", "portfolio"],
      criteria:
        "The full journey passes with screenshots attached to board items.",
    },
    {
      key: "upgrade-check",
      title: "Upgrade & regression",
      activity: "verify",
      workstream: "quality",
      depends_on: ["templates", "reminders"],
      verifies: ["templates", "reminders"],
      criteria: "Existing data survives and the previous journey still works.",
    },
    {
      key: "accept",
      title: "Accept the complete journey",
      activity: "accept",
      workstream: "quality",
      depends_on: ["api-check", "browser-check", "upgrade-check"],
      verifies: [],
      criteria:
        "Win a deal, adjust a milestone, verify affected views, and complete the plan.",
    },
  ],
  final_acceptance: { item_key: "accept", owner: "lead" },
};

export const securityProposal = {
  title: "Close the staging exposure gaps",
  summary:
    "Assess the approved staging targets, prepare fixes, and independently recheck the findings.",
  targets: ["acme/billing-service", "sandbox-1 · approved staging environment"],
  external_actions: {
    status: "planned",
    actions: [
      "Run authenticated checks against sandbox-1",
      "Open remediation pull requests in acme/billing-service",
    ],
    exclusions: [
      "Production scans or infrastructure changes",
      "Merging remediation pull requests",
    ],
    explanation:
      "Only the staging targets named in the request are in scope. Active scans contact external systems.",
  },
  activities: [
    { id: "assess", title: "Assess", summary: "Establish exposure" },
    { id: "remediate", title: "Remediate", summary: "Prepare fixes" },
    { id: "validate", title: "Validate", summary: "Independently recheck" },
  ],
  workstreams: [
    {
      id: "application",
      title: "Application security",
      summary: "Repository and dependency findings",
    },
    {
      id: "cloud",
      title: "Staging infrastructure",
      summary: "Exposure and configuration checks",
    },
    {
      id: "review",
      title: "Remediation & validation",
      summary: "Evidence-backed closure",
    },
  ],
  items: [
    {
      key: "code",
      title: "Assess repository findings",
      activity: "assess",
      workstream: "application",
      depends_on: [],
      verifies: [],
      criteria:
        "Findings identify affected code and include reproducible evidence.",
    },
    {
      key: "infra",
      title: "Assess staging exposure",
      activity: "assess",
      workstream: "cloud",
      depends_on: [],
      verifies: [],
      criteria:
        "Checks stay within the approved staging targets and report coverage gaps.",
    },
    {
      key: "fix",
      title: "Prepare remediation changes",
      activity: "remediate",
      workstream: "review",
      depends_on: ["code", "infra"],
      verifies: [],
      criteria:
        "Each proposed fix references its finding and includes regression coverage.",
    },
    {
      key: "validate",
      title: "Accept independently verified findings",
      activity: "validate",
      workstream: "review",
      depends_on: ["fix"],
      verifies: ["fix"],
      criteria:
        "Reported vulnerabilities no longer reproduce; unresolved risks remain explicitly documented.",
    },
  ],
  final_acceptance: { item_key: "validate", owner: "assigned_worker" },
};

export const marketingProposal = {
  title: "Prepare the customer launch campaign",
  summary:
    "Research the audience, create campaign drafts, and review claims before publication is considered.",
  targets: ["Acme · customer launch campaign"],
  external_actions: {
    status: "undetermined",
    actions: [],
    exclusions: [
      "Publishing before the user chooses channels and approves the copy",
    ],
    explanation:
      "Publication channels and recipients have not been selected. Clarification is needed before any publishing or sending.",
  },
  activities: [
    { id: "research", title: "Research", summary: "Establish the audience" },
    { id: "create", title: "Create", summary: "Draft assets" },
    { id: "review", title: "Review", summary: "Verify claims and tone" },
  ],
  workstreams: [
    {
      id: "campaign",
      title: "Campaign preparation",
      summary: "Audience, drafts, and publication-ready review",
    },
  ],
  items: [
    {
      key: "research",
      title: "Audience and message brief",
      activity: "research",
      workstream: "campaign",
      depends_on: [],
      verifies: [],
      criteria:
        "The brief identifies the audience and cites sources for factual claims.",
    },
    {
      key: "draft",
      title: "Campaign drafts",
      activity: "create",
      workstream: "campaign",
      depends_on: ["research"],
      verifies: [],
      criteria:
        "Drafts match the approved brief and make no unsupported claims.",
    },
    {
      key: "review",
      title: "Review campaign assets",
      activity: "review",
      workstream: "campaign",
      depends_on: ["draft"],
      verifies: ["draft"],
      criteria:
        "Claims, tone, and links are checked; publication requires a separate decision.",
    },
  ],
  final_acceptance: { item_key: "review", owner: "lead" },
};

export const launchTeam = {
  title: "The customer launch team",
  summary:
    "Seven builders own separate workstreams. Three verifiers independently check their results.",
  groups: [
    {
      id: "builders",
      title: "Builders",
      summary: "One owner per implementation workstream",
    },
    {
      id: "verifiers",
      title: "Verifiers",
      summary: "Independent checks and board evidence",
    },
  ],
  members: launchProposal.items.slice(0, 10).map((task, i) => ({
    persona: i < 7 ? "swe-worker" : "test-worker",
    name: `${i < 7 ? "sam" : "maya"}-${i + 1}`,
    reason: task.title,
    group: i < 7 ? "builders" : "verifiers",
    item_ids: [i + 1],
    connectors: [],
    resolved_model: "anthropic:claude-sonnet-4-8",
  })),
  enable_chat: true,
  planned_items: launchProposal.items.map((task, i) => ({
    id: i + 1,
    title: task.title,
    final_acceptance: {
      id: 11,
      title: "Accept the complete journey",
      owner: "lead",
    },
  })),
  lead_mode: "interactive",
  offer: {},
  other_connected: [],
  runnable_models: [
    { id: "anthropic:claude-sonnet-4-8", label: "Claude Sonnet 4.8" },
  ],
  lead_model: "anthropic:claude-sonnet-4-8",
  model_options: {},
};
