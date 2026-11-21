import { esc } from "./observability-data.js";
import { evalEndpoint, jobHref } from "./eval-lists.js";

const id = encodeURIComponent;
const traceHref = (turn) => "#observability/" + id(turn.session_id) + "/" + id(turn.turn_id);
export const evalTagPath = ({ sessionId, turnId }) =>
  evalEndpoint + "/tags/" + id(sessionId) + (turnId ? "/" + id(turnId) : "");
export const evalSelectionLink = (tag) => "#evals/selection" + (tag ? "?" + new URLSearchParams({ tag }) : "");
export const evalOutcome = (result) => result.status === "error" ? ["error", "Error"]
  : result.status === "skipped" ? ["skipped", "Skipped"]
    : result.score?.value === true ? ["passed", "Passed"] : ["failed", "Failed"];

function requestKey() {
  let last;
  return (body) => {
    const snapshot = JSON.stringify(body);
    if (last?.snapshot !== snapshot) last = { snapshot, key: crypto.randomUUID() };
    return last.key;
  };
}

export function mountEvalSelection(root, { api, query, signal, canRun }) {
  root.innerHTML = `
    <nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals">Evals</a><span aria-hidden="true">/</span><span>Tagged turns</span></nav>
    <header class="ev-heading"><div><h1>Evaluate tagged turns</h1><p>Choose which turns to score with a saved evaluator.</p></div></header>
    <section class="ev-section ev-selection" aria-label="Set up evaluation">
      <form class="ev-selection-filters">
        <label>Tag<input name="tag" maxlength="138" placeholder="e.g. release-check" required></label>
        <label>Tag applied<select name="period">
          <option value="24">Past 24 hours</option><option value="168">Past 7 days</option>
          <option value="720">Past 30 days</option><option value="">Any time</option>
        </select></label>
        <button class="button" type="submit">Preview turns</button>
      </form>
      <div class="ev-evaluator" ${canRun ? "" : "hidden"}>
        <div class="ev-selection-filters">
          <label>Evaluator<select data-evaluator><option value="">Choose an evaluator</option></select></label>
        </div>
        <details class="ev-disclosure">
          <summary>Find an evaluator</summary>
          <div class="ev-selection-filters ev-evaluator-lookup"><form class="ev-evaluator-search"><label>Evaluator name<input name="q" type="search" maxlength="100" placeholder="Name"></label><button class="button">Search</button></form>
            <button class="button" type="button" data-more-evaluators hidden>Load more</button></div>
        </details>
        <details class="ev-disclosure">
          <summary>Save an evaluator</summary>
          <form class="ev-capture ev-evaluator-editor">
            <label>Name<input name="name" maxlength="100" required></label>
            <label>Check<select name="comparison"><option value="contains">Contains text</option><option value="equals">Exact match</option></select></label>
            <label>Expected text<textarea name="expected" rows="3" maxlength="4000" required></textarea></label>
            <p class="ev-muted">Saving under the same name creates a new version.</p>
            <div><button class="button" type="submit">Save evaluator</button></div>
          </form>
        </details>
        <p data-evaluator-status class="ev-muted" role="status"></p>
      </div>
      <p class="ev-error" role="alert"></p>
    </section>
    <section class="ev-section" data-targets aria-label="Matching turns"></section>
    ${canRun ? '<footer class="ev-section ev-selection-footer" hidden><span class="ev-muted" role="status"></span><button class="button primary" type="button" disabled>Queue evaluation</button></footer>' : ""}`;
  const $ = (selector) => root.querySelector(selector);
  const filters = $(".ev-selection-filters");
  const results = $("[data-targets]");
  const evaluator = $("[data-evaluator]");
  const search = $(".ev-evaluator-search");
  const more = $("[data-more-evaluators]");
  const evaluatorStatus = $("[data-evaluator-status]");
  const editor = $(".ev-evaluator-editor");
  const message = $(".ev-error");
  const footer = $(".ev-selection-footer");
  const queue = footer?.querySelector("button");
  const selected = new Set();
  const saveKey = requestKey(), queueKey = requestKey();
  let evaluatorQuery = "", nextEvaluator = null, evaluatorRequest = 0, previewRequest = 0;
  let filter, turns = [], submitting = false;
  filters.elements.tag.value = (query.get("tag") || "").slice(0, 138);
  const update = () => {
    if (!queue) return;
    queue.disabled = submitting || !selected.size || !evaluator.value;
    footer.querySelector("[role=status]").textContent = selected.size + " of " + turns.length + " selected";
  };
  const loadEvaluators = async ({ append = false, select } = {}) => {
    const current = ++evaluatorRequest;
    const params = new URLSearchParams();
    if (evaluatorQuery) params.set("q", evaluatorQuery);
    if (append && nextEvaluator) params.set("after", nextEvaluator);
    more.disabled = true;
    try {
      const page = await api(evalEndpoint + "/evaluators" + (params.size ? "?" + params : ""), "GET", undefined, undefined, signal);
      if (signal.aborted || current !== evaluatorRequest) return;
      if (!append) evaluator.length = 1;
      for (const entry of page.evaluators) {
        if (![...evaluator.options].some((option) => option.value === entry.id)) {
          evaluator.add(new Option(entry.name + " · v" + entry.version, entry.id));
        }
      }
      if (select) {
        evaluator.value = select.id;
        if (!evaluator.value) {
          evaluator.add(new Option(select.name + " · v" + select.version, select.id));
          evaluator.value = select.id;
        }
      }
      nextEvaluator = page.next_cursor;
      more.hidden = !nextEvaluator;
      evaluatorStatus.textContent = select ? "Saved " + select.name + " · v" + select.version + "."
        : !page.evaluators.length ? "No matching evaluators." : "";
      update();
    } catch (error) {
      if (!signal.aborted && current === evaluatorRequest) evaluatorStatus.textContent = "Couldn’t load evaluators.";
    } finally {
      if (current === evaluatorRequest) more.disabled = false;
    }
  };
  filters.addEventListener("input", () => {
    previewRequest++;
    selected.clear();
    turns = [];
    results.replaceChildren();
    if (footer) footer.hidden = true;
    update();
  }, { signal });
  filters.addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = ++previewRequest;
    const period = filters.elements.period.value;
    const requested = { tag: filters.elements.tag.value.trim(), from_ms: period ? Date.now() - Number(period) * 3_600_000 : null };
    const params = new URLSearchParams({ tag: requested.tag });
    if (requested.from_ms != null) params.set("from_ms", String(requested.from_ms));
    message.textContent = "";
    results.innerHTML = '<p class="ev-muted" role="status">Finding tagged turns…</p>';
    selected.clear();
    if (footer) footer.hidden = true;
    update();
    try {
      const page = await api(evalEndpoint + "/candidates?" + params, "GET", undefined, undefined, signal);
      if (signal.aborted || current !== previewRequest) return;
      filter = requested;
      turns = page.turns;
      if (!turns.length) {
        results.innerHTML = '<div class="ev-empty"><h3>No matching turns</h3><p>Check the tag or expand the time window.</p></div>';
        return;
      }
      results.innerHTML = `
        <div class="ev-section-head"><h2>Matching turns</h2><span class="ev-muted">${page.truncated ? "Showing the 100 most recently tagged turns" : turns.length + " found"}</span></div>
        <div class="ev-table-shell"><table class="ev-table ev-target-table" aria-label="Matching turns">
          <thead><tr>${canRun ? '<th scope="col"><input type="checkbox" data-select-all aria-label="Select all matching turns"></th>' : ""}
            <th scope="col">Turn</th><th scope="col">Status</th><th scope="col" class="ev-optional">Tagged</th></tr></thead>
          <tbody>${turns.map((turn, n) => `<tr>${canRun ? '<td><input type="checkbox" data-target="' + n + '" aria-label="Select turn ' + (n + 1) + '"></td>' : ""}
            <th scope="row"><a class="ev-table-link" href="${traceHref(turn)}">${esc(turn.name || "Agent session")}</a>
              <small class="ev-table-subtitle">${esc(turn.turn_id.slice(0, 16))} · ${esc(new Date(turn.created_ms).toLocaleString())}</small></th>
            <td>${esc(turn.status)}</td><td class="ev-optional">${esc(new Date(turn.tagged_ms).toLocaleString())}</td></tr>`).join("")}</tbody>
        </table></div>`;
      if (footer) footer.hidden = false;
      update();
    } catch (error) {
      if (signal.aborted || current !== previewRequest) return;
      results.replaceChildren();
      message.textContent = error.status === 400 || error.status === 403
        ? "Check the tag. Use lowercase letters, numbers, dots, underscores, and hyphens for manual tags."
        : "Couldn’t load matching turns.";
    }
  }, { signal });
  results.addEventListener("change", ({ target }) => {
    const all = results.querySelector("[data-select-all]");
    if (target === all) {
      selected.clear();
      results.querySelectorAll("[data-target]").forEach((input) => {
        input.checked = all.checked;
        if (all.checked) selected.add(Number(input.dataset.target));
      });
    } else if (target.hasAttribute("data-target")) {
      const index = Number(target.dataset.target);
      if (target.checked) selected.add(index);
      else selected.delete(index);
    } else return;
    all.checked = selected.size === turns.length;
    all.indeterminate = selected.size > 0 && !all.checked;
    update();
  }, { signal });
  if (!canRun) return;
  evaluator.addEventListener("change", update, { signal });
  search.addEventListener("submit", (event) => {
    event.preventDefault();
    evaluatorQuery = search.elements.q.value.trim();
    loadEvaluators();
  }, { signal });
  more.addEventListener("click", () => loadEvaluators({ append: true }), { signal });
  editor.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = editor.querySelector("button");
    const data = new FormData(editor);
    const body = { name: data.get("name").trim(), check: { comparison: data.get("comparison"), expected: data.get("expected") } };
    button.disabled = true;
    try {
      const saved = await api(evalEndpoint + "/evaluators", "POST", body, saveKey(body), signal);
      if (signal.aborted) return;
      evaluatorQuery = saved.name;
      search.elements.q.value = saved.name;
      editor.closest("details").open = false;
      editor.reset();
      await loadEvaluators({ select: saved });
    } catch {
      if (!signal.aborted) evaluatorStatus.textContent = "Couldn’t save this evaluator. Check the name and expected text.";
    } finally {
      button.disabled = false;
    }
  }, { signal });
  queue.addEventListener("click", async () => {
    const body = { evaluator_id: evaluator.value, filter, turns: turns
      .filter((_, index) => selected.has(index)).map(({ session_id, turn_id }) => ({ session_id, turn_id })) };
    submitting = true;
    update();
    message.textContent = "";
    try {
      const job = await api(evalEndpoint + "/batches", "POST", body, queueKey(body), signal);
      if (!signal.aborted) location.hash = jobHref(null, job.id);
    } catch (error) {
      if (!signal.aborted) message.textContent = error.status === 409
        ? "The matching turns changed. Preview the filter again before queuing."
        : "Couldn’t confirm that the evaluation was queued. Retry to reuse this request.";
    } finally {
      submitting = false;
      update();
    }
  }, { signal });
  loadEvaluators();
}

