import { esc } from "./observability-data.js";

export const evalEndpoint = "/v1/evals";
const id = encodeURIComponent;
export const datasetHref = (dataset) => `#evals/${id(dataset)}`;
export const jobHref = (dataset, job) => `${dataset ? datasetHref(dataset) : "#evals/batch"}/${id(job)}`;

const shortDate = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});
const exactDate = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium", timeStyle: "medium",
});
export function runStatus(status) {
  return status === "completed"
    ? '<span class="ev-pill ev-passed"><i></i>Completed</span>'
    : '<span class="ev-pill ev-queued"><i></i>Queued</span>';
}
const time = (ms) => `<time datetime="${new Date(ms).toISOString()}" title="${esc(exactDate.format(ms))}">${esc(shortDate.format(ms))}</time>`;
const periodOptions = [["", "Any time"], ["24", "Past 24 hours"], ["168", "Past 7 days"], ["720", "Past 30 days"]];
const statusOptions = [["", "All statuses"], ["queued", "Queued"], ["completed", "Completed"]];

export function evalTabs(active, datasetId) {
  const root = datasetId ? datasetHref(datasetId) : "#evals";
  return `<nav class="ev-catalog-tabs" aria-label="${datasetId ? "Dataset views" : "Evaluation views"}">
    <a href="${root}" ${active === "runs" ? "" : 'aria-current="page"'}>${datasetId ? "Cases" : "Datasets"}</a>
    <a href="${root}?view=runs" ${active === "runs" ? 'aria-current="page"' : ""}>Runs</a>
  </nav>`;
}

function optionList(options) {
  return options.map(([value, name]) => `<option value="${value}">${name}</option>`).join("");
}

function scoreCounts(counts) {
  if (!counts) return '<span class="ev-muted">Pending</span>';
  return `<span class="ev-counts">
    <span title="Passed">${counts.passed} passed</span>
    ${counts.failed ? `<span class="ev-count-failed">${counts.failed} failed</span>` : ""}
    ${counts.errors ? `<span class="ev-count-error">${counts.errors} ${counts.errors === 1 ? "error" : "errors"}</span>` : ""}
  </span>`;
}

