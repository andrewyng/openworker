import { esc } from "./observability-data.js";
import { mountPolicyCoverage } from "./policy-coverage.js";

const endpoint = "/v1/compliance";
const day = 86_400_000;
const viewNames = { decisions: "Decisions", agents: "Agent overview", audit: "Admin audit" };
const outcomes = { blocked: "Blocked", denied: "Denied", redacted: "Redacted", unavailable: "Unavailable", matched: "Matched", approved: "Approved" };
const effects = {
  tool_blocked: "Tool did not run",
  tool_started: "Tool dispatch recorded · result unconfirmed",
  tool_result_recorded: "Tool result recorded",
  request_blocked: "Request blocked before admission",
  model_blocked: "Model request blocked",
  response_withheld: "Response withheld",
  result_withheld: "Tool result withheld",
  turn_admitted: "Turn admitted",
  unconfirmed: "Execution unconfirmed",
};
const sources = {
  guardrail: "Guardrail", human: "Human approval", reviewer: "Automatic approval",
  app_policy: "App policy", session_grant: "Session grant", workspace_read_grant: "Workspace read grant",
  hard_policy: "Tool policy", bypass: "Bypass mode",
};
const auditActions = {
  "credential.bootstrap": "Bootstrap access",
  "credential.issue": "Issue credential",
  "credential.revoke": "Revoke credential",
  "credential.rotate": "Rotate credential",
  "credential.recovery": "Recover administrator",
  "credential.manage": "Manage credentials",
  "service.configure": "Change service configuration",
  "release.publish": "Publish release",
  "release.activate": "Activate release",
  "compliance.evidence.download": "Export evidence",
  "compliance.evidence.forward": "Forward evidence",
  "compliance.content.read": "Access session content",
};
const title = row => row.tool
  ? row.tool.replaceAll("__", " · ").replaceAll("_", " ").replace(/^[a-z]/, value => value.toUpperCase())
  : row.policy || "Policy decision";
