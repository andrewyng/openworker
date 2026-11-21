import { esc } from "./observability-data.js";

const endpoint = "/v1/evals";
const id = encodeURIComponent;
const traceLink = ({ session_id, turn_id }) =>
  `#observability/${id(session_id)}/${id(turn_id)}`;
const count = (n, item) => `${n} ${item}${n === 1 ? "" : "s"}`;
const date = (ms) => new Date(ms).toLocaleString(undefined, {
  dateStyle: "medium", timeStyle: "short",
});
const pathFor = (dataset) => `${endpoint}/datasets/${id(dataset)}`;
const textBlock = (text) => text == null
  ? '<p class="ev-muted">Unavailable for capture.</p>'
  : `<pre>${esc(text)}</pre>`;
const checkLabel = ({ comparison }) => comparison === "equals" ? "Exact match" : "Contains text";

function errorText(error) {
  if (error?.status === 403) return "Your role does not have access to this evaluation.";
  if (error?.status === 404) return "This evaluation is unavailable. Check that cluster observability is enabled and that you have access.";
  if (error?.status === 400) return "Check the case details. Capture requires a completed turn and a valid expectation.";
  if (error?.status === 409) return "The request conflicts with a saved change. Refresh and try again.";
  return "Couldn’t complete the request. Try again.";
}

export async function captureEvalCase(root, api, turn, signal) {
  if (root.querySelector(".ev-dialog")) return;
  const dialog = document.createElement("dialog");
  dialog.className = "ev-dialog";
  dialog.setAttribute("aria-labelledby", "ev-capture-title");
  dialog.innerHTML = `
    <header class="ev-dialog-header">
      <div><h2 id="ev-capture-title">Add to dataset</h2><p>Save the final response and an expectation to check.</p></div>
      <button class="ev-close" type="button" aria-label="Close">×</button>
    </header>
    <div class="ev-dialog-body"><p class="ev-muted" role="status">Loading evidence…</p></div>`;
  root.append(dialog);
  const cancel = () => dialog.close();
  const remove = () => {
    signal.removeEventListener("abort", cancel);
    dialog.remove();
  };
  signal.addEventListener("abort", cancel, { once: true });
  dialog.addEventListener("close", remove, { once: true });
  dialog.querySelector(".ev-close").addEventListener("click", cancel);
  dialog.showModal();
  const body = dialog.querySelector(".ev-dialog-body");
  try {
    const { session_id, turn_id } = turn;
    const evidence = await api(`${endpoint}/evidence/${id(session_id)}/${id(turn_id)}`, "GET", undefined, undefined, signal);
    if (!dialog.open || signal.aborted) return;
    const listing = await api(`${endpoint}/datasets`, "GET", undefined, undefined, signal);
    if (!dialog.open || signal.aborted) return;
    const names = listing.datasets.filter((dataset) => dataset.owner === evidence.owner);
    body.innerHTML = `
      <form class="ev-capture">
        <label>Dataset<input name="dataset" list="ev-dataset-names" maxlength="100" placeholder="Choose or name a dataset" required>
          <datalist id="ev-dataset-names">${names.map((d) => `<option value="${esc(d.name)}"></option>`).join("")}</datalist>
        </label>
        <label>Expected response<textarea name="expected" rows="3" maxlength="4000" placeholder="Enter the text the response should include" required></textarea></label>
        <details class="ev-disclosure">
          <summary>${evidence.output == null ? "Evidence unavailable" : "Review captured evidence"}</summary>
          <h3>Final response</h3>
          ${textBlock(evidence.output)}
          ${evidence.input == null ? "" : `<label class="ev-check"><input type="checkbox" name="include_input"> Include request in the saved case</label>
            <details class="ev-input-preview"><summary>Preview request</summary>${textBlock(evidence.input)}</details>`}
        </details>
        <details class="ev-disclosure">
          <summary>Case settings</summary>
          <label>Case name<input name="name" maxlength="100" placeholder="${esc(evidence.name || "Agent response")}"></label>
          <label>Text comparison<select name="comparison"><option value="contains">Contains expected text</option><option value="equals">Matches expected text exactly</option></select></label>
        </details>
        ${evidence.output == null ? '<p class="ev-muted">The evaluator will record an error for this case because the final response is unavailable.</p>' : ""}
        <p class="ev-error" role="alert"></p>
        <footer class="ev-actions">
          <button class="button" type="button" data-ev-cancel>Cancel</button>
          <button class="button primary" type="submit">Save case</button>
        </footer>
      </form>`;
    const form = body.querySelector("form");
    form.querySelector("[data-ev-cancel]").addEventListener("click", cancel);
    form.elements.dataset.focus();
    let previous;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector("[type=submit]");
      if (submit.disabled) return;
      const data = new FormData(form);
      const payload = {
        dataset: data.get("dataset").trim(),
        name: data.get("name").trim() || null,
        turn: { session_id, turn_id },
        include_input: data.has("include_input"),
        check: { expected: data.get("expected"), comparison: data.get("comparison") },
      };
      const snapshot = JSON.stringify(payload);
      if (previous?.snapshot !== snapshot) previous = { snapshot, key: crypto.randomUUID() };
      const error = form.querySelector(".ev-error");
      error.textContent = "";
      submit.disabled = true;
      submit.textContent = "Saving…";
      try {
        const saved = await api(`${endpoint}/cases`, "POST", payload, previous.key, signal);
        if (!dialog.open || signal.aborted) return;
        dialog.close();
        location.hash = `#evals/${id(saved.dataset_id)}`;
      } catch (failure) {
        if (!signal.aborted) error.textContent = errorText(failure);
      } finally {
        submit.disabled = false;
        submit.textContent = "Save case";
      }
    });
  } catch (error) {
    if (!signal.aborted && dialog.open) {
      body.innerHTML = `<p class="ev-error" role="alert">${esc(errorText(error))}</p>`;
    }
  }
}

