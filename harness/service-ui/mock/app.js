const paths = {
  overview: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  agents: "M8 4V2m8 2V2M5 7h14v13H5zM9 12h.01M15 12h.01M9 16h6M2 11v5m20-5v5",
  sessions: "M4 4h16v12H9l-5 4V4zM8 8h8M8 12h5",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  infra: "M4 3h16v7H4zM4 14h16v7H4zM7 6h.01M7 17h.01M12 6h5M12 17h5",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM9 3l-1 3-3 1-2 3 2 2-1 3 2 3 3-1 3 2 3-2 3 1 2-3-1-3 2-2-2-3-3-1-1-3H9z",
  search: "m21 21-5-5M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  plus: "M12 5v14M5 12h14",
  close: "m6 6 12 12M6 18 18 6",
  check: "m5 12 4 4L19 6",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v5l3 2",
  terminal: "m5 6 5 6-5 6M12 18h7",
  file: "M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3zm-4 9 3 3 5-6",
  bolt: "m13 2-9 12h7l-1 8 10-13h-7l1-7",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  menu: "M4 6h16M4 12h16M4 18h16",
  external: "M14 3h7v7M10 14 21 3M10 3H3v18h18v-7",
  branch:
    "M6 3v12a4 4 0 0 0 4 4h8M6 6h7a4 4 0 0 0 4-4M3 18a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
  chart: "M4 20V10m8 10V4m8 16v-7",
  globe:
    "M3 12h18M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 3c5 5 5 13 0 18-5-5-5-13 0-18",
  copy: "M8 8h12v12H8zM16 8V4H4v12h4",
  play: "m8 5 12 7-12 7V5z",
  pause: "M8 5v14M16 5v14",
  key: "M15 3a6 6 0 1 0 0 12 6 6 0 0 0 0-12M10 14l-7 7m3-3-2-2m5-1-2-2",
  layers: "m12 3 10 5-10 5L2 8l10-5zM2 12l10 5 10-5M2 16l10 5 10-5",
};
const icon = (name, cls = "") =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`;
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const agents = [
  {
    id: "reviewer",
    name: "Code reviewer",
    icon: "branch",
    description:
      "Reviews changes, checks edge cases, and leaves a clear summary.",
    model: "OpenAI · Sol",
    version: "v3",
    runs: 64,
    tools: 6,
    profile: "Standard",
    instructions:
      "Review the changes in the pull request. Check correctness and tests, then summarize the findings.",
  },
  {
    id: "builder",
    name: "Game builder",
    icon: "terminal",
    description: "Turns an idea into a playable game, one iteration at a time.",
    model: "Anthropic · Sonnet",
    version: "v2",
    runs: 48,
    tools: 5,
    profile: "Standard",
    instructions:
      "Build small, playful HTML, CSS and JavaScript games. Verify your work before asking to publish.",
  },
  {
    id: "researcher",
    name: "Research assistant",
    icon: "globe",
    description:
      "Finds relevant sources and turns research into useful answers.",
    model: "Local · Qwen",
    version: "v1",
    runs: 30,
    tools: 4,
    profile: "Autonomous",
    instructions:
      "Gather relevant evidence and produce a concise, well-sourced answer. Ask when the scope is unclear.",
  },
];
const sessions = [
  {
    id: "ses_7f2a9c",
    title: "Review checkout retry logic",
    agent: "reviewer",
    status: "running",
    duration: "1m 42s",
    started: "2 min ago",
    tokens: "8,420",
    modelCalls: 3,
    toolCalls: 5,
  },
  {
    id: "ses_b9c104",
    title: "Build a space shooter",
    agent: "builder",
    status: "waiting",
    duration: "3m 18s",
    started: "4 min ago",
    tokens: "14,280",
    modelCalls: 5,
    toolCalls: 8,
  },
  {
    id: "ses_c41e8b",
    title: "Compare local inference options",
    agent: "researcher",
    status: "running",
    duration: "48s",
    started: "1 min ago",
    tokens: "3,240",
    modelCalls: 2,
    toolCalls: 3,
  },
  {
    id: "ses_4a8b21",
    title: "Review pagination changes",
    agent: "reviewer",
    status: "completed",
    duration: "2m 06s",
    started: "18 min ago",
    tokens: "9,610",
    modelCalls: 4,
    toolCalls: 6,
  },
  {
    id: "ses_63d0a9",
    title: "Add a leaderboard to Neon Snake",
    agent: "builder",
    status: "completed",
    duration: "4m 12s",
    started: "32 min ago",
    tokens: "18,460",
    modelCalls: 6,
    toolCalls: 11,
  },
  {
    id: "ses_a29f31",
    title: "Summarize the release notes",
    agent: "researcher",
    status: "completed",
    duration: "1m 24s",
    started: "46 min ago",
    tokens: "5,320",
    modelCalls: 2,
    toolCalls: 4,
  },
];
let filter = "All",
  query = "",
  sessionTab = "Trace",
  settingsTab = "Defaults",
  workers = 2,
  pendingWorkers = 2,
  paused = false,
  toastTimer;
const $ = (s) => document.querySelector(s),
  main = $("#main"),
  panel = $("#panel"),
  modal = $("#modal");
const agentFor = (s) => agents.find((a) => a.id === s.agent) || agents[0];
const status = (s) =>
  `<span class="status ${s}"><i></i>${{ running: "Running", waiting: "Needs approval", completed: "Completed", cancelled: "Cancelled", failed: "Failed" }[s] || s}</span>`;
const btn = (label, action, primary = false, ic = "") =>
  `<button class="button ${primary ? "primary" : ""}" data-action="${action}">${ic ? icon(ic) : ""}${label}</button>`;
const title = (name, sub = "", actions = "") =>
  `<div class="page-heading"><div><h1>${name}</h1>${sub ? `<p>${sub}</p>` : ""}</div><div class="heading-actions">${actions}</div></div>`;
const navItems = [
  ["overview", "Overview", "overview"],
  ["agents", "Agents", "agents"],
  ["sessions", "Sessions", "sessions"],
  ["activity", "Activity", "activity"],
  ["infrastructure", "Infrastructure", "infra"],
];
$("#nav").innerHTML = navItems
  .map(
    ([id, label, ic], i) =>
      `${i === 4 ? '<div class="nav-label">Manage</div>' : ""}<a class="nav-link" href="#${id}" data-nav="${id}">${icon(ic)}${label}${id === "sessions" ? '<span class="count">3</span>' : ""}</a>`,
  )
  .join("");
$("#settings-link").innerHTML = icon("settings") + "Settings";
$("#search-icon").innerHTML = icon("search");
$("#menu-toggle").innerHTML = icon("menu");
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 2500);
}
function spark(n = 0) {
  return `<svg class="sparkline" viewBox="0 0 100 30" aria-hidden="true"><path d="${["M1 24 10 21 18 23 27 15 37 18 45 10 54 14 63 9 73 12 82 4 91 7 99 3", "M1 24 12 24 21 20 32 21 41 13 53 16 63 8 75 10 85 4 99 5", "M1 12 12 14 24 10 36 11 45 9 54 12 64 8 73 10 83 6 99 8"][n]}"/></svg>`;
}
function chart() {
  return `<div class="chart-wrap"><svg class="volume-chart" viewBox="0 0 620 170" preserveAspectRatio="none" role="img" aria-label="Sample session volume over 24 hours. Volume rises through the day with a peak of 24 sessions."><defs><linearGradient id="volume-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#b6c2d3" stop-opacity=".22"/><stop offset="100%" stop-color="#b6c2d3" stop-opacity=".015"/></linearGradient></defs>${[25, 65, 105, 145].map((y, i) => `<line class="grid" x1="28" x2="614" y1="${y}" y2="${y}"/><text x="6" y="${y + 3}">${[30, 20, 10, 0][i]}</text>`).join("")}<path d="M28 127 C48 123 64 131 85 119 S115 128 139 119 S170 124 193 102 S222 120 246 90 S277 103 298 78 S325 92 349 61 S375 87 400 50 S426 62 447 44 S474 71 494 40 S522 50 546 29 S576 50 596 35 L614 44 L614 145 L28 145Z" fill="url(#volume-fill)"/><path d="M28 135 C80 128 110 140 150 130 S205 136 250 116 S302 126 350 110 S400 122 450 99 S510 117 550 95 S590 106 614 89" fill="none" stroke="#c1cad7" stroke-width="1.3" stroke-dasharray="3 4"/><path d="M28 127 C48 123 64 131 85 119 S115 128 139 119 S170 124 193 102 S222 120 246 90 S277 103 298 78 S325 92 349 61 S375 87 400 50 S426 62 447 44 S474 71 494 40 S522 50 546 29 S576 50 596 35 L614 44" fill="none" stroke="#7488a3" stroke-width="1.8"/>${[28, 170, 314, 462, 610].map((x, i) => `<text x="${x}" y="165" text-anchor="${i === 4 ? "end" : "start"}">${["00:00", "06:00", "12:00", "18:00", "Now"][i]}</text>`).join("")}</svg></div>`;
}
function sessionTable(list, extra = false) {
  return `<div class="card table-wrap"><table class="table"><thead><tr><th>Session</th><th>Agent</th><th>Status</th><th>Duration</th>${extra ? "<th>Tokens</th>" : ""}<th>Started</th><th></th></tr></thead><tbody>${list.map((s) => `<tr data-session="${s.id}" tabindex="0" role="link" aria-label="Open ${esc(s.title)}"><td><span class="row-title"><span class="row-icon">${icon(agentFor(s).icon)}</span>${esc(s.title)}</span></td><td>${esc(agentFor(s).name)}</td><td>${status(s.status)}</td><td class="mono">${s.duration}</td>${extra ? `<td class="mono">${s.tokens}</td>` : ""}<td>${s.started}</td><td class="row-arrow">${icon("chevron")}</td></tr>`).join("") || '<tr><td colspan="7" class="empty">No sessions match your search.</td></tr>'}</tbody></table>${extra ? `<div class="table-tail"><span>${list.length} sessions</span><span>Showing recent activity</span></div>` : ""}</div>`;
}
function overview() {
  const waiting = sessions.find((s) => s.status === "waiting");
  return (
    title(
      "Overview",
      "",
      btn("Last 24 hours", "period", false, "clock") +
        btn("New session", "new-session", true, "plus"),
    ) +
    `<div class="stats"><div class="stat"><div class="stat-label">Active sessions ${icon("activity")}</div><div class="stat-main"><span class="stat-value">${sessions.filter((s) => ["running", "waiting"].includes(s.status)).length}</span>${spark()}</div><div class="stat-note">Across <strong>3 agents</strong></div></div><div class="stat"><div class="stat-label">Finished runs ${icon("check")}</div><div class="stat-main"><span class="stat-value">142</span>${spark(1)}</div><div class="stat-note"><strong>↗ 18%</strong> vs. previous day</div></div><div class="stat"><div class="stat-label">Success rate ${icon("shield")}</div><div class="stat-main"><span class="stat-value">97.8<span style="font-size:20px;color:#778394">%</span></span>${spark(2)}</div><div class="stat-note">139 of 142 completed successfully</div></div></div><div class="overview-grid"><section class="card"><div class="card-head"><h2>Session volume</h2><div class="legend"><span><i></i>Today</span><span><i class="light"></i>Previous day</span></div></div>${chart()}<div class="chart-caption"><span><strong>2m 34s</strong> median duration</span><a class="text-link" href="#activity">View activity ${icon("arrow")}</a></div></section><section class="card attention"><div class="attention-title"><h2>Needs attention</h2><span class="mini-count">${waiting ? 1 : 0}</span></div><div class="attention-symbol">${icon(waiting ? "shield" : "check")}</div><div><h3>${waiting ? "Ready to publish" : "You’re all caught up"}</h3><p>${waiting ? "Game builder is waiting for approval to publish the space shooter." : "There are no pending decisions in this preview."}</p></div>${waiting ? `<button class="button" data-session="${waiting.id}">Review request ${icon("arrow")}</button>` : ""}<div class="capacity-mini"><div class="capacity-label"><span>Worker capacity</span><b>3 / ${workers * 2} slots</b></div><div class="capacity-track">${Array.from({ length: Math.max(4, workers * 2) }, (_, i) => `<i class="${i < 3 ? "used" : ""}"></i>`).join("")}</div></div></section></div><section><div class="section-heading"><h2>Recent sessions</h2><a class="text-link" href="#sessions">All sessions ${icon("arrow")}</a></div>${sessionTable(sessions.slice(0, 5))}</section>`
  );
}
function agentsPage() {
  return (
    title(
      "Agents",
      `${agents.length} agents in this workspace`,
      btn("New agent", "new-agent", true, "plus"),
    ) +
    `<div class="agent-grid">${agents.map((a) => `<article class="card agent-card" data-agent="${a.id}" tabindex="0" role="button" aria-label="Configure ${esc(a.name)}"><div class="agent-card-top"><span class="row-icon">${icon(a.icon)}</span><span class="tag">${a.version}</span></div><h2>${esc(a.name)}</h2><p>${esc(a.description)}</p><div class="agent-card-meta"><span class="tag">${esc(a.model)}</span><span class="tag">${a.tools} tools</span></div><div class="agent-card-foot"><span>${a.runs} sessions today</span>${a.version === "Draft" ? '<span class="tag">Draft</span>' : status("completed").replace("Completed", "Active")}</div></article>`).join("")}</div>`
  );
}
function sessionsPage() {
  return (
    title("Sessions", "", btn("New session", "new-session", true, "plus")) +
    `<div class="toolbar"><div class="filter-tabs" role="group" aria-label="Session status">${["All", "Running", "Waiting", "Completed"].map((f) => `<button data-filter="${f}" class="${filter === f ? "active" : ""}" aria-pressed="${filter === f}">${f}</button>`).join("")}</div><label class="input-search">${icon("search")}<input id="session-search" type="search" placeholder="Search sessions…" aria-label="Search sessions" value="${esc(query)}"></label></div><div id="session-table">${filteredTable()}</div>`
  );
}
function filteredTable() {
  return sessionTable(
    sessions.filter(
      (s) =>
        (filter === "All" || s.status === filter.toLowerCase()) &&
        `${s.title} ${agentFor(s).name}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    ),
    true,
  );
}
function step(title, time, ic, body, open = false) {
  return `<div class="trace-step"><span class="step-dot">${icon("check")}</span><details ${open ? "open" : ""}><summary>${icon(ic)}<span>${title}</span><span class="step-time">${time}</span>${icon("chevron", "chevron")}</summary><div class="trace-body">${body}</div></details></div>`;
}
function trace(s) {
  if (!s.modelCalls)
    return `<div class="timeline">${step("Session created", "0s", "sessions", `<p>${esc(s.title)}</p>`, true)}<p class="small-note">Design preview — no agent has been started.</p></div>`;
  const game = s.agent === "builder";
  return `<div class="timeline">${step("Request received", "0.0s", "sessions", `<p>${game ? "Build a space shooter with neon visuals, keyboard controls, and a score counter." : esc(s.title) + ". Check the implementation and tests, then summarize what you find."}</p>`, true)}${step("Plan created", "1.2s", "layers", `<p>${game ? "Create the game loop, add controls and scoring, then verify the playable result." : "Inspect the changes, check related tests, and review failure paths."}</p>`)}${step(
    game ? "Read project files" : "Read pull request",
    "0.4s",
    "file",
    `<pre>read_file
workspace: /sandbox/project
status: completed</pre>`,
  )}${step(
    game ? "Generate game files" : "Review implementation",
    game ? "42.8s" : "18.6s",
    "bolt",
    `<p>${game ? "Created the canvas scene, player controls, and scoring logic." : "Compared the updated behavior against the surrounding code and test cases."}</p><pre>model call · ${esc(agentFor(s).model)}
input: 4,280 tokens
output: 1,620 tokens</pre>`,
  )}${step("Run checks", "3.1s", "terminal", `<pre>${game ? "✓ Keyboard controls respond correctly\n✓ Score updates after a hit\n✓ Restart resets the game state" : "✓ 18 tests passed\n✓ No type errors\n✓ Retry behavior verified"}</pre>`, true)}${s.status === "waiting" ? `<div class="approval-box"><h3>Publish the game?</h3><p>The generated files are ready. Publishing makes this version available in the game library.</p><div class="approval-actions">${btn("Approve once", "approve:" + s.id, true, "check")}${btn("Deny", "deny:" + s.id)}</div><details><summary>Permission details</summary><p><span class="mono">game.publish</span> · Space shooter<br>This approval applies to this action only.</p>${btn("Allow for this session", "always:" + s.id)}</details></div>` : step(s.status === "cancelled" ? "Run cancelled" : s.status === "completed" ? "Result saved" : "Working on the final summary", s.status === "completed" ? "0.2s" : "In progress", s.status === "completed" ? "check" : "activity", `<p>${s.status === "cancelled" ? "The run was stopped. Existing work remains available." : s.status === "completed" ? "The result and execution record are available in this session." : "The agent is preparing its summary from the tool results."}</p>`)}<div class="trace-step"><span class="step-dot">${icon("more")}</span><span class="small-note">${s.status === "waiting" ? "Waiting for your decision" : s.status === "cancelled" ? "Run cancelled" : s.status === "completed" ? "Session completed" : "Agent is working"}</span></div></div>`;
}
function sessionPage(s) {
  const a = agentFor(s);
  return `<div class="detail-heading"><a class="back" href="#sessions">${icon("back")}Sessions</a><div class="session-title-row"><h1>${esc(s.title)}</h1><div class="heading-actions">${["running", "waiting"].includes(s.status) ? btn("Cancel", "cancel:" + s.id, false) : btn("New session", "new-session", false, "plus")}</div></div><div class="session-meta">${status(s.status)}<span class="sep">·</span><span>${esc(a.name)}</span><span class="sep">·</span><span>${s.started}</span><span class="sep">·</span><span class="mono">${s.id}</span><button class="icon-button" aria-label="Copy session ID" data-action="copy:${s.id}">${icon("copy")}</button></div><div class="tabs" role="tablist" aria-label="Session details">${["Trace", "Result", "Events"].map((t) => `<button class="tab ${sessionTab === t ? "active" : ""}" role="tab" aria-selected="${sessionTab === t}" data-session-tab="${t}">${t}</button>`).join("")}</div></div><div class="detail-layout"><section>${
    !s.modelCalls && sessionTab !== "Trace"
      ? '<div class="blank-activity">No execution data yet.</div>'
      : sessionTab === "Trace"
        ? trace(s)
        : sessionTab === "Result"
          ? `<div class="session-result"><h3>${s.status === "waiting" ? "Ready for review" : s.status === "running" ? "Work in progress" : "Result"}</h3><p>${s.agent === "builder" ? "A playable game with keyboard controls, a live score counter, and a restart flow. The generated files passed the checks shown in the trace." : "The implementation and related tests have been reviewed. The summary is grounded in the recorded tool results."}</p><ul><li>${s.agent === "builder" ? "index.html" : "Review summary"}</li><li>${s.agent === "builder" ? "game.js" : "Test results"}</li></ul>${s.status === "waiting" ? btn("Review approval", "trace-tab", false, "shield") : ""}</div>`
          : `<div>${[
              ["14:32:04", "Session created"],
              ["14:32:04", "Run admitted"],
              ["14:32:05", "Model response received"],
              ["14:32:06", "Tool call started"],
              ["14:32:07", "Tool call completed"],
              ["14:32:49", "Checks completed"],
              [
                "14:32:50",
                s.status === "waiting"
                  ? "Approval requested"
                  : "Result updated",
              ],
            ]
              .map(
                ([t, e]) =>
                  `<div class="event-row"><time>${t}</time><span>${e}</span></div>`,
              )
              .join("")}</div>`
  }</section><aside class="detail-side"><div><h3>Session</h3><div class="property"><small>Agent</small><div>${esc(a.name)} <span class="tag">${a.version}</span></div></div><div class="property"><small>Model</small><div>${esc(a.model)}</div></div><div class="property"><small>Runtime profile</small><div>${a.profile}</div></div><div class="property"><small>Workspace</small><div class="mono">${s.agent === "builder" ? "space-shooter" : "project-review"}</div></div></div><div><hr><h3>Usage</h3><div class="inline-stat-row"><span>Duration</span><b>${s.duration}</b></div><div class="inline-stat-row"><span>Tokens</span><b>${s.tokens}</b></div><div class="inline-stat-row"><span>Model calls</span><b>${s.modelCalls}</b></div><div class="inline-stat-row"><span>Tool calls</span><b>${s.toolCalls}</b></div><hr><h3>Execution</h3><div class="property"><small>Environment</small><div>NVIDIA OpenShell</div></div><div class="property"><small>Worker</small><div class="mono">worker-02</div></div></div></aside></div>`;
}
function activityPage() {
  return (
    title("Activity", "", btn("Last 24 hours", "period", false, "clock")) +
    `<div class="metrics-row"><div class="metric-inline"><small>Model calls</small><b>684</b></div><div class="metric-inline"><small>Tool calls</small><b>1,208</b></div><div class="metric-inline"><small>Tokens processed</small><b>1.42m</b></div><div class="metric-inline"><small>Median run</small><b>2m 34s</b></div></div><div class="activity-grid"><section class="card"><div class="card-head"><h2>Activity over time</h2><div class="legend"><span><i></i>Runs</span><span><i class="light"></i>Previous day</span></div></div>${chart()}<div class="chart-caption"><span>15-minute intervals</span><span>24 hours</span></div></section><section class="card distribution"><h2>Usage by provider</h2>${[
      ["OpenAI", 63, "894k tokens"],
      ["Anthropic", 28, "398k tokens"],
      ["Local", 9, "128k tokens"],
    ]
      .map(
        ([n, v, t]) =>
          `<div class="distribution-row"><span>${n} <small>· ${t}</small></span><span>${v}%</span></div><div class="distribution-track"><i style="width:${v}%"></i></div>`,
      )
      .join(
        "",
      )}</section></div><section><div class="section-heading"><h2>Recent traces</h2><button class="text-link" data-action="integrations">Export destinations ${icon("external")}</button></div><div class="card table-wrap"><table class="table"><thead><tr><th>Run</th><th>Agent</th><th>Duration</th><th>Model / tools</th><th>Status</th><th></th></tr></thead><tbody>${sessions
      .slice(0, 5)
      .map(
        (s, i) =>
          `<tr data-session="${s.id}" tabindex="0" role="link" aria-label="Trace ${esc(s.title)}"><td>${esc(s.title)}<span class="cell-subtitle mono">run_${s.id.slice(4)}</span></td><td>${esc(agentFor(s).name)}</td><td>${s.duration}<span class="row-bar"><i style="width:${[43, 78, 25, 58, 92][i]}%"></i></span></td><td><span class="mono">${s.modelCalls} / ${s.toolCalls}</span></td><td>${status(s.status)}</td><td class="row-arrow">${icon("chevron")}</td></tr>`,
      )
      .join("")}</tbody></table></div></section>`
  );
}
function infrastructure() {
  return (
    title(
      "Infrastructure",
      "",
      btn("Configuration", "settings-route", false, "settings"),
    ) +
    `<div class="pool-layout"><section class="card pool-card"><div class="pool-top"><div class="row-title"><span class="row-icon">${icon("infra")}</span><div><h2>Default pool</h2><span class="header-subtitle">Manual scaling</span></div></div><span class="status"><i></i>${workers ? "Ready" : "Scaled to zero"}</span></div><div class="node-grid">${workers ? Array.from({ length: workers }, (_, i) => `<div class="node">${icon("infra")}<span>worker-0${i + 1}</span><span class="node-slot"><i></i><i class="${i ? "free" : ""}"></i></span></div>`).join("") : '<p class="small-note">Sessions stay queued until a worker is available.</p>'}</div><div class="pool-footer"><span class="small-note">${workers * 2} slots · ${workers} workers</span><div class="heading-actions"><div class="stepper"><button data-action="workers-minus" aria-label="Decrease workers">−</button><span id="worker-count">${pendingWorkers}</span><button data-action="workers-plus" aria-label="Increase workers">+</button></div><button class="button" id="scale-button" data-action="scale" ${pendingWorkers === workers ? "disabled" : ""}>Apply</button></div></div></section><section class="card pool-card"><h2>Environment</h2><div class="property" style="margin-top:24px"><small>Execution provider</small><div>NVIDIA OpenShell</div></div><div class="inline-stat-row"><span>Active environments</span><b>3</b></div><div class="inline-stat-row"><span>Shared workspaces</span><b>1</b></div><hr class="divider"><button class="text-link" data-action="environment">View environment details ${icon("arrow")}</button></section></div><section><div class="section-heading"><h2>Runtime profiles</h2>${btn("New profile", "new-profile", false, "plus")}</div><div class="card table-wrap"><table class="table"><thead><tr><th>Profile</th><th>Provider</th><th>Resources</th><th>Approvals</th><th></th></tr></thead><tbody>${[
      ["Standard", "1 CPU · 512 MB", "Ask"],
      ["Autonomous", "1 CPU · 1 GB", "Bypass"],
    ]
      .map(
        ([n, r, a]) =>
          `<tr><td><span class="row-title"><span class="row-icon">${icon("layers")}</span>${n}</span></td><td>OpenShell</td><td>${r}</td><td>${a}</td><td><button class="icon-button" data-action="profile:${n}" aria-label="Edit ${n} profile">${icon("chevron")}</button></td></tr>`,
      )
      .join("")}</tbody></table></div></section>`
  );
}
function settings() {
  return (
    title("Settings") +
    `<div class="tabs" role="tablist" aria-label="Settings">${["Defaults", "Access", "Integrations"].map((t) => `<button class="tab ${settingsTab === t ? "active" : ""}" role="tab" aria-selected="${settingsTab === t}" data-settings-tab="${t}">${t}</button>`).join("")}</div><div class="settings-layout">${
      settingsTab === "Defaults"
        ? `<section class="settings-section"><h2>Session defaults</h2><div class="setting-row"><div><label for="profile-default">Runtime profile</label><p>Used when a session doesn’t specify one.</p></div><select id="profile-default"><option>Standard</option><option>Autonomous</option></select></div><div class="setting-row"><div><label for="compaction">Compact context at</label><p>New sessions inherit this setting.</p></div><select id="compaction"><option>80% of context</option><option>70% of context</option><option>60% of context</option></select></div></section><section class="settings-section"><h2>Admissions</h2><div class="setting-row"><div><label id="pause-label">Pause new tasks</label><p>Running tasks and pending decisions remain available.</p></div><button class="switch" role="switch" aria-labelledby="pause-label" aria-checked="${paused}" data-action="toggle-pause"><span></span></button></div><details style="margin-top:20px"><summary class="small-note" style="cursor:pointer">Limits</summary><div class="setting-row"><label for="queue-limit">Queued tasks per session</label><input id="queue-limit" type="number" value="100" min="1" max="100"></div><div class="setting-row"><label for="env-limit">Environments per principal</label><input id="env-limit" type="number" value="16" min="1" max="16"></div></details></section>${btn("Save changes", "save-settings", true)}`
        : settingsTab === "Access"
          ? `<div class="section-heading"><h2>Service tokens</h2>${btn("Create token", "new-token", true, "plus")}</div><div class="card table-wrap"><table class="table"><thead><tr><th>Name</th><th>Role</th><th>Expires</th><th></th></tr></thead><tbody><tr><td>Product backend</td><td>Runner</td><td>In 6 days</td><td>${btn("Details", "token-details", false)}</td></tr><tr><td>Release workflow</td><td>Publisher</td><td>In 23 hours</td><td>${btn("Details", "token-details", false)}</td></tr></tbody></table></div>`
          : `${[
              ["G", "Grafana", "Infrastructure metrics and dashboards."],
              ["L", "Langfuse", "Model calls and agent traces."],
            ]
              .map(
                ([l, n, d]) =>
                  `<div class="integration-card"><div class="integration-mark">${l}</div><div><h3>${n}</h3><p>${d}</p></div>${btn("Configure", "integration:" + n, false)}</div>`,
              )
              .join(
                "",
              )}<p class="small-note" style="margin-top:20px">Export connections are not configured in this preview.</p>`
    }</div>`
  );
}
function render() {
  const [route, id] = location.hash.slice(1).split("/");
  const page =
    navItems.some(([n]) => n === route) || route === "settings"
      ? route
      : "overview";
  document.querySelectorAll("[data-nav]").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === page);
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("#settings-link").classList.toggle("active", page === "settings");
  $("#crumb").textContent =
    page === "infrastructure"
      ? "Infrastructure"
      : page[0].toUpperCase() + page.slice(1);
  const s = sessions.find((s) => s.id === id);
  main.innerHTML =
    page === "overview"
      ? overview()
      : page === "agents"
        ? agentsPage()
        : page === "sessions"
          ? s
            ? sessionPage(s)
            : sessionsPage()
          : page === "activity"
            ? activityPage()
            : page === "infrastructure"
              ? infrastructure()
              : settings();
  document.title = `${s ? s.title : $("#crumb").textContent} · OpenWorker`;
  $("#sidebar").classList.remove("open");
}
function openPanel(name, body, footer = "") {
  $("#panel-content").innerHTML =
    `<div class="drawer-header"><h2 id="panel-title">${name}</h2><button class="icon-button" data-close="panel" aria-label="Close panel">${icon("close")}</button></div><div class="drawer-body">${body}</div>${footer ? `<div class="drawer-footer">${footer}</div>` : ""}`;
  panel.setAttribute("aria-labelledby", "panel-title");
  panel.showModal();
}
function openModal(name, body, footer = "") {
  $("#modal-content").innerHTML =
    `<div class="modal-header"><h2 id="modal-title">${name}</h2><button class="icon-button" data-close="modal" aria-label="Close dialog">${icon("close")}</button></div><div class="modal-body">${body}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ""}`;
  modal.setAttribute("aria-labelledby", "modal-title");
  modal.showModal();
}
const field = (label, id, value = "", type = "input") =>
  `<div class="field"><label for="${id}">${label}</label>${type === "textarea" ? `<textarea id="${id}">${esc(value)}</textarea>` : `<input id="${id}" value="${esc(value)}" autocomplete="off">`}</div>`;