const label = value => value?.replaceAll("_", " ") || "—";
const appLabel = value => value?.replace(/^app_/, "") || "—";
const formatter = new Intl.DateTimeFormat(undefined, { timeZone: "UTC", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const date = ms => formatter.format(new Date(ms));
const opt = (key, value) => `<option value="${esc(key)}">${esc(value)}</option>`;
const pill = outcome => `<span class="cp-pill" data-tone="${esc(outcome)}"><i></i>${esc(outcomes[outcome] || label(outcome))}</span>`;
function hash(view, input = {}) {
  const query = new URLSearchParams(Object.entries(input).filter(([,value]) => value));
  return `#compliance${view === "decisions" ? "" : "/" + view}${query.size ? "?" + query : ""}`;
}
const apiPath = (view, input) => `${endpoint}/${view}?${new URLSearchParams(input)}`;

function rows(view, data, selected, icon) {
  if (view === "agents") return data.map(row => {
    const name = row.release || row.agent || "Before session creation";
    const href = hash("decisions", { app:row.app, agent:row.release || row.agent, days:row.days });
    return `<tr><td><a class="cp-row-action" href="${esc(href)}">${icon("agents")}<span><strong>${esc(name)}</strong><small>${esc(appLabel(row.app))}</small></span></a></td>
      <td>${row.turns}</td><td>${row.decisions || "No decisions recorded"}</td><td>${row.blocked}</td><td>${row.redacted}</td><td>${row.unavailable}</td></tr>`;
  }).join("");
  if (view === "audit") return data.map(row => `<tr>
    <td><strong>${esc(auditActions[row.action] || row.action)}</strong></td>
    <td>${esc(row.actor || "System")}</td><td>${esc(appLabel(row.app))}</td>
    <td>${esc(row.target || "—")}</td><td>${esc(label(row.outcome))}</td><td><time>${esc(date(row.occurred_ms))}</time></td></tr>`).join("");
  return data.map((row,index) => `<tr data-row="${index}" data-current="${row.id === selected}">
    <td><button type="button" class="cp-row-action" aria-pressed="${row.id === selected}">${icon(row.source === "guardrail" ? "shield" : row.outcome === "approved" ? "check" : "key")}<span><strong>${esc(title(row))}</strong><small>${esc(sources[row.source] || label(row.source))}<span class="cp-mobile-app"> · ${esc(appLabel(row.app))}</span></small></span></button></td>
    <td class="cp-app-cell">${esc(appLabel(row.app))}</td>
    <td>${pill(row.outcome)}</td><td><time>${esc(date(row.occurred_ms))}</time></td>
    </tr>`).join("");
}

function inspector(row, icon, canTrace) {
  const trace = row.session_id && (row.turn_id
    ? `#observability/${encodeURIComponent(row.session_id)}/${encodeURIComponent(row.turn_id)}`
    : `#sessions/${encodeURIComponent(row.session_id)}`);
  const facts = [
    ["App", row.app], ["Agent release", row.release || row.agent],
    ["Policy", row.policy], ["Tool", row.tool], ["Policy version", row.policy_version],
    ["Policy digest", row.policy_digest],
    ["Decision source", sources[row.source] || label(row.source)], ["Stage", label(row.phase)],
    ["Session", row.session_id], ["Turn", row.turn_id],
  ];
  const session = row.session_id && `#sessions/${encodeURIComponent(row.session_id)}`;
  return `<div class="cp-detail-top">${pill(row.outcome)}<button class="icon-button" aria-label="Close decision details" type="button" data-close>${icon("close")}</button></div>
    <h2>${esc(title(row))}</h2><time>${esc(date(row.occurred_ms))} UTC</time>
    <div class="cp-effect">${icon("activity")}<strong>${esc(effects[row.effect] || effects.unconfirmed)}</strong></div>
    <dl>${facts.filter(([,value]) => value).map(([key,value]) => `<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
    ${trace && canTrace ? `<div class="cp-source"><a class="button" href="${trace}">${icon("external")}${row.turn_id ? "Open source trace" : "Open session"}</a>
      ${row.turn_id ? `<a class="cp-session-link" href="${session}">Open session</a>` : ""}</div>` : ""}`;
}

export function mountComplianceCatalog(root, { api, icon, view, params, canExport, canTrace, canForward, canCoverage }) {
  const lifetime = new AbortController();
  const { signal } = lifetime;
  const views = canCoverage ? { ...viewNames, coverage: "Policy coverage" } : viewNames;
  const tab = Object.hasOwn(views, view) ? view : "decisions";
  const heading = `<header class="cp-heading"><div><h1>Compliance</h1><p>Policy decisions and agent activity</p></div></header>
    <nav class="cp-nav" aria-label="Compliance views">${Object.entries(views).map(([name,label]) =>
      `<a href="${hash(name)}" ${tab === name ? 'aria-current="page"' : ""}>${icon(name === "decisions" || name === "coverage" ? "shield" : name === "agents" ? "layers" : "file")}${label}</a>`).join("")}</nav>`;
  if (tab === "coverage") {
    root.innerHTML = `<section class="cp-page">${heading}<div class="cp-panel" data-coverage></div></section>`;
    mountPolicyCoverage(root.querySelector("[data-coverage]"), { api, params, signal });
    return () => lifetime.abort();
  }
  const isDecisions = tab === "decisions";
  const isAgents = tab === "agents";
  const initialDay = ["1","7","30"].includes(params.get("days")) ? params.get("days") : "7";
  const defaultApp = params.get("app") || "";
  const headers = isDecisions ? ["Event","App","Decision","Time UTC"] : isAgents
    ? ["Agent release","Turns","Decisions","Blocked / denied","Redacted","Unavailable"]
    : ["Action","Actor","App","Target","Outcome","Time UTC"];
  root.innerHTML = `<section class="cp-page">${heading}
    <section class="cp-panel" aria-label="${viewNames[tab]}">
      <form class="cp-filters">
        <div class="cp-toolbar">
          ${!isAgents ? `<label class="cp-search">${icon("search")}<span class="ev-visually-hidden">Search ${isDecisions ? "decisions" : "audit"}</span>
            <input type="search" name="q" maxlength="160" placeholder="${isDecisions ? "Search events, policies, or sessions…" : "Search actions or actors…"}" /></label>
            <select name="app" aria-label="App"><option value="">All apps</option>${defaultApp ? opt(defaultApp, appLabel(defaultApp)) : ""}</select>` : ""}
          ${isDecisions ? `<select name="outcome" aria-label="Outcome"><option value="">All outcomes</option>${Object.entries(outcomes).map(([key,label]) => opt(key,label)).join("")}</select>` : ""}
          <select name="days" aria-label="Time range">${[["1","Last 24 hours"],["7","Last 7 days"],["30","Last 30 days"]].map(([key,value]) => opt(key,value)).join("")}</select>
          ${!isAgents ? '<button class="button" type="submit">Apply</button>' : ""}
          ${isDecisions && canExport ? `<button class="button cp-export" type="button" data-export>${icon("download")}Export evidence</button>` : ""}
        </div>
        ${isDecisions ? `<details class="cp-advanced"><summary>More filters</summary>
          <div><label>Agent or release<input name="agent" maxlength="128" /></label>
          <label>Policy ID<input name="policy" maxlength="128" /></label></div></details>` : ""}
      </form>
      <div class="cp-split">
        <div class="cp-list"><div class="cp-table-wrap"><table class="cp-table" aria-label="${viewNames[tab]}">
          <thead><tr>${headers.map(h => `<th scope="col" class="${isDecisions && h === "App" ? "cp-app-cell" : ""}">${h}</th>`).join("")}</tr></thead>
          <tbody></tbody></table></div>
          <div class="cp-footer"><span role="status"></span><div><button class="icon-button" data-page="-1" aria-label="Previous page" type="button">${icon("back")}</button><button class="icon-button" data-page="1" aria-label="Next page" type="button">${icon("arrow")}</button></div></div>
          <p class="cp-message" data-message role="alert" hidden></p>
        </div>
        <aside class="cp-detail" aria-label="Selected decision" aria-live="polite" hidden></aside>
      </div>
    </section></section>`;
  if (isDecisions && canForward) {
    root.querySelector(".cp-panel").insertAdjacentHTML("beforeend", `<details class="cp-delivery" hidden>
      <summary>Evidence delivery</summary>
      <div class="cp-toolbar"><label>Destination <select data-sink aria-label="Evidence destination"></select></label>
      <button type="button" class="button" data-forward>Forward current selection</button>
      <button type="button" class="button" data-delivery-refresh>Refresh delivery status</button></div>
      <div data-deliveries></div>
      </details>`);
  }
  const form = root.querySelector("form");
  const tbody = root.querySelector("tbody");
  const detail = root.querySelector("aside");
  const message = root.querySelector("[data-message]");
  const info = root.querySelector("[role=status]");
  const previous = root.querySelector('[data-page="-1"]');
  const next = root.querySelector('[data-page="1"]');
  const split = root.querySelector(".cp-split");
  const exportButton = root.querySelector("[data-export]");
  const delivery = root.querySelector(".cp-delivery");
  const saved = history.state?.complianceList;
  const restored = saved?.hash === location.hash && Number.isSafeInteger(saved.until)
    && saved.until > Number(initialDay) * day && saved.until <= Date.now() + 60_000
    && Array.isArray(saved.cursors) && saved.cursors[0] === null && saved.cursors.slice(1).every(cursor =>
      isDecisions ? Number.isSafeInteger(cursor?.before_us) && typeof cursor?.before_id === "string"
        : isAgents ? typeof cursor === "string" : Number.isSafeInteger(cursor))
    ? saved : null;
  let data = [], selected = restored?.closed ? "" : params.get("decision"), filters = {}, appliedDays = initialDay, cursors = [null], nextCursor = null;
  let generation = 0;
  for (const key of ["app", "agent", "policy", "outcome", "q"]) {
    if (form.elements[key] && params.has(key)) form.elements[key].value = params.get(key);
  }
  form.elements.days.value = initialDay;
  if (isDecisions && (params.has("agent") || params.has("policy"))) form.querySelector("details").open = true;

  function remember() {
    const { from_ms, until_ms, ...current } = filters;
    current.days = appliedDays;
    if (selected) current.decision = selected;
    const fragment = hash(tab, current);
    history.replaceState({ ...history.state, complianceList: {
      hash: fragment, until: until_ms, cursors, closed: selected === "",
    } }, "", fragment);
  }

  function showMessage(value, error = true) {
    message.textContent = value;
    message.hidden = !value;
    message.classList.toggle("cp-error", error);
  }

  function paint() {
    const active = isDecisions && data.find(row => row.id === selected);
    tbody.innerHTML = data.length ? rows(tab, data, selected, icon)
      : `<tr><td colspan="${headers.length}" class="cp-empty">No ${isAgents ? "agent activity" : "matching records"} in this time range.</td></tr>`;
    detail.hidden = !active;
    split.classList.toggle("cp-inspecting", Boolean(active));
    detail.innerHTML = active ? inspector(active, icon, canTrace) : "";
  }

  async function loadPage(save = false) {
    const current = ++generation;
    const cursor = cursors.at(-1);
    let query = filters;
    if (cursor) query = isDecisions ? {...filters, ...cursor}
      : {...filters, [isAgents ? "after" : "before"]:cursor};
    info.textContent = "Loading…";
    showMessage("");
    previous.disabled = next.disabled = true;
    if (exportButton) exportButton.disabled = true;
    try {
      const response = await api(apiPath(tab, query), "GET", undefined, undefined, signal);
      if (signal.aborted || current !== generation) return;
      data = response.records || response.agents.map(row => ({...row, days:form.elements.days.value}));
      if (!isAgents) {
        const control = form.elements.app;
        const picked = control.value;
        const apps = new Set([...response.apps, ...data.map(row => row.app).filter(Boolean), ...(picked ? [picked] : [])]);
        control.replaceChildren(new Option("All apps", ""), ...[...apps].sort().map(app => new Option(appLabel(app),app)));
        control.value = picked;
      }
      nextCursor = response.next;
      if (isDecisions && selected !== "" && !data.some(row => row.id === selected)) selected = data[0]?.id ?? "";
      paint();
      info.textContent = `Page ${cursors.length} · ${data.length} shown`;
      previous.disabled = cursors.length <= 1;
      next.disabled = !nextCursor;
      if (exportButton) exportButton.disabled = !data.length;
      if (save) remember();
    } catch {
      if (!signal.aborted && current === generation) {
        data = [];
        selected = "";
        paint();
        info.textContent = "";
        showMessage("Couldn’t load this view. Check your access or try another filter.");
      }
    }
  }

  function apply(resume = null) {
    const entered = Object.fromEntries(new FormData(form));
    const until = resume?.until ?? Date.now() + 1;
    appliedDays = entered.days;
    filters = { until_ms:until, from_ms:until - Number(appliedDays) * day };
    for (const key of ["app","agent","outcome","policy","q"]) {
      if (entered[key]?.trim()) filters[key] = entered[key].trim();
    }
    cursors = resume?.cursors ?? [null];
    void loadPage();
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!isDecisions || selected !== "") selected = null;
    apply();
    remember();
  }, { signal });
  if (isAgents) form.elements.days.addEventListener("change", () => { apply(); remember(); }, { signal });
  if (delivery) {
    const destination = delivery.querySelector("[data-sink]");
    const forward = delivery.querySelector("[data-forward]");
    const output = delivery.querySelector("[data-deliveries]");
    let pendingForward = null;
    async function loadDelivery() {
      const { destinations, deliveries } = await api(`${endpoint}/delivery`, "GET", undefined, undefined, signal);
      if (signal.aborted) return;
      delivery.hidden = !destinations.length && !deliveries.length;
      const picked = destination.value;
      destination.replaceChildren(...destinations.map(name => new Option(name,name)));
      if (destinations.includes(picked)) destination.value = picked;
      forward.disabled = !destinations.length;
      output.innerHTML = deliveries.length ? `<ul>${deliveries.map(receipt => `<li>
        <strong>${esc(receipt.sink)}</strong><span>${receipt.records} records · ${esc(receipt.actor)} · ${esc(date(receipt.created_ms))} UTC</span>
        <span>${esc(({queued:"Queued",delivered:"Delivered",retry_scheduled:"Retry scheduled",destination_unavailable:"Destination unavailable"})[receipt.status] || receipt.status)}
          ${receipt.status === "retry_scheduled" ? `· ${esc(date(receipt.next_ms))} UTC` : ""}</span></li>`).join("")}</ul>`
        : '<p>No recent deliveries.</p>';
    }
    void loadDelivery().catch(() => {});
    delivery.querySelector("[data-delivery-refresh]").addEventListener("click", () => {
      void loadDelivery().catch(() => { if (!signal.aborted) showMessage("Couldn’t load delivery status."); });
    }, { signal });
    forward.addEventListener("click", async () => {
      forward.disabled = true;
      try {
        const body = { sink:destination.value, selection:filters };
        const selection = JSON.stringify(body);
        if (pendingForward?.selection !== selection) pendingForward = { selection, key:crypto.randomUUID() };
        const receipt = await api(`${endpoint}/delivery`,"POST",body,pendingForward.key,signal);
        pendingForward = null;
        if (signal.aborted) return;
        showMessage(`Queued ${receipt.records} records for delivery.`,false);
        await loadDelivery().catch(() => {});
      } catch (error) {
        if (!signal.aborted) {
          const rejected = error.status >= 400 && error.status < 500;
          if (rejected) pendingForward = null;
          showMessage(rejected ? "Couldn’t queue delivery. Check the destination and selection."
            : "Delivery admission is unconfirmed. Retry the same selection to check it.");
        }
      } finally {
        if (!signal.aborted) forward.disabled = !destination.value;
      }
    }, { signal });
  }
  split.addEventListener("click", event => {
    const row = event.target.closest("[data-row]");
    const control = event.target.closest("button");
    if (row) {
      selected = data[Number(row.dataset.row)].id;
      paint();
      remember();
      tbody.querySelector('[data-current="true"] button')?.focus({ preventScroll:true });
      if (window.matchMedia("(max-width: 750px)").matches) detail.scrollIntoView({ block:"nearest" });
    } else if (control?.hasAttribute("data-close")) {
      const index = data.findIndex(row => row.id === selected);
      selected = "";
      paint();
      remember();
      tbody.querySelector(`[data-row="${index}"] button`)?.focus({preventScroll:true});
    } else if (control?.dataset.page === "-1" && cursors.length > 1) {
      cursors.pop();
      void loadPage(true);
    } else if (control?.dataset.page === "1" && nextCursor) {
      cursors.push(nextCursor);
      void loadPage(true);
    }
  }, { signal });
  exportButton?.addEventListener("click", async () => {
    exportButton.disabled = true;
    showMessage("");
    try {
      const { records } = await api(`${endpoint}/export`, "POST", filters, crypto.randomUUID(), signal);
      if (signal.aborted) return;
      const file = new Blob(records.map(row => JSON.stringify(row) + "\n"), {type:"application/x-ndjson"});
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = "openworker-compliance-evidence.jsonl";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
      showMessage(`Exported ${records.length} decision${records.length === 1 ? "" : "s"}.`, false);
    } catch (error) {
      if (!signal.aborted) showMessage(error.status === 409 || error.status === 429
        ? "Export unavailable. Use a shorter time range or narrower filters."
        : "Couldn’t export evidence. Check your access and try again.");
    } finally {
      if (!signal.aborted) exportButton.disabled = !data.length;
    }
  }, { signal });
  apply(restored);
  return () => lifetime.abort();
}
