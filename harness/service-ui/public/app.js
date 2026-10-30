import { mountObservability } from "./observability.js";
let disposeObservability = null;
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
const $ = (s) => document.querySelector(s),
  main = $("#main"),
  panel = $("#panel"),
  modal = $("#modal");
let expiryTimer;
let auth = null,
  version = 0,
  stream = null,
  reconnect = null,
  refreshTimer = null,
  settingsTab = "Defaults",
  sessionTab = "Trace";
let sessions = [],
  nextAfter = null,
  releases = [],
  profiles = [],
  pools = [],
  snapshot = null,
  events = [],
  eventCursor = 0,
  pending = null,
  formHandler = null;
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const role = () => auth?.identity.role;
const can = {
  run: () => ["administrator", "operator"].includes(role()),
  publish: () => ["administrator", "publisher"].includes(role()),
  activate: () => ["administrator", "release_administrator"].includes(role()),
  configure: () =>
    [
      "administrator",
      "release_administrator",
      "service_administrator",
    ].includes(role()),
  admin: () => ["administrator", "service_administrator"].includes(role()),
};
const human = (s) => String(s || "idle").replaceAll("_", " ");
const pretty = (v) => JSON.stringify(v, null, 2);
const btn = (text, action, primary = false, ic = "") =>
  `<button class="button ${primary ? "primary" : ""}" data-action="${action}">${ic ? icon(ic) : ""}${text}</button>`;
const status = (s) =>
  `<span class="status ${["completed", "waiting", "running", "failed", "cancelled"].includes(s) ? s : ""}"><i></i>${esc(human(s))}</span>`;
const head = (title, sub = "", actions = "") =>
  `<div class="page-heading"><div><h1>${esc(title)}</h1>${sub ? `<p>${esc(sub)}</p>` : ""}</div><div class="heading-actions">${actions}${btn("Refresh", "refresh", false)}</div></div>`;
const empty = (text) => `<div class="empty">${esc(text)}</div>`;
const field = (name, id, value = "", type = "text") =>
  `<div class="field"><label for="${id}">${esc(name)}</label>${type === "textarea" ? `<textarea id="${id}" required>${esc(value)}</textarea>` : `<input id="${id}" type="${type}" value="${esc(value)}" required>`}</div>`;
