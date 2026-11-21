import { esc } from "./observability-data.js";

import { datasetHref, evalEndpoint as endpoint, evalTabs, jobHref, mountEvalList, runStatus } from "./eval-lists.js";
import { evalOutcome as outcome, evalSelectionLink, mountEvalSelection } from "./eval-selection.js";
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

export async function captureEvalCase(root, api, turn, signal, batch) {
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
    const preview = `${endpoint}/evidence/${id(session_id)}/${id(turn_id)}${batch ? "?batch_id=" + id(batch.id) : ""}`;
    const evidence = await api(preview, "GET", undefined, undefined, signal);
    if (!dialog.open || signal.aborted) return;
    body.innerHTML = `
      <form class="ev-capture">
        <label>Dataset<input name="dataset" list="ev-dataset-names" maxlength="100" placeholder="Choose or name a dataset" required></label>
        <datalist id="ev-dataset-names"></datalist>
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
    if (batch?.evaluator.check) {
      form.elements.expected.value = batch.evaluator.check.expected;
      form.elements.comparison.value = batch.evaluator.check.comparison;
    }
    form.querySelector("[data-ev-cancel]").addEventListener("click", cancel);
    const datasetInput = form.elements.dataset;
    const suggestions = form.querySelector("datalist");
    let lookupTimer, lookupRequest;
    function lookup() {
      lookupRequest?.abort();
      const request = new AbortController();
      lookupRequest = request;
      const params = new URLSearchParams({ owner: evidence.owner, limit: "12" });
      if (datasetInput.value.trim()) params.set("q", datasetInput.value.trim());
      api(`${endpoint}/datasets?${params}`, "GET", undefined, undefined, request.signal)
        .then(({ datasets }) => {
          if (request.signal.aborted || !dialog.open) return;
          suggestions.replaceChildren(...datasets.map(({ name }) => new Option(name, name)));
        })
        .catch(() => {
          if (!request.signal.aborted) suggestions.replaceChildren();
        });
    }
    datasetInput.addEventListener("input", () => {
      lookupRequest?.abort();
      clearTimeout(lookupTimer);
      lookupTimer = setTimeout(lookup, 200);
    });
    dialog.addEventListener("close", () => {
      lookupRequest?.abort();
      clearTimeout(lookupTimer);
    }, { once: true });
    datasetInput.focus();
    lookup();
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
        ...(batch ? { batch_id: batch.id } : {}),
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
        location.hash = datasetHref(saved.dataset_id);
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

const pill = (kind, name) => `<span class="ev-pill ev-${kind}"><i></i>${esc(name)}</span>`;
const caseName = (entry) => esc(entry.turn.name || "Agent response");
function renderIndex(root, view, canRun) {
  root.innerHTML = `
    <header class="ev-heading">
      <div><h1>Evals</h1><p>Evaluate captured agent responses.</p></div>
      <a class="button" href="${canRun ? evalSelectionLink() : "#observability"}">${canRun ? "Evaluate tagged turns" : "Open tracing"}</a>
    </header>
    ${evalTabs(view)}
    <section class="ev-section" data-eval-list></section>`;
}

function renderDataset(root, dataset, canRun, view) {
  root.innerHTML = `
    <nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals">Evals</a><span aria-hidden="true">/</span><span>${esc(dataset.name)}</span></nav>
    <header class="ev-heading">
      <div><h1>${esc(dataset.name)}</h1><p>${count(dataset.cases.length, "case")} · Version ${dataset.version}</p></div>
      ${canRun && dataset.cases.length ? '<button class="button primary" type="button" data-ev-run>Run eval</button>' : ""}
    </header>
    <p class="ev-error ev-run-error" role="alert"></p>
    ${evalTabs(view, dataset.id)}
    ${view === "runs" ? '<section class="ev-section" data-eval-list></section>' : `<section class="ev-section" aria-labelledby="ev-cases">
      <div class="ev-section-head"><h2 id="ev-cases">Cases</h2><a href="#observability">Add from trace ↗</a></div>
      <form class="ev-versions"><label>Dataset version <input type="number" name="version" min="1" max="${dataset.latest_version}" value="${dataset.version}" required></label>
        <button class="button" type="submit">View version</button>
        ${dataset.version === dataset.latest_version ? "" : `<a href="${datasetHref(dataset.id)}">Back to latest version</a>`}</form>
      <div class="ev-list">
        ${dataset.cases.map((entry) => `
          <details class="ev-case" data-case="${esc(entry.id)}">
            <summary><span>${caseName(entry)}</span><small>${checkLabel(entry.check)}</small></summary>
            <div class="ev-case-detail"><div data-ev-evidence><p class="ev-muted" role="status">Loading captured evidence…</p></div></div>
          </details>`).join("")}
      </div>
    </section>`}`;
}

function renderCase(target, item, editable) {
  target.innerHTML = `
    <dl><dt>Check</dt><dd>${checkLabel(item.check)}</dd>
        <dt>Expected</dt><dd>${textBlock(item.check.expected)}</dd>
        <dt>Captured response</dt><dd>${textBlock(item.evidence.output)}</dd>
        ${item.evidence.input == null ? "" : `<dt>Captured request</dt><dd>${textBlock(item.evidence.input)}</dd>`}</dl>
    <a href="${traceLink(item.turn)}">Open source turn ↗</a>
    ${item.turn.batch_id ? `<a href="${jobHref(null, item.turn.batch_id)}">Open source evaluation ↗</a>` : ""}
    ${editable ? `<details class="ev-disclosure ev-case-edit"><summary>Edit or archive case</summary>
      <form class="ev-capture">
        <label>Case name<input name="name" maxlength="100" value="${esc(item.turn.name)}" required></label>
        <label>Expected response<textarea name="expected" rows="3" maxlength="4000" required></textarea></label>
        <label>Text comparison<select name="comparison"><option value="contains">Contains text</option><option value="equals">Exact match</option></select></label>
        <p class="ev-error" role="alert"></p>
        <footer class="ev-actions"><button class="button" type="submit" value="archive" formnovalidate>Archive case</button>
          <button class="button primary" type="submit">Save revision</button></footer>
      </form></details>` : ""}`;
}

function renderJob(root, dataset, job, canRun) {
  const cases = new Map((dataset?.cases || []).map((entry) => [entry.id, entry]));
  const results = job.results || [];
  const states = results.map(({ result }) => outcome(result)[0]);
  root.innerHTML = `
    <nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals?view=runs">Evals</a><span aria-hidden="true">/</span>
      ${dataset ? `<a href="#evals/${id(dataset.id)}">${esc(dataset.name)}</a>`
        : `<a href="${evalSelectionLink(job.selection.tag)}">Tagged turns</a>`}
      <span aria-hidden="true">/</span><span>Run</span></nav>
    <header class="ev-heading">
      <div><h1>Evaluation run</h1><p>${esc(date(job.created_ms))} · ${dataset ? "Dataset version " + job.dataset_version
        : esc(job.evaluator.name) + " · v" + job.evaluator.version}</p></div>
      ${runStatus(job.status)}
    </header>
    ${dataset ? "" : `<section class="ev-section ev-run-selection">
      <details class="ev-disclosure"><summary>${job.targets.length} ${job.targets.length === 1 ? "turn" : "turns"} · ${esc(job.selection.tag)}</summary>
        <p>Check: ${checkLabel(job.evaluator.check)}</p>${textBlock(job.evaluator.check.expected)}
        ${job.selection.from_ms == null ? "" : `<p>Tag applied since ${esc(date(job.selection.from_ms))}</p>`}
        ${job.status === "completed" ? "" : `<ul>${job.targets.map((turn) =>
          `<li><a href="${traceLink(turn)}">${esc(turn.name || "Agent turn")}</a> · ${esc(turn.status)}</li>`).join("")}</ul>`}
      </details>
    </section>`}
    ${job.status !== "completed" ? `<div class="ev-section ev-empty" role="status"><h3>Waiting for results</h3><p>${job.progress?.total ? `${job.progress.completed} of ${job.progress.total} ${dataset ? "cases" : "turns"} scored.` : "This page updates as the cluster finishes the eval."}</p></div>` : `
      <div class="ev-metrics">${[["passed", "Passed"], ["failed", "Failed"], ["error", "Evaluator errors"]].map(([kind, label]) => `
        <div><span>${label}</span><strong>${states.filter((state) => state === kind).length}</strong></div>`).join("")}</div>
      <section class="ev-section" aria-labelledby="ev-results">
        <div class="ev-section-head"><h2 id="ev-results">Results</h2></div>
        <div class="ev-list">${results.map(({ turn, result }, index) => {
          const entry = cases.get(result.dataset_case);
          return `<details class="ev-case" ${entry ? `data-case="${esc(entry.id)}"` : ""}>
            <summary><span>${entry ? caseName(entry) : esc(turn.name || "Agent turn")}</span>${pill(...outcome(result))}</summary>
            <div class="ev-case-detail">
              ${result.explanation ? `<p class="ev-result-note">${esc(result.explanation)}</p>` : ""}
              ${turn.status ? `<p class="ev-muted">Turn status · ${esc(turn.status)}</p>` : ""}
              <div data-ev-evidence>${entry ? '<p class="ev-muted" role="status">Loading captured evidence…</p>' : `<a href="${traceLink(turn)}">Open source turn ↗</a>`}</div>
              ${!dataset && canRun ? `<button class="button ev-case-edit" type="button" data-promote="${index}">Add to dataset</button>` : ""}
            </div>
          </details>`;
        }).join("")}</div>
      </section>`}`;
}

export function mountEvals(root, { api, datasetId, jobId, canRun, query }) {
  const lifetime = new AbortController();
  const { signal } = lifetime;
  let timer;
  const view = query.get("view") === "runs" ? "runs" : "datasets";
  const loadList = () => mountEvalList(root.querySelector("[data-eval-list]"), {
    api, view, datasetId, query, signal, errorText,
  });
  const get = (path) => api(path, "GET", undefined, undefined, signal);
  const showError = (error) => {
    if (signal.aborted) return;
    root.innerHTML = `<header class="ev-heading"><div><h1>Evals</h1><p role="alert">${esc(errorText(error))}</p></div><a class="button" href="#observability">Open tracing</a></header>`;
  };
  function loadCaseEvidence(dataset, editable = false) {
    root.querySelectorAll("details[data-case]").forEach((node) => {
      node.addEventListener("toggle", async () => {
        if (!node.open || node.dataset.loaded) return;
        node.dataset.loaded = "true";
        const detail = node.querySelector("[data-ev-evidence]");
        try {
          const item = await get(`${pathFor(datasetId)}/cases/${id(node.dataset.case)}`);
          if (signal.aborted) return;
          renderCase(detail, item, editable);
          const editor = detail.querySelector("form");
          if (!editor) return;
          editor.elements.expected.value = item.check.expected;
          editor.elements.comparison.value = item.check.comparison;
          let previous;
          editor.addEventListener("submit", async (event) => {
            event.preventDefault();
            const archive = event.submitter.value === "archive";
            const data = new FormData(editor);
            const body = archive ? { archive: true } : {
              name: data.get("name").trim(),
              check: { expected: data.get("expected"), comparison: data.get("comparison") },
            };
            const snapshot = JSON.stringify(body);
            if (previous?.snapshot !== snapshot) previous = { snapshot, key: crypto.randomUUID() };
            const buttons = [...editor.querySelectorAll("button")];
            buttons.forEach((button) => button.disabled = true);
            try {
              await api(`${pathFor(datasetId)}/cases/${id(node.dataset.case)}`, "POST", body, previous.key, signal);
              if (!signal.aborted) {
                if (location.hash === datasetHref(dataset.id)) await load();
                else location.hash = datasetHref(dataset.id);
              }
            } catch (error) {
              if (!signal.aborted) editor.querySelector("[role=alert]").textContent = errorText(error);
            } finally {
              buttons.forEach((button) => button.disabled = false);
            }
          }, { signal });
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
      if (datasetId === "selection" && !jobId) {
        mountEvalSelection(root, { api, query, signal, canRun });
        return;
      }
      if (!datasetId) {
        renderIndex(root, view, canRun);
        loadList();
        return;
      }
      const batch = datasetId === "batch" && jobId;
      const requestedVersion = query.get("version");
      const dataset = batch || jobId ? null : await get(pathFor(datasetId) + (requestedVersion ? "?version=" + id(requestedVersion) : ""));
      if (signal.aborted) return;
      if (jobId) {
        const poll = async () => {
          try {
            const job = await get(batch ? `${endpoint}/batches/${id(jobId)}` : `${pathFor(datasetId)}/jobs/${id(jobId)}`);
            if (signal.aborted) return;
            const snapshot = batch ? null : await get(pathFor(datasetId) + "?version=" + job.dataset_version);
            if (signal.aborted) return;
            renderJob(root, snapshot, job, canRun);
            if (job.status === "completed") {
              loadCaseEvidence(snapshot);
              root.querySelectorAll("[data-promote]").forEach((button) => button.addEventListener("click", () => {
                captureEvalCase(root, api, job.results[Number(button.dataset.promote)].turn, signal, job);
              }, { signal }));
            }
            if (job.status !== "completed") timer = setTimeout(poll, 1500);
          } catch (error) { showError(error); }
        };
        await poll();
        return;
      }
      renderDataset(root, dataset, canRun, view);
      if (view === "runs") loadList();
      else loadCaseEvidence(dataset, canRun && dataset.version === dataset.latest_version);
      root.querySelector(".ev-versions")?.addEventListener("submit", (event) => {
        event.preventDefault();
        location.hash = datasetHref(dataset.id) + "?version=" + event.currentTarget.elements.version.value;
      }, { signal });
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