function renderTable(items, runs, withinDataset) {
  const headings = runs
    ? '<th scope="col">Started</th>' +
      (withinDataset ? "" : '<th scope="col">Source</th>') +
      '<th scope="col">Status</th><th scope="col" class="ev-optional">Results</th>'
    : '<th scope="col">Dataset</th><th scope="col" class="ev-optional">App</th><th scope="col" class="ev-numeric">Cases</th><th scope="col" class="ev-optional">Last run</th>';
  const body = runs
    ? items.map((run) => `<tr>
        <th scope="row"><a class="ev-table-link" href="${jobHref(run.dataset_id, run.id)}">${time(run.created_ms)}</a>
          <small class="ev-table-subtitle">${run.tag ? esc(run.tag) : `Version ${run.dataset_version}`}</small></th>
        ${withinDataset ? "" : `<td>${run.dataset_id ? `<a class="ev-secondary-link" href="${datasetHref(run.dataset_id)}">${esc(run.dataset_name)}</a>` : esc(run.name)}
          <small class="ev-table-subtitle">${esc(run.owner)}</small></td>`}
        <td>${runStatus(run.status)}<small class="ev-table-subtitle ev-compact">${scoreCounts(run.counts)}</small></td>
        <td class="ev-optional">${scoreCounts(run.counts)}</td>
      </tr>`).join("")
    : items.map((dataset) => `<tr>
        <th scope="row"><a class="ev-table-link" href="${datasetHref(dataset.id)}">${esc(dataset.name)}</a>
          <small class="ev-table-subtitle ev-compact">${esc(dataset.owner)}</small></th>
        <td class="ev-optional">${esc(dataset.owner)}</td>
        <td class="ev-numeric">${dataset.case_count ?? dataset.version}</td>
        <td class="ev-optional">${dataset.last_run_ms == null ? '<span class="ev-muted">—</span>' : time(dataset.last_run_ms)}</td>
      </tr>`).join("");
  return `<div class="ev-table-shell"><table class="ev-table" aria-label="${runs ? 'Evaluation runs' : 'Evaluation datasets'}"><thead><tr>${headings}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function mountEvalList(root, { api, view, datasetId, query, signal, errorText }) {
  const runs = view === "runs";
  const noun = runs ? "runs" : "datasets";
  root.innerHTML = `
    <div class="ev-list-toolbar">
      <label class="ev-list-search"><span class="ev-visually-hidden">Search ${noun}</span>
        <input type="search" maxlength="100" placeholder="${runs ? "Search dataset, evaluator, tag or run ID…" : "Search datasets or apps…"}">
      </label>
      ${runs ? `<label><span class="ev-visually-hidden">Run status</span><select data-filter="status">${optionList(statusOptions)}</select></label>
        <label><span class="ev-visually-hidden">Run date</span><select data-filter="period">${optionList(periodOptions)}</select></label>` : ""}
      <button class="button" type="button" data-list-action="refresh">Refresh</button>
    </div>
    <div class="ev-list-content"></div>
    <footer class="ev-page-controls">
      <span class="ev-muted" role="status"></span>
      <div><button type="button" class="button" data-list-action="previous" disabled>Previous</button>
      <button type="button" class="button" data-list-action="next" disabled>Next</button></div>
    </footer>`;
  const input = root.querySelector("input");
  const status = root.querySelector("[data-filter=status]");
  const period = root.querySelector("[data-filter=period]");
  const content = root.querySelector(".ev-list-content");
  const info = root.querySelector("[role=status]");
  const previous = root.querySelector("[data-list-action=previous]");
  const next = root.querySelector("[data-list-action=next]");
  input.value = (query.get("q") || "").slice(0, 100);
  if (status && statusOptions.some(([v]) => v === query.get("status"))) status.value = query.get("status");
  if (period && periodOptions.some(([v]) => v === query.get("period"))) period.value = query.get("period");

  let cursors = [null];
  let nextCursor = null;
  let timer, request;
  let since = null;
  const cancel = () => {
    clearTimeout(timer);
    request?.abort();
  };
  signal.addEventListener("abort", cancel, { once: true });
  const remember = () => {
    const params = new URLSearchParams();
    if (runs) params.set("view", "runs");
    if (input.value.trim()) params.set("q", input.value.trim());
    if (status?.value) params.set("status", status.value);
    if (period?.value) params.set("period", period.value);
    const base = datasetId ? datasetHref(datasetId) : "#evals";
    const hash = params.size ? `${base}?${params}` : base;
    history.replaceState({ ...history.state, evalList: { hash, cursors, since } }, "", hash);
  };

  async function load(focusResults = false) {
    request?.abort();
    const controller = new AbortController();
    request = controller;
    const params = new URLSearchParams({ limit: "16" });
    if (input.value.trim()) params.set("q", input.value.trim());
    if (datasetId) params.set("dataset", datasetId);
    if (status?.value) params.set("status", status.value);
    if (since != null) params.set("since", String(since));
    if (cursors.at(-1)) params.set("after", cursors.at(-1));
    previous.disabled = next.disabled = true;
    content.setAttribute("aria-busy", "true");
    if (!content.children.length) content.innerHTML = `<div class="ev-empty ev-muted">Loading ${noun}…</div>`;
    try {
      const page = await api(`${evalEndpoint}/${noun}?${params}`, "GET", undefined, undefined, controller.signal);
      if (controller.signal.aborted || signal.aborted) return;
      const items = page[noun];
      nextCursor = page.next_cursor;
      if (items.length) {
        content.innerHTML = renderTable(items, runs, Boolean(datasetId));
      } else {
        const filtered = input.value.trim() || status?.value || period?.value;
        const heading = filtered ? `No matching ${noun}` : `No ${noun} yet`;
        const hint = filtered ? "Try a different search or filter."
          : runs ? "Run an eval from a dataset or a set of tagged turns."
          : "Open a completed turn in Tracing and choose Add to dataset.";
        content.innerHTML = `<div class="ev-empty"><h3>${heading}</h3><p>${hint}</p></div>`;
      }
      info.textContent = `Page ${cursors.length} · ${items.length} shown`;
      next.disabled = !nextCursor;
      if (focusResults && items.length) {
        content.querySelector(".ev-table-link")?.focus({ preventScroll: true });
        root.querySelector(".ev-list-toolbar").scrollIntoView({ block: "start" });
      }
    } catch (error) {
      if (controller.signal.aborted || signal.aborted) return;
      nextCursor = null;
      content.innerHTML = `<div class="ev-empty"><p role="alert">${esc(errorText(error))}</p></div>`;
      info.textContent = "";
    } finally {
      if (request === controller && !signal.aborted) {
        content.removeAttribute("aria-busy");
        previous.disabled = cursors.length === 1;
      }
    }
  }

  function reset(debounce = false) {
    cancel();
    cursors = [null];
    nextCursor = null;
    since = period?.value ? Date.now() - Number(period.value) * 3_600_000 : null;
    previous.disabled = next.disabled = true;
    remember();
    if (debounce) timer = setTimeout(load, 230);
    else load();
  }
  input.addEventListener("input", () => reset(true), { signal });
  status?.addEventListener("change", () => reset(), { signal });
  period?.addEventListener("change", () => reset(), { signal });
  root.querySelector("[data-list-action=refresh]").addEventListener("click", () => reset(), { signal });
  previous.addEventListener("click", () => {
    cursors.pop();
    remember();
    load(true);
  }, { signal });
  next.addEventListener("click", () => {
    if (!nextCursor) return;
    cursors.push(nextCursor);
    remember();
    load(true);
  }, { signal });
  const saved = history.state?.evalList;
  if (saved?.hash === location.hash) {
    cursors = saved.cursors;
    since = saved.since;
    load();
  } else {
    reset();
  }
}