const select = (name, id, options) =>
  `<div class="field"><label for="${id}">${esc(name)}</label><select id="${id}">${options.map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("")}</select></div>`;
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.add("show");
  setTimeout(() => $("#toast").classList.remove("show"), 4000);
}
function stop() {
  disposeObservability?.();
  disposeObservability = null;
  stream?.close();
  stream = null;
  clearTimeout(reconnect);
  clearTimeout(refreshTimer);
}
function loggedOut(message = "") {
  clearTimeout(expiryTimer);
  version++;
  stop();
  auth = null;
  settingsTab = "Defaults";
  sessionTab = "Trace";
  pending = null;
  sessions = [];
  events = [];
  snapshot = null;
  releases = [];
  profiles = [];
  pools = [];
  panel.close();
  modal.close();
  $("#panel-content").replaceChildren();
  $("#modal-content").replaceChildren();
  main.replaceChildren();
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#token").value = "";
  $("#login-error").textContent = message;
}
async function api(path, method = "GET", data, key, signal) {
  const r = await fetch("/api" + path, {
    method,
    signal,
    headers:
      method === "GET"
        ? {}
        : {
            "Content-Type": "application/json",
            "X-CSRF-Token": auth?.csrf || "",
            "Idempotency-Key": key,
          },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  let value;
  try {
    value = await r.json();
  } catch {
    value = {
      error: method === "GET" ? "service_unavailable" : "outcome_unknown",
    };
  }
  if (r.status === 401) {
    loggedOut("Your credential expired or was revoked. Sign in again.");
    throw Error("Sign in required");
  }
  if (!r.ok)
    throw Object.assign(Error(value.error || `Request failed (${r.status})`), {
      status: r.status,
      value,
    });
  return value;
}
function friendly(e) {
  return e.message === "outcome_unknown"
    ? "The response was lost. The action may have been accepted. Retry uses the same request identity."
    : e.status === 403
      ? "Your credential does not allow this action."
      : e.status === 409
        ? "The record changed. Refresh it before submitting another update."
        : human(e.message);
}
function drawer(title, body, handler, button = "Save") {
  if (pending) {
    toast("Resolve the pending request before starting another change.");
    return;
  }
  formHandler = handler;
  $("#panel-content").innerHTML =
    `<div class="drawer-header"><h2 id="panel-title">${esc(title)}</h2><button class="icon-button" data-close="panel" aria-label="Close panel">${icon("close")}</button></div><form id="edit-form"><div class="drawer-body">${body}<p id="form-error" role="alert"></p></div><div class="drawer-footer">${handler ? `<button class="button primary" id="save" type="submit">${esc(button)}</button>` : btn("Close", "close-panel")}</div></form>`;
  panel.setAttribute("aria-labelledby", "panel-title");
  panel.showModal();
}
// An uncertain write retains its exact payload and idempotency key in memory.
// Neither this UI nor its server repeats tool effects or retries mutations on its own.
async function mutate(path, method, body, done) {
  if (!pending)
    pending = { path, method, body, key: crypto.randomUUID(), done };
  const p = pending;
  const save = $("#save");
  if (save) {
    save.disabled = true;
    save.textContent = "Submitting…";
  }
  try {
    const result = await api(p.path, p.method, p.body, p.key);
    pending = null;
    panel.close();
    $("#panel-content").replaceChildren();
    await p.done(result);
  } catch (e) {
    if (!auth) return;
    const unknown = !e.status || e.status >= 500;
    if (!unknown) pending = null;
    $("#form-error")?.replaceChildren(
      document.createTextNode(
        friendly(e) + (unknown ? ` Idempotency key: ${p.key}` : ""),
      ),
    );
    if (unknown) {
      for (const el of panel.querySelectorAll("input,textarea,select"))
        el.disabled = true;
    }
    if (save) {
      save.disabled = false;
      save.textContent = unknown ? "Retry same request" : "Submit";
    }
  }
}
function jsonDrawer(title, definition, path, method = "POST", wrap = (x) => x) {
  drawer(
    title,
    field("Definition", "definition", pretty(definition), "textarea") +
      '<p class="small-note">Definitions are immutable. Use a new version ID for changes.</p>',
    async () => {
      let value;
      try {
        value = JSON.parse($("#definition").value);
      } catch {
        throw Error("Enter valid JSON");
      }
      await mutate(path, method, wrap(value), async () => {
        toast("Saved");
        await render();
      });
    },
    "Publish",
  );
}
function wireForm() {
  document.addEventListener("submit", async (e) => {
    if (e.target.id === "login-form") {
      e.preventDefault();
      const b = e.target.querySelector("button");
      b.disabled = true;
      const token = $("#token").value.trim();
      $("#token").value = "";
      try {
        const r = await fetch("/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        const data = await r.json();
        if (!r.ok) throw Error(human(data.error));
        auth = data;
        showApp();
      } catch (e) {
        $("#login-error").textContent = e.message;
      } finally {
        b.disabled = false;
      }
    }
    if (e.target.id === "edit-form") {
      e.preventDefault();
      try {
        if (pending) await mutate();
        else await formHandler?.();
      } catch (e) {
        $("#form-error").textContent = friendly(e);
      }
    }
  });
}
function showApp() {
  clearTimeout(expiryTimer);
  expiryTimer = setTimeout(
    () => loggedOut("Session expired. Sign in again."),
    Math.max(0, auth.expires - Date.now()),
  );
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#workspace-name").textContent = auth.capabilities.namespace;
  $("#principal-label").textContent = auth.identity.principal;
  render();
}
const nav = [
  ["overview", "Overview", "overview"],
  ["agents", "Agents", "agents"],
  ["sessions", "Sessions", "sessions"],
  ["activity", "Activity", "activity"],
  ["observability", "Observability", "chart"],
  ["infrastructure", "Infrastructure", "infra"],
];
$("#nav").innerHTML = nav
  .map(
    ([id, name, ic]) =>
      `<a class="nav-link" href="#${id}" data-nav="${id}">${icon(ic)}${name}</a>`,
  )
  .join("");
$("#settings-link").innerHTML = icon("settings") + "Settings";
$("#search-icon").innerHTML = icon("search");
$("#menu-toggle").innerHTML = icon("menu");
async function loadSessions(more = false) {
  const r = await api(
    "/v1/sessions?limit=50" + (more && nextAfter ? "&after=" + nextAfter : ""),
  );
  sessions = more
    ? [
        ...sessions,
        ...r.sessions.filter((s) => !sessions.some((x) => x.id === s.id)),
      ]
    : r.sessions;
  nextAfter = r.next_after;
}
async function loadCatalog() {
  const r = await Promise.all([api("/v1/releases"), api("/v1/profiles")]);
  releases = r[0].entries;
  profiles = r[1].entries;
}
function rows(list) {
  return list.length
    ? `<div class="card table-wrap"><table class="table"><thead><tr><th>Session</th><th>Status</th><th>Model</th><th>Created</th><th></th></tr></thead><tbody>${list.map((s) => `<tr data-session="${esc(s.id)}" tabindex="0" role="link"><td><span class="row-title">${esc(s.name || s.id)}</span><span class="cell-subtitle mono">${esc(s.id)}</span></td><td>${status(s.closed ? "closed" : s.pending ? "waiting" : s.status)}</td><td>${esc(s.model || "—")}</td><td>${esc(new Date(s.created).toLocaleString())}</td><td class="row-arrow">${icon("chevron")}</td></tr>`).join("")}</tbody></table></div>`
    : empty("No sessions to show.");
}
function stats() {
  return `<div class="stats">${[
    ["Sessions", sessions.length],
    [
      "Active",
      sessions.filter(
        (s) => !s.closed && !terminal.has(s.status) && s.status !== "idle",
      ).length,
    ],
    ["Awaiting response", sessions.filter((s) => s.pending > 0).length],
  ]
    .map(
      ([name, n]) =>
        `<div class="stat"><div class="stat-label">${name}</div><div class="stat-main"><span class="stat-value">${n}</span></div></div>`,
    )
    .join("")}</div>`;
}
function chart() {
  const buckets = {};
  for (const s of sessions) {
    const day = new Date(s.created).toLocaleDateString();
    buckets[day] = (buckets[day] || 0) + 1;
  }
  const entries = Object.entries(buckets).slice(0, 14).reverse(),
    max = Math.max(1, ...entries.map((x) => x[1]));
  return `<section class="card real-chart"><h2>Sessions by day</h2><div class="bar-days">${entries.map(([d, n]) => `<div class="day"><span>${n}</span><meter min="0" max="${max}" value="${n}" aria-label="${esc(d)}: ${n} sessions"></meter><small>${esc(d)}</small></div>`).join("") || empty("Activity will appear when sessions are created.")}</div></section>`;
}
function overview() {
  const waiting = sessions.filter((s) => s.pending > 0);
  return (
    head(
      "Overview",
      "",
      can.run() ? btn("New session", "new-session", true, "plus") : "",
    ) +
    stats() +
    `<p class="scope-note">Based on ${sessions.length} loaded sessions${nextAfter ? " · more available" : ""}.</p><div class="overview-grid">${chart()}<section class="card attention"><h2>Needs attention</h2>${
      waiting.length
        ? waiting
            .slice(0, 3)
            .map(
              (s) =>
                `<button class="attention-request" data-session="${esc(s.id)}">${esc(s.name || s.id)} ${icon("arrow")}</button>`,
            )
            .join("")
        : '<p class="small-note">No pending decisions in these sessions.</p>'
    }</section></div><div class="section-heading"><h2>Recent sessions</h2><a class="text-link" href="#sessions">All sessions ${icon("arrow")}</a></div>${rows(sessions.slice(0, 10))}`
  );
}
function agentsPage() {
  return (
    head(
      "Agents",
      "",
      can.publish() ? btn("Publish release", "new-release", true, "plus") : "",
    ) +
    `<div class="agent-grid">${releases.map((a) => `<article class="card agent-card"><div class="agent-card-top"><span class="row-icon">${icon("agents")}</span><span class="tag">${a.activated ? "Active" : "Unactivated"}</span></div><h2>${esc(a.id)}</h2><p>${esc(a.definition.instructions.slice(0, 160))}</p><div class="agent-card-meta"><span class="tag">${esc(a.definition.defaultModel?.model || "Service default")}</span><span class="tag">${a.definition.tools.length} tools</span></div><div class="agent-card-foot">${btn("Details", "release:" + a.id)}${can.activate() && !a.activated ? btn("Activate", "activate-release:" + a.id) : ""}${can.run() && a.activated ? btn("Run", "run:" + a.id, true) : ""}</div></article>`).join("")}</div>${releases.length ? "" : empty("No visible agent releases.")}`
  );
}
function sessionList() {
  return (
    head(
      "Sessions",
      "",
      can.run() ? btn("New session", "new-session", true, "plus") : "",
    ) +
    `<div class="toolbar"><label class="input-search">${icon("search")}<input id="session-search" placeholder="Search loaded sessions" aria-label="Search sessions"></label><select id="status-filter" aria-label="Session status"><option value="">All statuses</option><option>running</option><option>waiting</option><option>completed</option><option>failed</option><option>idle</option></select></div><div id="session-table">${rows(sessions)}</div>${nextAfter ? btn("Load more", "more-sessions") : ""}`
  );
}
function activity() {
  return (
    head("Activity") +
    stats() +
    `<p class="scope-note">Loaded-session summary · ${sessions.length} sessions. Open a session for its recorded trace.</p>${chart()}<div class="section-heading activity-heading"><h2>Execution records</h2></div>${rows(sessions.slice(0, 20))}<details class="export-note"><summary>Export integrations</summary><p>Grafana and Langfuse configuration will be added here. This page currently reads service records.</p></details>`
  );
}
function profileTable() {
  return `<div class="card table-wrap"><table class="table"><thead><tr><th>RuntimeProfile</th><th>Provider</th><th>Resources</th><th>Approvals</th><th></th></tr></thead><tbody>${profiles.map((p) => `<tr><td>${esc(p.id)}<span class="cell-subtitle">${p.activated ? "Active" : "Unactivated"}</span></td><td>${esc(p.definition.provider)}</td><td>${p.definition.cpu} CPU · ${p.definition.memoryMB} MB</td><td>${esc(p.definition.approvalMode || "ask")}</td><td>${btn("Details", "profile:" + p.id)}${can.configure() && !p.activated ? btn("Activate", "activate-profile:" + p.id) : ""}</td></tr>`).join("")}</tbody></table></div>`;
}
function infrastructure() {
  return (
    head("Infrastructure") +
    `<div class="pool-layout">${pools.map((p) => `<section class="card pool-card"><div class="pool-top"><h2>${esc(p.binding.id)}</h2><span class="tag">${esc(p.binding.ownership)}</span></div><div class="pool-count">${p.desiredReplicas}<small>desired workers</small></div><p class="small-note">${esc(human(p.observed.status || "unknown"))} · ${p.observed.readyReplicas ?? "—"} ready</p><p class="small-note">${esc(p.observed.reason || "")}</p><div class="pool-footer">${btn("Details", "pool:" + p.binding.id)}${can.admin() && p.binding.ownership === "manual" ? btn("Scale", "scale:" + p.binding.id) : ""}</div></section>`).join("") || empty("No registered worker pools.")}</div><div class="section-heading"><h2>Runtime profiles</h2>${can.configure() ? btn("New profile", "new-profile", false, "plus") : ""}</div>${profileTable()}${can.admin() ? `<div class="section-heading activity-heading"><h2>Environments</h2>${btn("View environments", "environments")}</div>` : ""}`
  );
}
function settings(value) {
  return (
    head("Settings") +
    `<div class="tabs">${["Defaults", ...(can.admin() ? ["Access", "Audit"] : []), "Integrations"].map((t) => `<button class="tab ${t === settingsTab ? "active" : ""}" data-settings-tab="${t}">${t}</button>`).join("")}</div><div class="settings-layout">${settingsTab === "Defaults" ? `<div class="settings-section"><div class="setting-row"><span>Admissions</span><span>${value.configuration.admissionsPaused ? "Paused" : "Open"}</span></div><div class="setting-row"><span>Compaction threshold</span><span>${esc(Math.round(value.configuration.compaction.threshold_pct * 100))}%</span></div><div class="setting-row"><span>Environments per principal</span><span>${value.configuration.maxEnvironmentsPerPrincipal}</span></div><div class="setting-row"><span>Revision</span><span>${value.revision}</span></div></div>${can.admin() ? btn("Edit configuration", "configuration", true) : ""}` : settingsTab === "Access" ? `<div class="section-heading"><h2>Service credentials</h2>${btn("Issue token", "issue", true)}</div><div class="card table-wrap"><table class="table"><thead><tr><th>Principal</th><th>Role</th><th>Expires</th><th></th></tr></thead><tbody>${(value.credentials || []).map((c) => `<tr><td>${esc(c.principal || c.spec?.principal || c.id)}</td><td>${esc(c.role || c.spec?.role)}</td><td>${c.expires_at || c.spec?.expires_at ? esc(new Date((c.expires_at || c.spec.expires_at) * 1000).toLocaleString()) : "—"}</td><td>${c.revoked ? "Revoked" : btn("Revoke", "revoke:" + c.id)}</td></tr>`).join("")}</tbody></table></div>${value.next_after ? btn("Next page", "credentials-next:" + value.next_after) : ""}` : settingsTab === "Audit" ? `<details open><summary>Security audit</summary><pre>${esc(pretty(value))}</pre></details>` : `<div class="integration-card"><div><h3>Grafana and Langfuse</h3><p>Export configuration will be added here.</p></div></div>`}</div>`
  );
}
function renderTrace() {
  return events.length
    ? `<div class="timeline">${events.map((e) => `<div class="trace-step"><span class="step-dot">${icon(e.type.includes("tool") ? "terminal" : "activity")}</span><details data-event="${e.event_id}"><summary><span>${esc(human(e.type))}</span><span class="step-time">#${e.event_id}</span>${icon("chevron", "chevron")}</summary><div class="trace-body"><pre>${esc(pretty(e.data))}</pre></div></details></div>`).join("")}</div>`
    : empty("No recorded events yet.");
}
function sessionContent() {
  const s = snapshot;
  return sessionTab === "Trace"
    ? renderTrace()
    : sessionTab === "Result"
      ? `<div class="session-result">${s.messages.length ? s.messages.map((m) => `<article><h3>${esc(m.role || m.type || "Message")}</h3><pre>${esc(typeof m.content === "string" ? m.content : pretty(m.content ?? m))}</pre></article>`).join("") : empty("No messages yet.")} ${s.history_truncated ? "<p>Showing the most recent messages.</p>" : ""}</div>`
      : `<pre class="raw-events">${esc(pretty(events))}</pre>`;
}
function paintSessionContent() {
  const root = $("#trace-content");
  const opened = [...root.querySelectorAll("details[data-event][open]")].map(
    (el) => el.dataset.event,
  );
  root.innerHTML = sessionContent();
  for (const id of opened) {
    const el = root.querySelector(`details[data-event="${id}"]`);
    if (el) el.open = true;
  }
}
function sessionPage() {
  const s = snapshot.session,
    t = snapshot.turn;
  return `<div class="detail-heading"><a class="back" href="#sessions">${icon("back")}Sessions</a><div class="session-title-row"><h1>${esc(s.name || s.id)}</h1><div class="heading-actions">${can.run() && !s.closed ? btn("Send message", "send") + (t && !terminal.has(t.status) ? (cancelRequested() ? '<span class="small-note">Cancellation requested</span>' : btn("Cancel run", "cancel")) : "") : ""}${btn("Refresh", "refresh")}</div></div><div class="session-meta">${status(s.closed ? "closed" : t?.status || "idle")}<span class="mono">${esc(s.id)}</span><span id="stream-state">Connecting…</span></div><div class="tabs">${["Trace", "Result", "Events"].map((t) => `<button class="tab ${t === sessionTab ? "active" : ""}" data-tab="${t}">${t}</button>`).join("")}</div></div><div class="detail-layout"><section><div id="interactions">${interactions()}</div><div id="trace-content">${sessionContent()}</div><p class="small-note" id="event-note">Showing up to 500 recent events.</p></section><aside class="detail-side"><div><h3>Session</h3>${[
    ["Agent", snapshot.service?.releaseId || s.coworker],
    ["Model", s.model_selection?.model || s.model],
    ["Runtime profile", snapshot.service?.runtimeProfile || "—"],
    ["Workspace", s.workspace],
    ["Execution", s.sandbox],
    ["Mode", snapshot.mode],
  ]
    .map(
      ([k, v]) =>
        `<div class="property"><small>${k}</small><div>${esc(v || "—")}</div></div>`,
    )
    .join(
      "",
    )}<details><summary>Identity and policy</summary><pre>${esc(pretty({ service: snapshot.service, environment: s.environment, permissions: s.permissions, enforcement: s.enforcement }))}</pre></details></div></aside></div>`;
}
function interactions() {
  return snapshot.interactions
    .map((i) => {
      const control = (snapshot.controls || []).find(
        (c) => c.target === "response:" + i.id,
      );
      return `<div class="approval-box"><h3>${control ? "Decision submitted" : i.kind === "approval" ? "Approval needed" : "Response needed"}</h3><details><summary>${esc(i.call?.name || human(i.kind))}</summary><pre>${esc(pretty(i.call))}</pre></details>${control ? `<p>Decision ${esc(control.status)}${control.reason ? ": " + esc(control.reason) : ""}</p>` : can.run() ? btn(i.kind === "approval" ? "Review request" : "Answer", "interaction:" + i.id, true) : "<p>Your credential can view this request but cannot answer it.</p>"}</div>`;
    })
    .join("");
}
function cancelRequested() {
  return (snapshot.controls || []).some(
    (c) =>
      c.target === "cancel:" + snapshot.turn?.id && c.status !== "rejected",
  );
}
async function updateSnapshot(sid, v) {
  const value = await api("/v1/sessions/" + sid);
  if (version !== v) return;
  snapshot = value;
  $("#interactions").innerHTML = interactions();
  const cancel = document.querySelector('[data-action="cancel"]');
  if (cancel && cancelRequested()) {
    cancel.disabled = true;
    cancel.textContent = "Cancellation requested";
  }
  if (sessionTab === "Result") paintSessionContent();
  const el = $(".session-meta .status");
  if (el)
    el.outerHTML = status(
      value.session.closed ? "closed" : value.turn?.status || "idle",
    );
}
function connectStream(sid, v) {
  if (version !== v || !auth) return;
  stream?.close();
  stream = new EventSource(
    `/api/v1/sessions/${sid}/events?after=${eventCursor}`,
  );
  stream.onopen = () => {
    if (version === v) $("#stream-state").textContent = "Live";
  };
  stream.addEventListener("progress", (message) => {
    if (version !== v) return;
    try {
      const e = JSON.parse(message.data);
      if (e.event_id <= eventCursor) return;
      if (e.event_id !== eventCursor + 1) throw Error("gap");
      eventCursor = e.event_id;
      events.push(e);
      events = events.slice(-500);
      if (sessionTab !== "Result") paintSessionContent();
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(
        () => updateSnapshot(sid, v).catch(() => {}),
        350,
      );
    } catch {
      stream.close();
      $("#stream-state").textContent = "Refresh required";
    }
  });
  const retry = () => {
    if (version !== v) return;
    stream.close();
    $("#stream-state").textContent = "Reconnecting…";
    reconnect = setTimeout(async () => {
      try {
        await updateSnapshot(sid, v);
        connectStream(sid, v);
      } catch {
        if (auth && version === v) retry();
      }
    }, 2000);
  };
  stream.onerror = retry;
  stream.addEventListener("stream_error", retry);
}
async function render() {
  if (!auth) return;
  const v = ++version;
  stop();
  const [route = "overview", id] = location.hash.slice(1).split("/");
  const page = route || "overview";
  $("#sidebar").classList.remove("open");
  $("#crumb").textContent = page === "operation" ? "Submission" : human(page);
  for (const a of document.querySelectorAll("[data-nav]")) {
    a.classList.toggle("active", a.dataset.nav === page);
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  main.innerHTML = empty("Loading…");
  try {
    if (page === "observability") {
      disposeObservability = mountObservability(main, {
        request: (path, signal) =>
          api(path, "GET", undefined, undefined, signal),
        isAdmin: can.admin(),
      });
      return;
    }
    let html;
    if (
      page === "overview" ||
      (page === "sessions" && !id) ||
      page === "activity"
    ) {
      await loadSessions();
      if (v !== version) return;
      html =
        page === "overview"
          ? overview()
          : page === "activity"
            ? activity()
            : sessionList();
    } else if (page === "agents") {
      await loadCatalog();
      html = agentsPage();
    } else if (page === "infrastructure") {
      await loadCatalog();
      pools = (await api("/v1/worker-pools")).pools;
      html = infrastructure();
    } else if (page === "settings") {
      const value =
        settingsTab === "Defaults"
          ? await api("/v1/configuration")
          : settingsTab === "Access"
            ? await api("/v1/credentials")
            : settingsTab === "Audit"
              ? await api("/v1/audit?after=0")
              : {};
      html = settings(value);
    } else if (page === "sessions" && id) {
      snapshot = await api("/v1/sessions/" + id);
      eventCursor = Math.max(0, snapshot.cursor - 500);
      const result = await api(
        `/v1/sessions/${id}/events/page?after=${eventCursor}`,
      );
      events = result.events;
      if (events.length) eventCursor = events.at(-1).event_id;
      html = sessionPage();
    } else if (page === "operation" && id) {
      const o = await api("/v1/operations/" + id);
      if (v !== version) return;
      if (o.status === "complete") {
        location.hash = "sessions/" + o.sessionId;
        return;
      }
      html =
        head("Session provisioning") +
        `<div class="card pool-card">${status(o.status)}<p class="mono">${esc(o.id)}</p><p>${esc(o.error || "")}</p><details><summary>Details</summary><pre>${esc(pretty(o))}</pre></details></div>`;
      if (!["failed"].includes(o.status)) reconnect = setTimeout(render, 2500);
    } else html = empty("Page not found");
    if (v !== version) return;
    main.innerHTML = html;
    $("#connection-label").textContent = "Connected";
    if (page === "sessions" && id) connectStream(id, v);
  } catch (e) {
    if (v !== version || !auth) return;
    $("#connection-label").textContent = "Unavailable";
    main.innerHTML =
      head("Unable to load") + `<p role="alert">${esc(friendly(e))}</p>`;
  }
}
function details(title, data) {
  drawer(title, `<pre>${esc(pretty(data))}</pre>`);
}
async function newSession(agent) {
  await loadCatalog();
  const choices = releases.filter((r) => r.activated);
  if (!choices.length) {
    toast("No active releases available.");
    return;
  }
  choices.sort((a, b) => (a.id === agent ? -1 : b.id === agent ? 1 : 0));
  drawer(
    "New session",
    select(
      "Agent release",
      "agent",
      choices.map((r) => [r.id, r.id]),
    ) +
      field("Task", "task", "", "textarea") +
      `<details><summary>Session options</summary>${field("Project", "project", "default")}${select("Runtime profile", "profile", [["", "Agent default"], ...profiles.filter((p) => p.activated).map((p) => [p.id, p.id])])}${field("Existing environment (optional)", "environment", "")}</details>`,
    async () => {
      const body = {
        agent: $("#agent").value,
        text: $("#task").value,
        project: $("#project").value || "default",
      };
      if ($("#profile").value) body.runtimeProfile = $("#profile").value;
      if ($("#environment").value) {
        body.environmentId = $("#environment").value;
        delete body.runtimeProfile;
      }
      await mutate("/v1/submissions", "POST", body, async (r) => {
        location.hash = "operation/" + r.operation.id;
      });
    },
    "Start session",
  );
  $("#environment").required = false;
}
async function controls(action, { sid, runId }) {
  await mutate(
    `/v1/sessions/${sid}/controls`,
    "POST",
    { runId, action },
    async (r) => {
      toast(
        "Control " +
          human(r.status) +
          ". The worker applies it asynchronously.",
      );
      await render();
    },
  );
}
async function act(action) {
  const [kind, ...rest] = action.split(":"),
    id = rest.join(":");
  if (kind === "refresh") return render();
  if (kind === "close-panel") {
    if (pending) {
      toast("Retry the pending request before closing.");
      return;
    }
    panel.close();
    $("#panel-content").replaceChildren();
    return;
  }
  if (kind === "new-session" || kind === "run") return newSession(id);
  if (kind === "more-sessions") {
    await loadSessions(true);
    main.innerHTML = sessionList();
    return;
  }
  if (kind === "release" || kind === "profile")
    return details(
      id,
      (kind === "release" ? releases : profiles).find((x) => x.id === id),
    );
  if (kind === "pool")
    return details(
      id,
      pools.find((p) => p.binding.id === id),
    );
  if (kind === "new-release")
    return jsonDrawer(
      "Publish agent release",
      {
        id: "my-agent@1",
        instructions: "Help the user complete their task.",
        tools: [],
        runtimeProfiles: profiles.filter((p) => p.activated).map((p) => p.id),
        allowedPrincipals: [auth.identity.principal],
      },
      "/v1/releases",
    );
  if (kind === "new-profile")
    return jsonDrawer(
      "New runtime profile",
      {
        id: "standard@1",
        provider: "openshell",
        cpu: 1,
        memoryMB: 512,
        sharing: "exclusive",
        approvalMode: "ask",
        autoSkipQuestions: false,
        allowedPrincipals: [auth.identity.principal],
      },
      "/v1/profiles",
    );
  if (kind === "activate-release" || kind === "activate-profile")
    return (
      drawer(
        "Activate " + id,
        field("Alias (optional)", "alias", ""),
        () =>
          mutate(
            `/v1/${kind === "activate-release" ? "releases" : "profiles"}/${id}/activate`,
            "POST",
            { alias: $("#alias").value || null },
            async () => {
              toast("Activated");
              await render();
            },
          ),
        "Activate",
      ),
      ($("#alias").required = false)
    );
  if (kind === "scale") {
    const p = pools.find((p) => p.binding.id === id);
    drawer(
      "Scale " + id,
      field("Desired workers", "replicas", p.desiredReplicas, "number") +
        '<p class="small-note">This changes desired capacity. Running work drains before workers stop.</p>',
      () =>
        mutate(
          `/v1/worker-pools/${id}/scale`,
          "POST",
          {
            expectedRevision: p.revision,
            replicas: Number($("#replicas").value),
          },
          async () => {
            toast("Scale request accepted");
            await render();
          },
        ),
      "Apply",
    );
    $("#replicas").min = 0;
    $("#replicas").max = p.binding.maxReplicas;
    return;
  }
  if (kind === "configuration") {
    const c = await api("/v1/configuration");
    drawer(
      "Service configuration",
      field(
        "Configuration",
        "configuration-json",
        pretty(c.configuration),
        "textarea",
      ) +
        `<p class="small-note">Revision ${c.revision}. Defaults apply to new sessions.</p>`,
      () =>
        mutate(
          "/v1/configuration",
          "PUT",
          {
            expectedRevision: c.revision,
            configuration: JSON.parse($("#configuration-json").value),
          },
          async () => {
            toast("Configuration saved");
            await render();
          },
        ),
    );
    return;
  }
  if (kind === "environments")
    return details("Environments", await api("/v1/environments"));
  if (kind === "cancel") {
    const target = { sid: snapshot.session.id, runId: snapshot.turn.id };
    return drawer(
      "Cancel this run?",
      `<p>${esc(snapshot.session.name)}</p><p class="small-note">Existing work remains available.</p>`,
      () => controls({ kind: "cancel" }, target),
      "Cancel run",
    );
  }
  if (kind === "send")
    return drawer(
      "Send message",
      field("Message", "message", "", "textarea"),
      () =>
        mutate(
          `/v1/sessions/${snapshot.session.id}/turns`,
          "POST",
          { text: $("#message").value },
          async () => {
            toast("Message accepted");
            await render();
          },
        ),
      "Send",
    );
  if (kind === "interaction") {
    const target = { sid: snapshot.session.id, runId: snapshot.turn.id };
    const i = snapshot.interactions.find((x) => x.id === id);
    let args = {};
    try {
      args = JSON.parse(i.call?.arguments || "{}");
    } catch {}
    const options = Array.isArray(args.options)
      ? args.options.filter((x) => typeof x === "string")
      : [];
    const binding = Object.fromEntries(
      [
        "bundle_digest",
        "schema_digest",
        "policy_digest",
        "resource_policy_digest",
      ].map((k) => [k, i[k]]),
    );
    drawer(
      i.kind === "approval" ? "Review tool request" : "Answer question",
      `<pre>${esc(pretty(i.call))}</pre>` +
        (i.kind === "approval"
          ? select("Decision", "response", [
              ["once", "Allow once"],
              ["session", "Allow for this session"],
              ["deny", "Deny"],
            ])
          : options.length
            ? select(
                "Response",
                "response",
                options.map((o) => [o, o]),
              )
            : field("Response", "response", "", "textarea")),
      () =>
        controls(
          {
            kind: "response",
            interaction_id: i.id,
            response: $("#response").value,
            binding,
          },
          target,
        ),
      "Submit decision",
    );
    return;
  }
  if (kind === "issue") {
    const value = Array.from(crypto.getRandomValues(new Uint8Array(32)), (x) =>
      x.toString(16).padStart(2, "0"),
    ).join("");
    drawer(
      "Issue service token",
      field("Principal", "principal", "") +
        select("Role", "role", [
          ["operator", "Runner"],
          ["observer", "Observer"],
          ["publisher", "Publisher"],
          ["release_administrator", "Release administrator"],
          ["service_administrator", "Service administrator"],
        ]) +
        select("Lifetime", "lifetime", [
          ["3600", "1 hour"],
          ["86400", "1 day"],
          ["604800", "7 days"],
        ]) +
        `<details><summary>Token — copy before issuing</summary><pre>${value}</pre><p class="small-note">Keep this value private. It is cleared when this drawer closes.</p></details>`,
      async () => {
        const r = $("#role").value;
        await mutate(
          "/v1/credentials",
          "POST",
          {
            token: value,
            spec: {
              principal: $("#principal").value,
              role: r,
              scope: {
                kind: ["operator", "observer"].includes(r) ? "owned" : "all",
              },
              expires_at:
                Math.floor(Date.now() / 1000) + Number($("#lifetime").value),
            },
          },
          async () => {
            toast("Credential issued");
            await render();
          },
        );
      },
      "Issue token",
    );
    return;
  }
  if (kind === "revoke")
    return drawer(
      "Revoke credential?",
      `<p class="mono">${esc(id)}</p><p class="small-note">Requests using this token will be rejected.</p>`,
      () =>
        mutate(`/v1/credentials/${id}/revoke`, "POST", {}, async () => {
          toast("Credential revoked");
          await render();
        }),
      "Revoke",
    );
  if (kind === "credentials-next") {
    main.innerHTML = settings(await api("/v1/credentials?after=" + id));
    return;
  }
}
document.addEventListener("click", async (e) => {
  try {
    const session = e.target.closest("[data-session]");
    if (session) {
      modal.close();
      sessionTab = "Trace";
      location.hash = "sessions/" + session.dataset.session;
      return;
    }
    const tab = e.target.closest("[data-tab]");
    if (tab) {
      sessionTab = tab.dataset.tab;
      for (const b of document.querySelectorAll("[data-tab]"))
        b.classList.toggle("active", b === tab);
      paintSessionContent();
      return;
    }
    const t = e.target.closest("[data-settings-tab]");
    if (t) {
      settingsTab = t.dataset.settingsTab;
      await render();
      return;
    }
    const close = e.target.closest("[data-close]");
    if (close) {
      if (pending && close.dataset.close === "panel") {
        toast("Resolve the pending request first.");
        return;
      }
      document.getElementById(close.dataset.close).close();
      $("#panel-content").replaceChildren();
      return;
    }
    const a = e.target.closest("[data-action]");
    if (a) await act(a.dataset.action);
  } catch (e) {
    toast(friendly(e));
  }
});
function filterRows() {
  const q = $("#session-search").value.toLowerCase(),
    s = $("#status-filter").value;
  $("#session-table").innerHTML = rows(
    sessions.filter(
      (r) =>
        (!s || (r.pending ? "waiting" : r.status) === s) &&
        `${r.name} ${r.id}`.toLowerCase().includes(q),
    ),
  );
}
document.addEventListener("input", (e) => {
  if (e.target.id === "session-search") filterRows();
});
document.addEventListener("change", (e) => {
  if (e.target.id === "status-filter") filterRows();
});
function search() {
  drawer(
    "Find a session",
    field("Session ID", "find-session", ""),
    () => {
      panel.close();
      location.hash =
        "sessions/" + encodeURIComponent($("#find-session").value.trim());
    },
    "Open session",
  );
}
$("#search-trigger").onclick = search;
$("#menu-toggle").onclick = () => $("#sidebar").classList.toggle("open");
$("#workspace").onclick = () =>
  details("Service connection", {
    service: auth.capabilities.serviceId,
    namespace: auth.capabilities.namespace,
    principal: auth.identity.principal,
    role: auth.identity.role,
    scope: auth.identity.scope,
  });
$("#profile-button").onclick = () => {
  drawer(
    "Signed in",
    `<p>${esc(auth.identity.principal)}</p><p class="small-note">${esc(human(role()))}</p>`,
    async () => {
      await fetch("/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": auth.csrf },
      });
      loggedOut();
    },
    "Sign out",
  );
};
for (const d of [panel, modal])
  d.addEventListener("cancel", (e) => {
    if (pending) e.preventDefault();
    else $("#panel-content").replaceChildren();
  });
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "k" && auth) {
    e.preventDefault();
    search();
  }
  if (e.key === "Enter" && e.target.matches("tr[data-session]"))
    e.target.click();
});
window.addEventListener("hashchange", render);
wireForm();
fetch("/session")
  .then(async (r) => {
    if (r.ok) {
      auth = await r.json();
      showApp();
    } else loggedOut();
  })
  .catch(() => loggedOut("Service unavailable."));

window.addEventListener("beforeunload", (event) => {
  if (pending) {
    event.preventDefault();
    event.returnValue = "";
  }
});