export function editEvalTags(root, { api, target, signal, onChange }) {
  if (root.querySelector(".ev-dialog")) return;
  const dialog = document.createElement("dialog");
  const turn = Boolean(target.turn_id);
  dialog.className = "ev-dialog";
  dialog.setAttribute("aria-labelledby", "ev-tag-title");
  dialog.innerHTML = `
    <header class="ev-dialog-header"><div><h2 id="ev-tag-title">${turn ? "Turn tags" : "Session tags"}</h2>
      <p>${turn ? "Tags select this specific turn." : "Session tags select all of its finished turns."}</p></div>
      <button class="ev-close" type="button" aria-label="Close">×</button></header>
    <div class="ev-dialog-body ev-capture">
      <div data-tags><p class="ev-muted">Loading tags…</p></div>
      <form class="ev-capture"><label>Tag<input name="tag" maxlength="64" pattern="[a-z0-9._\\-]+" required placeholder="release-check"></label>
        <div><button class="button primary" type="submit">Add tag</button></div></form>
      <p class="ev-error" role="alert"></p>
    </div>`;
  root.append(dialog);
  const stop = () => dialog.close();
  const lifetime = new AbortController();
  signal.addEventListener("abort", stop, { once: true });
  dialog.addEventListener("close", () => {
    lifetime.abort();
    signal.removeEventListener("abort", stop);
    dialog.remove();
  }, { once: true });
  dialog.querySelector(".ev-close").addEventListener("click", stop);
  const path = evalTagPath({ sessionId: target.session_id, turnId: target.turn_id });
  const body = dialog.querySelector("[data-tags]");
  const error = dialog.querySelector("[role=alert]");
  const form = dialog.querySelector("form");
  const key = requestKey();
  let busy = false;
  const refresh = async () => {
    const value = await api(path, "GET", undefined, undefined, lifetime.signal);
    if (lifetime.signal.aborted) return;
    body.innerHTML = value.tags.length ? '<div class="ev-tag-list">' + value.tags.map(({ tag }) =>
      '<span><a href="' + esc(evalSelectionLink(tag)) + '">' + esc(tag) + '</a><button type="button" aria-label="Remove tag ' + esc(tag) + '" data-remove="' + esc(tag) + '">×</button></span>').join("") + '</div>'
      : '<p class="ev-muted">No manual tags yet.</p>';
    onChange(value);
  };
  const setTag = async (tag, enabled) => {
    if (busy) return;
    busy = true;
    error.textContent = "";
    const payload = { tag, enabled };
    try {
      await api(path, "POST", payload, key(payload), lifetime.signal);
      if (enabled) form.reset();
      await refresh();
    } catch {
      if (!lifetime.signal.aborted) error.textContent = "Couldn’t update this tag.";
    } finally {
      busy = false;
    }
  };
  body.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (button) setTag(button.dataset.remove, false);
  }, { signal: lifetime.signal });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    setTag(form.elements.tag.value.trim(), true);
  }, { signal: lifetime.signal });
  dialog.showModal();
  refresh().catch(() => {
    if (!lifetime.signal.aborted) body.textContent = "Couldn’t load tags.";
  });
}
