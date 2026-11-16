import { usageTotals } from "./usage.js";
const $ = (s) => document.querySelector(s);
const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" aria-hidden="true"><use href="/icons.svg#${name}"></use></svg>`;
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  samples: [],
  guides: [],
  sampleId: "door-scratch",
  upload: null,
  notes: "",
  part: "Door panel",
  station: "Paint inspection",
  current: null,
  records: [],
  health: null,
  page: "inspect",
  annotations: true,
  submitting: false,
  filter: "",
  source: null,
};
const labels = {
  pass: "Passed visual inspection",
  inspect: "Further inspection",
  rework: "Sent for rework",
  hold: "Assembly on hold",
  analyzing: "Assessing",
  review: "Needs review",
  reviewed: "Reviewed",
  error: "Needs attention",
};
const date = (v) =>
    new Date(v).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    }),
  clock = (v) =>
    new Date(v).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
const currentSample = () =>
  state.samples.find((s) => s.id === state.sampleId) ?? state.samples[0];
const photoUrl = () =>
  state.current
    ? `/api/inspections/${state.current.id}/photo`
    : (state.upload?.url ??
      `/samples/${currentSample()?.file ?? "door-scratch.png"}`);
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $("#toast").classList.remove("visible"), 5000);
}
async function api(path, { method = "GET", body } = {}) {
  const r = await fetch("/api" + path, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let x;
  try {
    x = await r.json();
  } catch {
    throw Error("The edge server returned an unreadable response.");
  }
  if (!r.ok) throw Error(x.error ?? "The request could not be completed.");
  return x;
}
function shell() {
  const connected = state.health?.agent?.connected;
  $("#app").innerHTML =
    `<aside class="sidebar"><a href="#inspect" class="brand"><span class="brand-mark">${icon("scan")}</span><span class="brand-name">InspectionBuddy</span></a><div class="workspace-label"><div class="factory-mark">${icon("layers")}</div><div><strong>Assembly workspace</strong><span>Visual inspection</span></div></div><nav class="nav" aria-label="Main navigation"><a href="#inspect" class="${state.page === "inspect" ? "active" : ""}">${icon("scan")}<span>Inspect a part</span></a><a href="#history" class="${state.page === "history" ? "active" : ""}">${icon("history")}<span>Inspection log</span></a><a href="#guides" class="${state.page === "guides" ? "active" : ""}">${icon("book")}<span>Reference guide</span></a></nav><div class="sidebar-bottom">A little more confidence.<br>A clearer next step.<div class="powered">${icon("spark")}<span>Built with <strong>OpenWorker</strong></span></div></div></aside><div class="main-shell"><header class="topbar"><div class="breadcrumb">Factory floor <span>/</span><strong>${state.page === "inspect" ? "Inspection" : state.page === "history" ? "Inspection log" : "Reference guide"}</strong></div><div class="topbar-right"><button class="connection" data-action="connection"><span class="dot ${connected ? "live" : "offline"}"></span>${esc(connectionLabel())}</button><div class="avatar" title="Inspector workspace">QA</div></div></header><main class="page" id="page-content"></main></div>`;
  renderPage();
}
function renderPage() {
  if (state.page === "history") renderHistory();
  else if (state.page === "guides") renderGuides();
  else renderInspection();
}
function photoCard() {
  const r = state.current,
    s = currentSample(),
    part = r?.part ?? state.part,
    synthetic = r?.synthetic ?? !state.upload;
  return `<section class="photo-card" aria-label="Inspection photo"><div class="photo-top"><div class="photo-heading"><span class="part-icon">${icon("image")}</span><div><strong>${esc(part)}</strong><small>${esc(r?.station ?? state.station)}${r ? " · " + esc(r.code) : ""}</small></div></div><button class="icon-button" data-action="zoom" aria-label="Enlarge photo">${icon("zoom")}</button></div><div class="photo-stage" id="photo-stage"><img src="${esc(photoUrl())}" alt="${esc(synthetic ? (r?.photoName ?? s?.subtitle) : "Uploaded " + part + " inspection photo")}" draggable="false">${annotations()}${synthetic ? '<span class="sample-tag">SYNTHETIC SAMPLE</span>' : ""}</div><div class="photo-bottom"><span class="synthetic-note">${icon("info")}${synthetic ? "Illustrative photo. Not a production inspection." : r ? `Uploaded photo · saved on ${locationLabel()}` : "Local preview · not yet submitted"}</span><button class="text-button" data-action="${r?.report ? "annotations" : "upload"}">${icon(r?.report ? "scan" : "upload")}${r?.report ? (state.annotations ? "Hide findings" : "Show findings") : "Upload your own"}</button></div></section>`;
}
function annotations() {
  const findings = state.current?.report?.findings ?? [];
  const image = $("#photo-stage > img");
  const viewH = image?.naturalWidth
    ? (1000 * image.naturalHeight) / image.naturalWidth
    : 666.67;
  if (!state.annotations) return "";
  return `<svg class="annotations" viewBox="0 0 1000 ${viewH}" preserveAspectRatio="xMidYMid meet" aria-label="Suggested locations of visible findings">${findings
    .filter((f) => f.box)
    .map((f) => {
      const [x, y, w, h] = f.box;
      return `<g><rect x="${x * 1000}" y="${y * viewH}" width="${w * 1000}" height="${h * viewH}" rx="5"/><rect class="label-bg" x="${x * 1000}" y="${Math.max(1, y * viewH - 25)}" width="31" height="21" rx="4"/><text x="${x * 1000 + 7}" y="${Math.max(1, y * viewH - 25) + 14}">${esc(f.id)}</text></g>`;
    })
    .join("")}</svg>`;
}
function localMode() { return state.health?.role === "local-development"; }
function locationLabel() { return localMode() ? "this computer" : "the edge server"; }
function connectionLabel() {
  return state.health?.agent?.connected
    ? (localMode() ? "Local harness connected" : "Edge server connected")
    : (localMode() ? "Local model setup required" : "Edge connection required");
}
function activity() {
  const r = state.current;
  if (!r) return "";
  const sum = r.trace?.summary;
  const totals = usageTotals(
    [r.trace, ...(r.followupTraces ?? []).map((t) => t.trace)].filter(Boolean),
  );
  const usage = {
    input: { total: totals.input },
    output: { total: totals.output },
  };
  const total = totals.total;
  return `<details class="activity"><summary><span>${icon("layers")} Agent activity</span><span>${total !== null ? total.toLocaleString() + (totals.partial ? "+ reported tokens" : " tokens") : "Saved agent session"} ${icon("chevron")}</span></summary><div class="activity-list">${r.activity
    .slice(-12)
    .map(
      (a) =>
        `<div class="activity-row"><span>${esc(a.label)}</span><time>${clock(a.at)}</time></div>`,
    )
    .join(
      "",
    )}</div>${sum ? `<div class="activity-meta">Input ${usage?.input?.total?.toLocaleString() ?? "not reported"} · Output ${usage?.output?.total?.toLocaleString() ?? "not reported"} tokens${sum.wall_elapsed_us != null ? " · Assessment " + (sum.wall_elapsed_us / 1000000).toFixed(1) + "s" : ""}</div>` : ""}${r.service?.session ? `<div class="activity-meta">Session ${esc(r.service.session.sessionId ?? r.service.session.session_id)}</div>` : ""}</details>`;
}
function assistant() {
  const r = state.current;
  let body;
  if (r?.status === "analyzing") {
    const latest = r.activity.at(-1)?.label ?? "Preparing inspection";
    body = `<div class="assistant-body"><div class="progress-orb">${icon("spark")}</div><span class="eyebrow">ASSESSING YOUR PHOTO</span><h2>A closer look.<br>A clearer next step.</h2><div class="progress-label" aria-live="polite">${esc(latest)}</div><div class="progress-bar"></div><p class="progress-note">Your inspection is saved on ${locationLabel()}. You can leave this page and return to it.</p>${activity()}</div>`;
  } else if (r?.report) {
    const a = r.report;
    body = `<div class="assistant-body"><div class="row"><span class="severity ${a.severity}"><span class="dot"></span>${{ low: "Low concern", moderate: "Moderate concern", high: "High concern", uncertain: "More evidence needed" }[a.severity]}</span><span class="review-required">AI assessment</span></div><h2 class="report-title">${esc(a.title)}</h2><p class="report-summary">${esc(a.summary)}</p><div class="finding-list">${a.findings.map((f) => `<button class="finding" data-action="finding" data-id="${esc(f.id)}"><span class="number">${esc(f.id)}</span><span><strong>${esc(f.label)}</strong><p>${esc(f.evidence)}</p></span></button>`).join("")}</div><h3 class="report-subheading">What to check next</h3><ul class="check-list">${a.nextChecks.map((c) => `<li>${icon("check")}<span>${esc(c)}</span></li>`).join("")}</ul><details class="report-details"><summary>Evidence & limitations</summary><ul>${a.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul><div class="references">${a.references.map((id) => `<a class="reference" href="#guides">${esc(id)} · ${esc(state.guides.find((g) => g.id === id)?.title ?? "Guide")}</a>`).join("")}</div></details>${r.decision ? `<div class="decision-saved"><strong>${icon("check")} ${esc(labels[r.decision.disposition])}</strong>${esc(r.decision.inspector)} · ${date(r.decision.at)} ${clock(r.decision.at)}${r.decision.note ? "<br>" + esc(r.decision.note) : ""}</div><button class="text-button" data-action="review">Update inspector decision ${icon("chevron")}</button>` : `<div class="review-actions"><button class="button primary full-width" data-action="review">Record your decision ${icon("arrow")}</button><div class="under-button">Final disposition stays with the inspector.</div></div>`}${activity()}</div><div class="followup">${
      r.conversation?.length
        ? `<div class="conversation">${r.conversation
            .slice(-6)
            .map((m) => `<div class="bubble ${m.role}">${esc(m.text)}</div>`)
            .join(
              "",
            )}${r.followupBusy ? '<div class="bubble">Reviewing your question…</div>' : ""}</div>`
        : ""
    }${r.followupError ? `<p class="muted">${esc(r.followupError)}</p>` : ""}<form class="followup-form" id="followup-form"><input name="question" placeholder="Ask about this part…" aria-label="Ask a follow-up question" maxlength="2000" required ${r.followupBusy ? "disabled" : ""}><button type="submit" aria-label="Send question" ${r.followupBusy ? "disabled" : ""}>${icon("arrow")}</button></form></div>`;
  } else if (r?.status === "error") {
    body = `<div class="assistant-body"><span class="eyebrow">PHOTO SAVED</span><h2>Let’s take another look.</h2><div class="error-box">${esc(r.error)}</div><p class="muted">The inspection record and its photo are preserved. No request was resubmitted automatically.</p><button class="button secondary full-width" data-action="new">Start a new inspection ${icon("plus")}</button>${activity()}</div>`;
  } else {
    body = `<div class="assistant-body"><div class="assistant-intro">${icon("spark")} YOUR INSPECTION ASSISTANT</div><h2>See the issue.<br>Know what to check.</h2><p>A little extra context for the decisions<br>you make on the assembly line.</p><div class="benefits"><div class="benefit">${icon("scan")}<div><strong>Understand what’s visible</strong>Findings grounded in the actual photo.</div></div><div class="benefit">${icon("book")}<div><strong>Get a practical next step</strong>Suggested checks, with clear limitations.</div></div><div class="benefit">${icon("history")}<div><strong>Keep the whole story</strong>Photo, assessment and your final decision.</div></div></div><button class="button primary full-width" data-action="analyze" ${state.submitting ? "disabled" : ""}>${state.submitting ? "Connecting…" : "Assess this photo"} ${icon("arrow")}</button><div class="under-button">${icon("server")} ${localMode() ? "Agent on this computer" : "Agent on edge"} · Vision with OpenAI.</div>${!state.health?.agent?.connected ? `<div class="offline-hint">${localMode() ? 'Configure OPENAI_API_KEY and restart the app' : 'Connect the edge server'} to run a real assessment.<br>You can explore the sample photos now.</div>` : ""}</div>`;
  }
  return `<aside class="assistant-card" aria-label="Inspection assessment"><div class="assistant-top"><div class="assistant-title">${icon("spark")} InspectionBuddy</div><span class="live-tag"><span class="dot ${state.health?.agent?.connected ? "live" : ""}"></span>${state.current?.status === "analyzing" ? "WORKING" : "INSPECTION ASSISTANT"}</span></div>${body}</aside>`;
}
function renderInspection() {
  const r = state.current;
  $("#page-content").innerHTML =
    `<div class="page-heading"><div><span class="eyebrow">HUMAN JUDGMENT. BETTER INFORMED.</span><h1>${r ? "Every detail. On record." : "A second set of eyes."}</h1><p>${r ? esc(r.code) + " · " + esc(r.part) + " · " + date(r.createdAt) + " at " + clock(r.createdAt) : "Inspect assembly photos, understand visible defects, and keep every decision on record."}</p></div>${r ? `<button class="small-button" data-action="new">${icon("plus")} New inspection</button>` : `<span class="welcome-date">${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</span>`}</div><div class="inspection-grid"><div>${photoCard()}${!r ? `<div class="context-fields"><label>Part<select id="part-select"><option${state.part === "Door panel" ? " selected" : ""}>Door panel</option><option${state.part === "Chassis weld" ? " selected" : ""}>Chassis weld</option><option${state.part === "Wiring connector" ? " selected" : ""}>Wiring connector</option><option${state.part === "Panel alignment" ? " selected" : ""}>Panel alignment</option><option${state.part === "Other part" ? " selected" : ""}>Other part</option></select></label><label>Station<select id="station-select">${["Paint inspection", "Body assembly", "Electrical assembly", "Final assembly"].map((v) => `<option${state.station === v ? " selected" : ""}>${esc(v)}</option>`).join("")}</select></label></div><label class="notes-label">Anything the inspector should know? <span class="muted">optional</span><textarea id="inspection-notes" rows="2" maxlength="2000" placeholder="For example: noticed after the panel came out of finishing…">${esc(state.notes)}</textarea></label>` : r.notes ? `<p class="muted">Inspector notes: ${esc(r.notes)}</p>` : ""}</div>${assistant()}</div>${!r ? `<section class="library"><div class="section-heading"><h2>Or start with a sample</h2><span>Six synthetic photos · defects & references</span></div><div class="sample-list">${state.samples.map((s) => `<button class="sample ${state.sampleId === s.id && !state.upload ? "selected" : ""}" data-action="sample" data-id="${s.id}" aria-label="Use ${esc(s.subtitle)}"><img src="/samples/${s.file}" alt="${esc(s.subtitle)}" loading="lazy">${state.sampleId === s.id && !state.upload ? `<span class="selected-check">${icon("check")}</span>` : ""}<div class="sample-copy"><strong>${esc(s.title)}</strong><small>${esc(s.category)}</small></div></button>`).join("")}</div></section>` : ""}<div class="footer-note"><span>${icon("info")} Visual assistance, not an engineering sign-off. The inspector decides.</span><span class="status-line">${icon("server")} ${state.health?.agent?.connected ? (localMode() ? "Local development workspace" : "Connected to your edge workspace") : "Agent connection required"}</span></div>`;
}
function renderHistory() {
  const q = state.filter.toLowerCase();
  const rows = state.records.filter((r) =>
    [r.code, r.part, r.station, r.report?.title, r.decision?.inspector]
      .join(" ")
      .toLowerCase()
      .includes(q),
  );
  $("#page-content").innerHTML =
    `<div class="page-heading"><div><span class="eyebrow">EVERY PHOTO. EVERY DECISION.</span><h1>The inspection log.</h1><p>A shared record of what was seen, suggested and decided.</p></div><button class="small-button" data-action="new">${icon("plus")} New inspection</button></div><div class="log-toolbar"><label class="searchbox">${icon("search")}<input id="history-search" placeholder="Search parts, inspectors or inspection IDs…" value="${esc(state.filter)}" aria-label="Search inspections"></label><span class="muted">${rows.length} inspection${rows.length === 1 ? "" : "s"}</span></div>${rows.length ? `<table class="inspection-table"><thead><tr><th>Inspection</th><th>AI assessment</th><th>Station</th><th>Inspector decision</th><th>Created</th></tr></thead><tbody>${rows.map((r) => `<tr tabindex="0" data-action="open" data-id="${r.id}" aria-label="Open ${esc(r.code)}"><td><div class="table-part"><img src="/api/inspections/${r.id}/photo" alt="${esc(r.part)}"><div><strong>${esc(r.part)}</strong><small>${esc(r.code)}</small></div></div></td><td>${r.report ? `<span class="severity ${r.report.severity}">${esc({ low: "Low", moderate: "Moderate", high: "High", uncertain: "Uncertain" }[r.report.severity])}</span>` : `<span class="table-status">${esc(labels[r.status] ?? r.status)}</span>`}</td><td>${esc(r.station)}</td><td>${r.decision ? `${esc(labels[r.decision.disposition])}<br><span class="muted">${esc(r.decision.inspector)}</span>` : "Awaiting review"}</td><td>${date(r.createdAt)}<br><span class="muted">${clock(r.createdAt)}</span></td></tr>`).join("")}</tbody></table>` : `<div class="empty-state">${icon("history")}<h2>${q ? "No matching inspections." : "Your first inspection starts here."}</h2><p>${q ? "Try a part name, inspector or inspection ID." : `Assess a sample or upload a photo. The image, AI observations and your decision will be saved on ${locationLabel()}.`}</p><button class="button primary" data-action="new">Inspect a part ${icon("arrow")}</button></div>`}`;
}
function renderGuides() {
  $("#page-content").innerHTML =
    `<div class="page-heading"><div><span class="eyebrow">A COMMON STARTING POINT.</span><h1>A little context helps.</h1><p>Reference guidance the assistant uses to suggest practical next checks.</p></div></div><p class="guide-banner">These are illustrative demo guides. Use your actual engineering drawings, inspection procedures and acceptance criteria for production decisions.</p><div class="guide-grid">${state.guides.map((g) => `<article class="guide-card"><span class="reference">${esc(g.id)} · ${esc(g.part)}</span><h2>${esc(g.title)}</h2><p>${esc(g.text.replace("Illustrative demo guidance. ", ""))}</p></article>`).join("")}</div>`;
}
async function refreshHealth() {
  try {
    state.health = await api("/status");
  } catch {
    state.health = { agent: { connected: false } };
  }
  const b = $(".connection");
  if (b)
    b.innerHTML = `<span class="dot ${state.health?.agent?.connected ? "live" : "offline"}"></span>${esc(connectionLabel())}`;
}
async function loadHistory() {
  try {
    state.records = (await api("/inspections")).inspections;
  } catch {
    state.records = [];
  }
  if (state.page === "history") renderHistory();
}
function watch(record) {
  state.source?.close();
  if (!record?.id) return;
  const source = new EventSource(`/api/inspections/${record.id}/events`);
  state.source = source;
  source.onmessage = (e) => {
    const update = JSON.parse(e.data);
    if (state.current?.id !== update.id) return;
    const hadReport = !!state.current.report;
    state.current = update;
    if (state.page === "inspect") {
      const old = $(".assistant-card");
      if (old) old.outerHTML = assistant();
      if (hadReport !== !!update.report) {
        const card = $(".photo-card");
        if (card) card.outerHTML = photoCard();
      }
      const svg = $(".annotations");
      if (svg) svg.outerHTML = annotations();
    }
    const i = state.records.findIndex((r) => r.id === update.id);
    if (i >= 0) state.records[i] = update;
    else state.records.unshift(update);
  };
  source.onerror = () => {
    const label = $(".live-tag");
    if (label) label.textContent = "RECONNECTING";
  };
}
async function openInspection(id) {
  try {
    const r = await api("/inspections/" + id);
    state.current = r;
    state.page = "inspect";
    history.replaceState(null, "", `#inspection/${id}`);
    shell();
    watch(r);
  } catch (e) {
    toast(e.message);
  }
}
function newInspection() {
  state.source?.close();
  state.source = null;
  state.current = null;
  state.page = "inspect";
  history.replaceState(null, "", "#inspect");
  shell();
}
async function analyze() {
  if (state.submitting) return;
  state.notes = $("#inspection-notes")?.value ?? state.notes;
  state.part = $("#part-select")?.value ?? state.part;
  state.station = $("#station-select")?.value ?? state.station;
  state.submitting = true;
  renderInspection();
  try {
    const body = {
      requestId: crypto.randomUUID(),
      part: state.part,
      station: state.station,
      notes: state.notes,
      ...(state.upload
        ? {
            photo: {
              name: state.upload.name,
              mediaType: state.upload.mediaType,
              data: state.upload.data,
            },
          }
        : { sampleId: state.sampleId }),
    };
    const r = await api("/inspections", { method: "POST", body });
    state.current = r;
    history.replaceState(null, "", `#inspection/${r.id}`);
    state.submitting = false;
    renderInspection();
    watch(r);
    void loadHistory();
  } catch (e) {
    state.submitting = false;
    renderInspection();
    toast(e.message);
  }
}
function connection() {
  const health = state.health;
  $("#connection-content").innerHTML =
    `<div class="connection-row"><span>Application interface</span><strong>This workstation</strong></div><div class="connection-row"><span>Agent sessions</span><strong>${health?.agent?.connected ? (localMode() ? "This computer · connected" : "Edge server · connected") : "Connection required"}</strong></div><div class="connection-row"><span>Photos & inspection history</span><strong>${localMode() ? "Local data directory" : "Edge server storage"}</strong></div><div class="connection-row"><span>Vision inference</span><strong>OpenAI cloud${health?.agent?.model ? " · " + esc(health.agent.model) : ""}</strong></div><div class="connection-row"><span>Agent runtime</span><strong>OpenWorker Harness SDK</strong></div><div class="mode-explanation">${localMode() ? "The app and managed harness run on this computer." : "The interface runs here; the agent session runs on the edge server."} Photos and context are sent to OpenAI for vision analysis. Model credentials stay off the browser. This is not offline inference.</div>`;
  $("#connection-dialog").showModal();
}
function review() {
  if (!state.current?.report) return;
  const f = $("#review-form");
  f.elements.disposition.value =
    state.current.decision?.disposition ??
    { hold: "hold", rework: "rework", no_visible_concern: "inspect" }[
      state.current.report.recommendation
    ] ??
    "inspect";
  f.elements.inspector.value =
    state.current.decision?.inspector ??
    sessionStorage.getItem("inspector-name") ??
    "";
  f.elements.note.value = "";
  state.reviewRevision = state.current.revision;
  $("#review-dialog").showModal();
}
document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  switch (action) {
    case "connection":
      connection();
      break;
    case "close-dialog":
      target.closest("dialog").close();
      break;
    case "sample": {
      const s = state.samples.find((s) => s.id === target.dataset.id);
      if (!s) return;
      if (state.upload) URL.revokeObjectURL(state.upload.url);
      state.sampleId = s.id;
      state.upload = null;
      state.part = s.part;
      state.station = s.station;
      state.notes = "";
      newInspection();
      break;
    }
    case "upload":
      $("#photo-input").click();
      break;
    case "analyze":
      await analyze();
      break;
    case "new":
      newInspection();
      break;
    case "open":
      await openInspection(target.dataset.id);
      break;
    case "zoom":
      $("#photo-stage").classList.toggle("zoomed");
      break;
    case "annotations":
      state.annotations = !state.annotations;
      renderInspection();
      break;
    case "finding":
      state.annotations = true;
      renderInspection();
      break;
    case "review":
      review();
      break;
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $(".photo-stage.zoomed")?.classList.remove("zoomed");
  if (e.key === "Enter" && e.target.matches("tr[data-action]"))
    e.target.click();
});
document.addEventListener("click", (e) => {
  if (e.target.closest(".photo-stage.zoomed"))
    $("#photo-stage")?.classList.remove("zoomed");
});
document.addEventListener("input", (e) => {
  if (e.target.id === "inspection-notes") state.notes = e.target.value;
  if (e.target.id === "history-search") {
    const position = e.target.selectionStart;
    state.filter = e.target.value;
    renderHistory();
    $("#history-search").focus();
    $("#history-search").setSelectionRange(position, position);
  }
});
document.addEventListener("change", (e) => {
  if (e.target.id === "part-select") state.part = e.target.value;
  if (e.target.id === "station-select") state.station = e.target.value;
});
$("#photo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (
    !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
    file.size > 8 * 1024 * 1024
  ) {
    toast("Choose a JPEG, PNG or WebP photo under 8 MB.");
    e.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    if (state.upload) URL.revokeObjectURL(state.upload.url);
    state.upload = {
      name: file.name,
      mediaType: file.type,
      data: String(reader.result).split(",")[1],
      url: URL.createObjectURL(file),
    };
    state.notes = "";
    newInspection();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
});
$("#review-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target,
    button = f.querySelector("[type=submit]");
  button.disabled = true;
  try {
    const body = {
      disposition: f.elements.disposition.value,
      inspector: f.elements.inspector.value,
      note: f.elements.note.value,
      revision: state.reviewRevision,
    };
    const r = await api(`/inspections/${state.current.id}/decision`, {
      method: "POST",
      body,
    });
    sessionStorage.setItem("inspector-name", body.inspector);
    state.current = r;
    $("#review-dialog").close();
    renderInspection();
    toast(`Inspector decision saved on ${locationLabel()}.`);
    void loadHistory();
  } catch (err) {
    toast(err.message);
  } finally {
    button.disabled = false;
  }
});
document.addEventListener("submit", async (e) => {
  if (e.target.id !== "followup-form") return;
  e.preventDefault();
  const input = e.target.elements.question,
    question = input.value.trim();
  if (!question) return;
  input.disabled = true;
  try {
    await api(`/inspections/${state.current.id}/followups`, {
      method: "POST",
      body: { question, requestId: crypto.randomUUID() },
    });
    input.value = "";
  } catch (err) {
    toast(err.message);
  } finally {
    input.disabled = false;
  }
});
window.addEventListener("hashchange", async () => {
  const hash = location.hash.slice(1);
  if (hash.startsWith("inspection/")) return openInspection(hash.split("/")[1]);
  state.page = ["history", "guides"].includes(hash) ? hash : "inspect";
  if (state.page === "inspect" && state.current) return newInspection();
  shell();
  if (state.page === "history") await loadHistory();
});
for (const type of ["dragenter", "dragover"])
  document.addEventListener(type, (e) => {
    if (!e.dataTransfer?.types.includes("Files")) return;
    e.preventDefault();
    document.body.classList.add("dragging");
  });
document.addEventListener("dragleave", (e) => {
  if (!e.relatedTarget) document.body.classList.remove("dragging");
});
document.addEventListener("drop", (e) => {
  e.preventDefault();
  document.body.classList.remove("dragging");
  if (e.dataTransfer?.files.length) {
    $("#photo-input").files = e.dataTransfer.files;
    $("#photo-input").dispatchEvent(new Event("change"));
  }
});
const catalog = await (await fetch("/catalog.json")).json();
state.samples = catalog.samples;
state.guides = catalog.guides;
state.page = ["history", "guides"].includes(location.hash.slice(1))
  ? location.hash.slice(1)
  : "inspect";
shell();
await refreshHealth();
shell();
if (location.hash.startsWith("#inspection/"))
  await openInspection(location.hash.split("/")[1]);
void loadHistory();

document.addEventListener(
  "load",
  (e) => {
    if (e.target.matches?.("#photo-stage > img")) {
      const svg = $(".annotations");
      if (svg) svg.outerHTML = annotations();
    }
  },
  true,
);
