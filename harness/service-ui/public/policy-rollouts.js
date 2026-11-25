import { esc } from "./observability-data.js";

const modes = { shadow: "Shadow", enforce: "Enforce", inherit: "Use cluster policy" };
const helpers = {
  shadow: "Record what this check would block and let the request proceed past this check.",
  enforce: "Apply this check’s verdict on the next affected operation, including in active sessions.",
  inherit: "Use the cluster’s existing activation and enforcement settings for this app.",
};
const path = "/v1/configuration";
const option = (value, name) => `<option value="${esc(value)}">${esc(name)}</option>`;
const checkLabel = check => check.replaceAll("-", " ").replace(/^[a-z]/, first => first.toUpperCase());

export function mountPolicyRollouts(root, { api, icon, checks, signal }) {
  root.innerHTML = `<div class="cp-rollouts">
    <div class="cp-rollout-heading"><div><h2>Policy rollouts</h2>
      <p>Try a scanner policy on one app, review the findings, then enforce it when you’re ready.</p></div>
      <button type="button" class="button" data-add disabled>${icon("plus")} Add rollout</button></div>
    <form class="cp-rollout-form" hidden>
      <div class="cp-rollout-fields">
        <label>App ID<input name="app" list="rollout-apps" placeholder="app_finance" required maxlength="64" pattern="app_[a-z0-9\\-]+" /><datalist id="rollout-apps"></datalist></label>
        <label>Scanner<select name="check">${checks.map(id => option(id, checkLabel(id))).join("")}</select></label>
        <label>Mode<select name="mode">${Object.entries(modes).map(([id, label]) => option(id, label)).join("")}</select></label>
      </div>
      <p class="cp-rollout-help" aria-live="polite"></p>
      <div class="cp-toolbar"><button class="button primary" type="submit">Save rollout</button>
      <button class="button" type="button" data-cancel>Cancel</button></div>
    </form>
    <div data-rollout-list></div><p class="cp-message" role="status">Loading rollouts…</p>
    </div>`;
  const add = root.querySelector("[data-add]");
  const form = root.querySelector("form");
  const list = root.querySelector("[data-rollout-list]");
  const status = root.querySelector("[role=status]");
  const { app, check, mode } = form.elements;
  const save = form.querySelector('[type="submit"]');
  let current;
  let editing = null;
  let entries = [];

  function paint() {
    entries = Object.entries(current.configuration.appCheckModes || {}).flatMap(([app, settings]) =>
      Object.entries(settings).map(([check, mode]) => ({ app, check, mode })));
    list.innerHTML = entries.length ? `<div class="cp-table-wrap"><table class="cp-table" aria-label="Policy rollouts">
      <thead><tr><th scope="col">App</th><th scope="col">Scanner</th><th scope="col">Mode</th><th scope="col">Actions</th></tr></thead>
      <tbody>${entries.map((row, index) => {
        const forbidden = current.configuration.integrationPolicies?.[row.check] === "forbidden";
        const review = new URLSearchParams({ app:row.app, days:"7" });
        return `<tr><td><strong>${esc(row.app)}</strong></td><td>${esc(checkLabel(row.check))}</td>
          <td><span class="cp-pill" data-tone="${esc(forbidden ? "unavailable" : row.mode)}"><i></i>${esc(forbidden ? "Forbidden by cluster" : modes[row.mode])}</span></td>
          <td><div class="cp-rollout-actions"><a href="#compliance?${esc(review)}">Review decisions</a>
          <button type="button" class="button" data-edit="${index}" aria-label="Edit ${esc(checkLabel(row.check))} for ${esc(row.app)}">Edit</button></div></td></tr>`;
      }).join("")}</tbody></table></div>`
      : '<p class="cp-rollout-empty">No app rollouts yet.</p>';
  }

  function describe() {
    const forbidden = current.configuration.integrationPolicies?.[check.value] === "forbidden";
    form.querySelector(".cp-rollout-help").textContent = forbidden && mode.value !== "inherit"
      ? "The cluster forbids this scanner. This rollout takes effect only if that policy changes."
      : helpers[mode.value];
  }

  function open(row) {
    form.reset();
    editing = row;
    app.readOnly = Boolean(row);
    check.disabled = Boolean(row);
    if (row) {
      app.value = row.app;
      check.value = row.check;
      mode.value = row.mode;
    }
    form.hidden = false;
    describe();
    (row ? mode : app).focus();
  }

  add.addEventListener("click", () => open(null), { signal });
  list.addEventListener("click", event => {
    const button = event.target.closest("[data-edit]");
    if (button && entries[Number(button.dataset.edit)]) open(entries[Number(button.dataset.edit)]);
  }, { signal });
  root.querySelector("[data-cancel]").addEventListener("click", () => {
    form.hidden = true;
    add.focus();
  }, { signal });
  for (const input of [check, mode]) input.addEventListener("change", describe, { signal });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const appId = editing?.app ?? app.value;
    const checkId = editing?.check ?? check.value;
    const selected = mode.value;
    const configuration = structuredClone(current.configuration);
    configuration.appCheckModes ??= {};
    const appModes = configuration.appCheckModes[appId] ??= {};
    if (selected === "inherit") {
      delete appModes[checkId];
      if (!Object.keys(appModes).length) delete configuration.appCheckModes[appId];
    } else {
      appModes[checkId] = selected;
    }
    save.disabled = add.disabled = true;
    status.textContent = "Saving…";
    try {
      current = await api(path, "PUT", { expectedRevision:current.revision, configuration }, crypto.randomUUID(), signal);
      if (signal.aborted) return;
      paint();
      form.hidden = true;
      status.textContent = "Rollout saved.";
      add.focus();
    } catch (error) {
      if (signal.aborted) return;
      status.textContent = error.status === 409
        ? "Configuration changed while you were editing. Reload this page and review the current settings."
        : error.status === 400 ? "Use an existing app ID and a configured scanner." : "Couldn’t save the rollout.";
    } finally {
      save.disabled = add.disabled = false;
    }
  }, { signal });

  void api("/v1/credentials", "GET", undefined, undefined, signal).then(({credentials}) => {
    if (signal.aborted) return;
    const apps = new Set(credentials.filter(row => row.spec?.role === "app").map(row => row.spec.principal));
    root.querySelector("#rollout-apps").replaceChildren(...[...apps].sort().map(app => new Option(app)));
  }).catch(() => { /* The app ID can also be entered directly. */ });

  void api(path, "GET", undefined, undefined, signal).then(value => {
    if (signal.aborted) return;
    current = value;
    paint();
    add.disabled = false;
    status.textContent = "";
  }).catch(() => {
    if (!signal.aborted) status.textContent = "Couldn’t load the rollout settings.";
  });
}