function outcome(result) {
  if (result.status === "error") return ["error", "Error"];
  if (result.status === "skipped") return ["skipped", "Skipped"];
  return result.score?.value === true ? ["passed", "Passed"] : ["failed", "Failed"];
}

const pill = (kind, name) => `<span class="ev-pill ev-${kind}"><i></i>${esc(name)}</span>`;
const caseName = (entry) => esc(entry.turn.name || "Agent response");
const jobName = (job) => `Eval from ${date(job.created_ms)}`;
const jobHref = (dataset, job) => `#evals/${id(dataset)}/${id(job)}`;

function renderIndex(root, datasets) {
  root.innerHTML = `
    <header class="ev-heading">
      <div><h1>Evals</h1><p>Score captured agent responses against saved expectations.</p></div>
      <a class="button" href="#observability">Open tracing</a>
    </header>
    <section class="ev-section" aria-labelledby="ev-datasets">
      <div class="ev-section-head"><h2 id="ev-datasets">Datasets</h2></div>
      ${datasets.length ? `
        <div class="ev-list">
          ${datasets.map((dataset) => `
            <a class="ev-dataset-row" href="#evals/${id(dataset.id)}">
              <span class="ev-dataset-icon" aria-hidden="true">▤</span>
              <div><strong>${esc(dataset.name)}</strong><small>${esc(dataset.owner)}</small></div>
              <span class="ev-muted">${count(dataset.version, "case")}</span><span aria-hidden="true">›</span>
            </a>`).join("")}
        </div>` : '<div class="ev-empty"><h3>No datasets yet</h3><p>Open a completed turn in Tracing and choose Add to dataset.</p></div>'}
    </section>`;
}

function renderDataset(root, dataset, canRun) {
  root.innerHTML = `
    <nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals">Evals</a><span aria-hidden="true">/</span><span>${esc(dataset.name)}</span></nav>
    <header class="ev-heading">
      <div><h1>${esc(dataset.name)}</h1><p>${count(dataset.cases.length, "case")} · Version ${dataset.version}</p></div>
      ${canRun ? '<button class="button primary" type="button" data-ev-run>Run eval</button>' : ""}
    </header>
    <p class="ev-error ev-run-error" role="alert"></p>
    <section class="ev-section" aria-labelledby="ev-cases">
      <div class="ev-section-head"><h2 id="ev-cases">Cases</h2><a href="#observability">Add from trace ↗</a></div>
      <div class="ev-list">
        ${dataset.cases.map((entry) => `
          <details class="ev-case" data-case="${esc(entry.id)}">
            <summary><span>${caseName(entry)}</span><small>${checkLabel(entry.check)}</small></summary>
            <div class="ev-case-detail"><div data-ev-evidence><p class="ev-muted" role="status">Loading captured evidence…</p></div></div>
          </details>`).join("")}
      </div>
    </section>
    <section class="ev-section" aria-labelledby="ev-runs">
      <div class="ev-section-head"><h2 id="ev-runs">Runs</h2></div>
      ${dataset.jobs.length ? `<div class="ev-list">${dataset.jobs.map((job) => `
        <a class="ev-run-row" href="${jobHref(dataset.id, job.id)}">
          <span><strong>${esc(jobName(job))}</strong><small>Version ${job.dataset_version}</small></span>
          ${job.status === "completed" ? pill("passed", "Completed") : pill("queued", "Queued")}
          <span aria-hidden="true">›</span>
        </a>`).join("")}</div>` : '<div class="ev-empty"><h3>No runs yet</h3><p>Run an eval to check the saved responses.</p></div>'}
    </section>`;
}

function renderCase(target, item) {
  target.innerHTML = `
    <dl><dt>Check</dt><dd>${checkLabel(item.check)}</dd>
        <dt>Expected</dt><dd>${textBlock(item.check.expected)}</dd>
        <dt>Captured response</dt><dd>${textBlock(item.evidence.output)}</dd>
        ${item.evidence.input == null ? "" : `<dt>Captured request</dt><dd>${textBlock(item.evidence.input)}</dd>`}</dl>
    <a href="${traceLink(item.turn)}">Open source turn ↗</a>`;
}