const select = (label, id, options) =>
  `<div class="field"><label for="${id}">${label}</label><select id="${id}">${options.map((o) => `<option>${esc(o)}</option>`).join("")}</select></div>`;
function agentPanel(id) {
  const a = agents.find((a) => a.id === id);
  openPanel(
    esc(a.name),
    `<div class="property"><small>Current release</small><div>${a.version} <span class="tag">Active</span></div></div>${field("Instructions", "agent-instructions", a.instructions, "textarea")}${select("Default model", "agent-model", [a.model, ...["OpenAI · Sol", "Anthropic · Sonnet", "Local · Qwen"].filter((x) => x !== a.model)])}${select("Runtime profile", "agent-profile", [a.profile, a.profile === "Standard" ? "Autonomous" : "Standard"])}<details><summary>Tools and release details</summary><div class="small-note">${["read_file", "write_file", "run_shell"].map((t) => `<span class="tag mono" style="display:inline-block;margin:4px">${t}</span>`).join("")}</div><div class="property" style="margin-top:20px"><small>Release ID</small><div class="mono">${a.id}@${a.version.slice(1)}</div></div></details>`,
    btn("Close", "close-panel") + btn("Save draft", "save-agent:" + id, true),
  );
}
function newSession() {
  openPanel(
    "New session",
    select(
      "Agent",
      "new-session-agent",
      agents.map((a) => a.name),
    ) +
      field("Task", "new-session-task", "", "textarea") +
      `<details><summary>Session options</summary>${select("Runtime profile", "new-session-profile", ["Agent default", "Standard", "Autonomous"])}${select("Model", "new-session-model", ["Agent default", "OpenAI · Sol", "Anthropic · Sonnet", "Local · Qwen"])}</details>`,
    btn("Cancel", "close-panel") +
      btn("Start session", "start-session", true, "arrow"),
  );
  setTimeout(() => $("#new-session-task").focus(), 40);
}
function search() {
  $("#modal-content").innerHTML =
    `<div class="search-modal-input">${icon("search")}<input id="global-search" type="search" placeholder="Search sessions and agents…" aria-label="Search workspace"><button class="icon-button" data-close="modal" aria-label="Close search">${icon("close")}</button></div><div class="search-results" id="search-results"></div>`;
  modal.removeAttribute("aria-labelledby");
  modal.setAttribute("aria-label", "Search workspace");
  modal.showModal();
  searchResults("");
  setTimeout(() => $("#global-search").focus(), 30);
}
function searchResults(q) {
  const matches = sessions.filter((s) =>
    s.title.toLowerCase().includes(q.toLowerCase()),
  );
  const aa = agents.filter((a) =>
    a.name.toLowerCase().includes(q.toLowerCase()),
  );
  $("#search-results").innerHTML =
    matches
      .map(
        (s) =>
          `<button class="search-result" data-session="${s.id}">${icon(agentFor(s).icon)}<span>${esc(s.title)}<small>Session · ${esc(agentFor(s).name)}</small></span></button>`,
      )
      .join("") +
      aa
        .map(
          (a) =>
            `<button class="search-result" data-agent="${a.id}">${icon(a.icon)}<span>${esc(a.name)}<small>Agent</small></span></button>`,
        )
        .join("") || '<div class="empty">No results.</div>';
}
function confirmAction(kind, id) {
  const s = sessions.find((x) => x.id === id);
  openModal(
    kind === "cancel" ? "Cancel this run?" : "Deny this action?",
    `<p>${esc(s.title)}</p><p>${kind === "cancel" ? "The session and its existing work will remain available." : "The agent will receive your decision and can continue from there."}</p>`,
    btn("Keep working", "close-modal") +
      btn(
        kind === "cancel" ? "Cancel run" : "Deny action",
        `confirm-${kind}:${id}`,
        true,
      ),
  );
}
document.addEventListener("click", async (e) => {
  const link = e.target.closest("[data-session]");
  if (link) {
    sessionTab = "Trace";
    if (modal.open) modal.close();
    location.hash = "sessions/" + link.dataset.session;
    return;
  }
  const a = e.target.closest("[data-agent]");
  if (a) {
    if (modal.open) modal.close();
    agentPanel(a.dataset.agent);
    return;
  }
  const close = e.target.closest("[data-close]");
  if (close) {
    document.getElementById(close.dataset.close).close();
    return;
  }
  const f = e.target.closest("[data-filter]");
  if (f) {
    filter = f.dataset.filter;
    render();
    return;
  }
  const st = e.target.closest("[data-session-tab]");
  if (st) {
    sessionTab = st.dataset.sessionTab;
    render();
    return;
  }
  const settingsButton = e.target.closest("[data-settings-tab]");
  if (settingsButton) {
    settingsTab = settingsButton.dataset.settingsTab;
    render();
    return;
  }
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  const [kind, id] = action.split(":");
  if (kind === "new-session") newSession();
  else if (kind === "new-agent")
    openPanel(
      "New agent",
      field("Name", "new-agent-name") +
        field("Instructions", "new-agent-instructions", "", "textarea") +
        select("Default model", "new-agent-model", [
          "OpenAI · Sol",
          "Anthropic · Sonnet",
          "Local · Qwen",
        ]),
      btn("Cancel", "close-panel") + btn("Create draft", "create-agent", true),
    );
  else if (kind === "create-agent") {
    const name = $("#new-agent-name").value.trim();
    if (!name) {
      $("#new-agent-name").focus();
      return;
    }
    agents.push({
      id: "agent-" + Date.now(),
      name,
      icon: "agents",
      description: "A new agent for your workspace.",
      model: $("#new-agent-model").value,
      version: "Draft",
      runs: 0,
      tools: 3,
      profile: "Standard",
      instructions: $("#new-agent-instructions").value,
    });
    panel.close();
    location.hash = "agents";
    render();
    toast("Draft added to the preview");
  } else if (kind === "start-session") {
    const task = $("#new-session-task").value.trim();
    if (!task) {
      $("#new-session-task").focus();
      return;
    }
    const agent = agents.find((a) => a.name === $("#new-session-agent").value);
    const s = {
      id: "ses_" + Date.now().toString(16),
      title: task.length > 60 ? task.slice(0, 57) + "…" : task,
      agent: agent.id,
      status: "running",
      duration: "0s",
      started: "Just now",
      tokens: "0",
      modelCalls: 0,
      toolCalls: 0,
    };
    sessions.unshift(s);
    panel.close();
    sessionTab = "Trace";
    location.hash = "sessions/" + s.id;
    toast("Preview session created — no agent was started");
  } else if (kind === "approve" || kind === "always") {
    sessions.find((s) => s.id === id).status = "completed";
    render();
    toast(
      kind === "always"
        ? "Session permission updated in the preview"
        : "Approval recorded in the preview",
    );
  } else if (kind === "cancel" || kind === "deny") confirmAction(kind, id);
  else if (kind === "confirm-cancel" || kind === "confirm-deny") {
    sessions.find((s) => s.id === id).status =
      kind === "confirm-cancel" ? "cancelled" : "running";
    modal.close();
    render();
    toast("Preview updated");
  } else if (kind === "close-panel") panel.close();
  else if (kind === "close-modal") modal.close();
  else if (kind === "save-agent") {
    const a = agents.find((a) => a.id === id);
    a.instructions = $("#agent-instructions").value;
    a.model = $("#agent-model").value;
    a.profile = $("#agent-profile").value;
    panel.close();
    render();
    toast("Draft saved in the preview");
  } else if (kind === "copy") {
    try {
      await navigator.clipboard.writeText(id);
      toast("Session ID copied");
    } catch {
      toast(id);
    }
  } else if (kind === "trace-tab") {
    sessionTab = "Trace";
    render();
  } else if (kind === "workers-plus" || kind === "workers-minus") {
    pendingWorkers = Math.max(
      0,
      Math.min(4, pendingWorkers + (kind === "workers-plus" ? 1 : -1)),
    );
    $("#worker-count").textContent = pendingWorkers;
    $("#scale-button").disabled = pendingWorkers === workers;
  } else if (kind === "scale")
    openModal(
      "Update worker capacity?",
      `<p>Scale the default pool from ${workers} to ${pendingWorkers} workers.</p><p>${pendingWorkers < workers ? "Workers finish or park active work before stopping." : "Queued work can use the additional capacity when workers are ready."}</p>`,
      btn("Cancel", "close-modal") + btn("Apply", "confirm-scale", true),
    );
  else if (kind === "confirm-scale") {
    workers = pendingWorkers;
    modal.close();
    render();
    toast("Capacity updated in the preview");
  } else if (kind === "settings-route") {
    settingsTab = "Defaults";
    location.hash = "settings";
  } else if (kind === "integrations") {
    settingsTab = "Integrations";
    location.hash = "settings";
  } else if (kind === "toggle-pause") {
    paused = !paused;
    e.target.closest("button").setAttribute("aria-checked", paused);
  } else if (kind === "save-settings") toast("Settings saved in the preview");
  else if (kind === "profile" || kind === "new-profile")
    openPanel(
      kind === "profile" ? id + " profile" : "New runtime profile",
      field("Name", "profile-name", id || "") +
        select("Provider", "profile-provider", ["NVIDIA OpenShell"]) +
        select("Resources", "profile-resources", [
          "1 CPU · 512 MB",
          "1 CPU · 1 GB",
          "2 CPU · 2 GB",
        ]) +
        select("Approvals", "profile-approvals", [
          "Ask",
          "Auto-approve",
          "Bypass",
        ]) +
        `<details><summary>Access policy</summary><div class="property"><small>Workspace</small><div class="mono">/sandbox</div></div><div class="property"><small>Network</small><div>Denied by default</div></div></details>`,
      btn("Close", "close-panel") + btn("Save draft", "save-profile", true),
    );
  else if (kind === "save-profile") {
    panel.close();
    toast("Profile draft saved in the preview");
  } else if (kind === "new-token")
    openPanel(
      "Create service token",
      field("Name", "token-name") +
        select("Role", "token-role", [
          "Runner",
          "Observer",
          "Publisher",
          "Release administrator",
          "Service administrator",
        ]) +
        select("Expires in", "token-expiry", ["1 hour", "1 day", "7 days"]) +
        `<details><summary>Scope</summary><p class="small-note">Runner tokens are limited to their own sessions. Management permissions remain separate from agent control.</p></details>`,
      btn("Cancel", "close-panel") + btn("Create token", "preview-token", true),
    );
  else if (kind === "preview-token") {
    panel.close();
    toast("Design preview — no credential was generated");
  } else if (kind === "token-details")
    openPanel(
      "Product backend",
      `<div class="property"><small>Role</small><div>Runner</div></div><div class="property"><small>Scope</small><div>Owned sessions</div></div><div class="property"><small>Expires</small><div>In 6 days</div></div><details><summary>Credential ID</summary><span class="mono">key_92c1…8f3b</span></details>`,
      btn("Close", "close-panel"),
    );
  else if (kind === "integration")
    openPanel(
      id,
      field("Destination URL", "integration-url", "https://") +
        `<p class="small-note">This is a design placeholder. No data is sent from this preview.</p>`,
      btn("Close", "close-panel") + btn("Save", "save-integration", true),
    );
  else if (kind === "save-integration") {
    panel.close();
    toast("Destination saved in the preview");
  } else if (kind === "environment")
    openPanel(
      "Execution environment",
      `<div class="property"><small>Provider</small><div>NVIDIA OpenShell</div></div><div class="property"><small>Workspace</small><div class="mono">/sandbox</div></div><div class="property"><small>Sharing</small><div>Coder and verifier</div></div><details><summary>Provider identity</summary><div class="mono">env_28a4…9c11<br>Development / default</div></details>`,
      btn("Close", "close-panel"),
    );
  else if (kind === "period")
    openModal(
      "Time range",
      select("Range", "time-range", [
        "Last 24 hours",
        "Last 7 days",
        "Last 30 days",
      ]) + "<p>Charts use sample data in this preview.</p>",
      btn("Apply", "apply-period", true),
    );
  else if (kind === "apply-period") {
    modal.close();
    toast("Sample charts remain on the 24-hour view");
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "session-search") {
    query = e.target.value;
    $("#session-table").innerHTML = filteredTable();
  }
  if (e.target.id === "global-search") searchResults(e.target.value);
});
document.addEventListener("keydown", (e) => {
  if (
    ["ArrowLeft", "ArrowRight"].includes(e.key) &&
    e.target.matches("[role=tab]")
  ) {
    e.preventDefault();
    const tabs = [...e.target.parentElement.querySelectorAll("[role=tab]")];
    const i =
      (tabs.indexOf(e.target) +
        (e.key === "ArrowRight" ? 1 : -1) +
        tabs.length) %
      tabs.length;
    const attr = tabs[i].hasAttribute("data-session-tab")
      ? "data-session-tab"
      : "data-settings-tab";
    const value = tabs[i].getAttribute(attr);
    tabs[i].click();
    document.querySelector(`[${attr}="${value}"]`).focus();
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (!modal.open) search();
  }
  if (e.key === "Enter" && e.target.matches("[data-session],[data-agent]"))
    e.target.click();
});
$("#search-trigger").addEventListener("click", search);
$("#menu-toggle").addEventListener("click", () =>
  $("#sidebar").classList.toggle("open"),
);
$("#profile-button").addEventListener("click", () => {
  settingsTab = "Access";
  location.hash = "settings";
  render();
});
$("#workspace").addEventListener("click", () =>
  openModal(
    "Development workspace",
    `<div class="property"><small>Service</small><div>OpenWorker Service</div></div><div class="property"><small>Access</small><div>Administrator preview</div></div><p>This mock uses sample data and makes no service requests.</p>`,
    btn("Done", "close-modal", true),
  ),
);
for (const d of [panel, modal])
  d.addEventListener("click", (e) => {
    if (e.target === d) {
      const r = d.getBoundingClientRect();
      if (
        e.clientX < r.left ||
        e.clientX > r.right ||
        e.clientY < r.top ||
        e.clientY > r.bottom
      )
        d.close();
    }
  });
window.addEventListener("hashchange", () => {
  render();
  window.scrollTo(0, 0);
});
render();