function renderJob(root, dataset, job) {
  const cases = new Map(dataset.cases.map((entry) => [entry.id, entry]));
  const results = job.results || [];
  const states = results.map(({ result }) => outcome(result)[0]);
  root.innerHTML = `
    <nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals">Evals</a><span aria-hidden="true">/</span><a href="#evals/${id(dataset.id)}">${esc(dataset.name)}</a><span aria-hidden="true">/</span><span>Run</span></nav>
    <header class="ev-heading">
      <div><h1>Evaluation run</h1><p>${esc(date(job.created_ms))} · Dataset version ${job.dataset_version}</p></div>
      ${job.status === "completed" ? pill("passed", "Completed") : pill("queued", "Queued")}
    </header>
    ${job.status !== "completed" ? '<div class="ev-empty" role="status"><h3>Waiting for results</h3><p>This page updates as the cluster finishes the eval.</p></div>' : `
      <div class="ev-metrics">${[["passed", "Passed"], ["failed", "Failed"], ["error", "Evaluator errors"]].map(([kind, label]) => `
        <div><span>${label}</span><strong>${states.filter((state) => state === kind).length}</strong></div>`).join("")}</div>
      <section class="ev-section" aria-labelledby="ev-results">
        <div class="ev-section-head"><h2 id="ev-results">Results</h2></div>
        <div class="ev-list">${results.map(({ turn, result }) => {
          const entry = cases.get(result.dataset_case);
          return `<details class="ev-case" ${entry ? `data-case="${esc(entry.id)}"` : ""}>
            <summary><span>${entry ? caseName(entry) : "Saved case"}</span>${pill(...outcome(result))}</summary>
            <div class="ev-case-detail">
              ${result.explanation ? `<p class="ev-result-note">${esc(result.explanation)}</p>` : ""}
              <div data-ev-evidence>${entry ? '<p class="ev-muted" role="status">Loading captured evidence…</p>' : `<a href="${traceLink(turn)}">Open source turn ↗</a>`}</div>
            </div>
          </details>`;
        }).join("")}</div>
      </section>`}`;
}

export function mountEvals(root, { api, datasetId, jobId, canRun }) {
  const lifetime = new AbortController();
  const { signal } = lifetime;
  let timer;
  const get = (path) => api(path, "GET", undefined, undefined, signal);
  const showError = (error) => {
    if (signal.aborted) return;
    root.innerHTML = `<header class="ev-heading"><div><h1>Evals</h1><p role="alert">${esc(errorText(error))}</p></div><a class="button" href="#observability">Open tracing</a></header>`;
  };
  function loadCaseEvidence() {
    root.querySelectorAll("details[data-case]").forEach((node) => {
      node.addEventListener("toggle", async () => {
        if (!node.open || node.dataset.loaded) return;
        node.dataset.loaded = "true";
        const detail = node.querySelector("[data-ev-evidence]");
        try {
          const item = await get(`${pathFor(datasetId)}/cases/${id(node.dataset.case)}`);
          if (!signal.aborted) renderCase(detail, item);
        } catch (error) {
          if (signal.aborted) return;
          detail.textContent = errorText(error);
          delete node.dataset.loaded;
        }
      });
    });
  }
  async function load() {
    try {
      if (!datasetId) {
        const { datasets } = await get(`${endpoint}/datasets`);
        if (!signal.aborted) renderIndex(root, datasets);
        return;
      }
      const dataset = await get(pathFor(datasetId));
      if (signal.aborted) return;
      if (jobId) {
        const poll = async () => {
          try {
            const job = await get(`${pathFor(datasetId)}/jobs/${id(jobId)}`);
            if (signal.aborted) return;
            renderJob(root, dataset, job);
            if (job.status === "completed") loadCaseEvidence();
            if (job.status !== "completed") timer = setTimeout(poll, 1500);
          } catch (error) { showError(error); }
        };
        await poll();
        return;
      }
      renderDataset(root, dataset, canRun);
      loadCaseEvidence();
      const key = crypto.randomUUID();
      const run = root.querySelector("[data-ev-run]");
      run?.addEventListener("click", async () => {
        run.disabled = true;
        run.textContent = "Queuing…";
        const message = root.querySelector(".ev-run-error");
        message.textContent = "";
        try {
          const job = await api(`${pathFor(datasetId)}/jobs`, "POST", { dataset_version: dataset.version }, key, signal);
          if (!signal.aborted) location.hash = jobHref(datasetId, job.id);
        } catch (error) {
          if (!signal.aborted) message.textContent = errorText(error);
        } finally {
          run.disabled = false;
          run.textContent = "Run eval";
        }
      });
    } catch (error) { showError(error); }
  }
  root.innerHTML = '<p class="ev-muted" role="status">Loading evaluations…</p>';
  load();
  return () => {
    lifetime.abort();
    clearTimeout(timer);
  };
}
