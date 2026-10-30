import {
  esc,
  known,
  count,
  duration,
  measurement,
  average,
  runKey,
  purpose,
  phases,
  usageTotal,
  purposeUsage,
  coverageNotes,
  timelineLayout,
} from "./observability-data.js";
const query = (params) =>
  new URLSearchParams(
    Object.entries(params).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    ),
  ).toString();
const perRun = (r) =>
  `/v1/sessions/${encodeURIComponent(r.session_id)}/observability/${encodeURIComponent(r.turn_id)}`;
const cell = (value) => `<td>${esc(value)}</td>`;
const hint = (text) => `<p class="obs-hint">${esc(text)}</p>`;
const notice = (text) =>
  `<div class="obs-notice" role="status">${esc(text)}</div>`;
function failure(e) {
  if (e.status === 403) return "This credential cannot read the selected run.";
  if (e.status === 404) return "This run is no longer available.";
  if (e.status === 429)
    return "Recording readers are busy. Try Refresh shortly.";
  if (e.status === 503)
    return "Recordings are unavailable or disabled. You can still search runs.";
  return "Unable to load recordings. Try Refresh.";
}
const rowTable = (headers, rows, label) =>
  `<div class="obs-table-wrap"><table class="table" aria-label="${label}"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${headers.length}" class="obs-empty-cell">No recorded data</td></tr>`}</tbody></table></div>`;
function bars(rows, label, formatter = duration, type = "phase") {
  const max = Math.max(
    1,
    ...rows.filter((r) => known(r.value)).map((r) => r.value),
  );
  return `<div class="obs-bars" aria-label="${label}">${rows.map((r) => `<div class="obs-bar-row"><span>${esc(r.name)}</span><div class="obs-track"><i class="obs-fill obs-${r.key || type}" data-obs-width="${known(r.value) ? Math.min(100, (r.value / max) * 100) : 0}"></i></div><span class="obs-number">${esc(formatter(r.value))}${r.missing ? "*" : ""}</span></div>`).join("") || hint("No recorded data")}</div>`;
}
function tokensByPurpose(s) {
  const rows = purposeUsage(s.models).map((r) => ({
    ...r,
    value: r.total,
    key: "model",
  }));
  return (
    bars(rows, "Tokens by purpose", count) +
    (s.coverage.group_truncated ? hint("Group list is truncated.") : "") +
    (rows.some((r) => r.missing)
      ? hint("* Some request counters are unknown.")
      : "") +
    hint(
      `Cached input: ${measurement(s.usage.cache_read)} · already included in input.`,
    )
  );
}
function modelTable(s) {
  return rowTable(
    ["Model / purpose", "Calls", "Input", "Output", "First output · avg."],
    s.models.map(
      (m) =>
        `<tr><td>${esc(m.name || "Unspecified")}<span class="cell-subtitle">${esc(purpose(m.purpose))}</span></td>${cell(count(m.calls))}${cell(measurement(m.usage?.input))}${cell(measurement(m.usage?.output))}<td>${esc(duration(average(m.first_output_us)))}${m.first_output_us.unknown ? `<span class="cell-subtitle">${count(m.first_output_us.unknown)} unknown</span>` : ""}</td></tr>`,
    ),
    "Model usage",
  );
}
function toolTable(s) {
  return rowTable(
    ["Tool", "Calls", "Accumulated", "Avg. known", "Result bytes"],
    [...s.tools]
      .sort(
        (a, b) =>
          (b.accumulated_us.total ?? -1) - (a.accumulated_us.total ?? -1),
      )
      .map(
        (t) =>
          `<tr><td><button class="obs-text" data-obs-tool="${esc(t.name || "")}">${esc(t.name || "Unspecified")}</button></td>${cell(count(t.calls))}${cell(measurement(t.accumulated_us, duration))}${cell(duration(average(t.accumulated_us)))}${cell(measurement(t.returned_bytes))}</tr>`,
      ),
    "Tool summary",
  );
}
function coverage(s) {
  const c = s.coverage,
    notes = coverageNotes(c);
  return `<details class="obs-details"><summary>Coverage${notes.length ? ` · ${notes.length} ${notes.length === 1 ? "note" : "notes"}` : ""}</summary><p>${count(c.scanned_steps)} steps scanned. Recording is best effort; missing counters are unknown, not zero.</p>${notes.length ? `<ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}<p>Late commits and retention can change a refreshed view. A closed run is not proof of lossless accounting.</p></details>`;
}
const stepName = (s) =>
  s.operation === "model.attempt"
    ? s.model || "Model request"
    : s.operation === "model.requested"
      ? "Request started"
      : s.operation === "tool.execute"
        ? s.tool || "Tool execution"
        : {
            "agent.execute": "Execution segment",
            "policy.review": "Policy check",
            "approval.wait": "Approval wait",
            "question.wait": "Question wait",
          }[s.operation] || s.operation;
const stepKind = (s) =>
  s.operation.startsWith("model.")
    ? "model"
    : s.operation.startsWith("tool.")
      ? "tool"
      : s.operation === "policy.review"
        ? "policy"
        : s.operation === "approval.wait"
          ? "approval_wait"
          : s.operation === "question.wait"
            ? "question_wait"
            : "other_active";

export function mountObservability(root, { request, isAdmin = false }) {
  const lifetime = new AbortController();
  let stopped = false,
    serial = 0,
    searchSerial = 0,
    timer,
    loadController,
    searchController;
  let chosen = null,
    tab = "usage",
    summary = null,
    selection = null,
    timeline = [],
    timelinePage = null,
    timelineThrough = null,
    selectedStep = null,
    tool = null;
  let results = [],
    resultNext = null,
    searchText = "",
    focusedIndex = -1,
    pageCursor = null,
    pageStack = [];
  let health = null,
    healthError = false;
  const controllers = new Set();
  function controller() {
    const c = new AbortController();
    controllers.add(c);
    return c;
  }
  async function read(path, c) {
    controllers.add(c);
    try {
      return await request(path, c.signal);
    } finally {
      controllers.delete(c);
    }
  }
  root.innerHTML = `<section class="observability" id="observability"><div class="page-heading"><div><h1>Observability</h1><p>Where agent time and tokens go.</p></div><button class="button" data-obs="refresh">Refresh</button></div><div class="obs-toolbar"><div class="tabs obs-tabs" role="tablist" aria-label="Observability views"><button class="tab active" role="tab" id="obs-usage-tab" aria-controls="obs-content" aria-selected="true" data-obs="usage">Usage</button><button class="tab" role="tab" id="obs-timeline-tab" aria-controls="obs-content" aria-selected="false" data-obs="timeline">Timeline</button></div><div class="obs-picker"><label for="obs-run-search">Find a run</label><input id="obs-run-search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="obs-run-options" autocomplete="off" placeholder="Search name or ID…"><div id="obs-run-menu" class="obs-run-menu" hidden><div id="obs-run-options" role="listbox" aria-label="Matching runs"></div><button class="obs-more" id="obs-search-more" hidden>More results</button><p id="obs-search-state" role="status"></p></div></div></div><div id="obs-scope"></div><div id="obs-content" role="tabpanel" aria-labelledby="obs-usage-tab" aria-live="polite"></div><details class="obs-details"><summary>How time and usage are counted</summary><p>Input and output are provider-reported tokens. Cached input and reasoning are subsets, not extra tokens. Tools have duration and result bytes; tokens cannot be attributed exactly to a tool.</p><p>Accumulated work adds individual durations. Exclusive phases partition the observed window, with parallel work shown separately. Unobserved gaps are not assigned to model or tool work. First-output time includes transport and provider queueing, not just inference.</p></details>${isAdmin ? '<details class="obs-details" id="obs-health"><summary>Recorder &amp; exporter health</summary><div id="obs-health-content"></div></details>' : ""}</section>`;
  const $ = (s) => root.querySelector(s);
  const content = $("#obs-content"),
    input = $("#obs-run-search"),
    menu = $("#obs-run-menu");
  function widths() {
    for (const el of root.querySelectorAll("[data-obs-width]"))
      el.style.width = `${el.dataset.obsWidth}%`;
    for (const el of root.querySelectorAll("[data-obs-left]"))
      el.style.left = `${el.dataset.obsLeft}%`;
  }
  function scope() {
    $("#obs-scope").innerHTML = chosen
      ? `<div class="obs-scope"><div><strong>${esc(chosen.name || chosen.turn_id)}</strong><span>${esc(chosen.agent)} · ${esc(chosen.status)} · ${esc(new Date(chosen.created_ms).toLocaleString())}</span></div><button class="obs-text" data-obs="recent">Recent runs</button><a class="obs-text" href="#sessions/${encodeURIComponent(chosen.session_id)}">Open session ↗</a></div>`
      : `<p class="obs-hint obs-scope">${selection ? `${selection.selected_runs} recent authorized runs${selection.selection_truncated ? " · more available" : ""}. Summary covers this page only.` : "Recent authorized runs"}</p>`;
    if (summary?.coverage.scan_truncated || summary?.coverage.group_truncated) {
      $("#obs-scope").insertAdjacentHTML(
        "beforeend",
        '<span class="obs-partial">Partial summary</span>',
      );
    }
    content.setAttribute("aria-labelledby", `obs-${tab}-tab`);
    for (const b of root.querySelectorAll(
      "[data-obs=usage],[data-obs=timeline]",
    )) {
      const active = b.dataset.obs === tab;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", String(active));
    }
  }
  function draw() {
    if (stopped) return;
    scope();
    if (!summary) return;
    const s = summary,
      tokenTotal = usageTotal(s.usage),
      tokensMissing = s.usage.input.unknown + s.usage.output.unknown;
    content.innerHTML = `<div class="stats obs-stats">${[
      [
        chosen ? "Run elapsed" : "Observed window",
        duration(chosen ? s.wall_elapsed_us : s.observed_window_us),
      ],
      [
        "Reported tokens",
        count(tokenTotal) +
          (known(tokenTotal) && tokensMissing ? " + unknown" : ""),
      ],
      [
        "Model requests",
        count(s.models.reduce((n, g) => n + g.calls, 0)) +
          (s.coverage.group_truncated ? "+" : ""),
      ],
      [
        "Tool calls",
        count(s.tools.reduce((n, g) => n + g.calls, 0)) +
          (s.coverage.group_truncated ? "+" : ""),
      ],
    ]
      .map(
        ([label, value]) =>
          `<div class="stat"><div class="stat-label">${label}</div><div class="stat-main"><span class="stat-value">${esc(value)}</span></div></div>`,
      )
      .join(
        "",
      )}</div>${s.coverage.scanned_steps === 0 ? notice("No recorded steps yet. The run directory is still available below.") : ""}${tab === "timeline" ? timelineView(s) : usageView(s)}${coverage(s)}${runDirectory()}`;
    widths();
  }
  function usageView(s) {
    return `<div class="obs-grid"><section class="obs-panel"><h2>Where time went</h2>${hint("Exclusive phases · observed time")}${bars(
      phases
        .map(([key, name]) => ({
          key,
          name,
          value: s.exclusive_observed_us[key],
        }))
        .filter((r) => r.value > 0),
      "Exclusive phase duration",
    )}${hint(`Observed window: ${duration(s.observed_window_us)}${chosen ? "" : " · union across selected runs, not summed run time"}`)}</section><section class="obs-panel"><h2>Tokens by purpose</h2>${hint("Reported input + output")}${tokensByPurpose(s)}</section></div><div class="obs-grid"><section class="obs-panel"><h2>Accumulated work</h2>${hint("May exceed elapsed time when calls overlap")}${bars(
      [
        {
          name: "Model requests",
          value: s.model_accumulated_us.total,
          key: "model",
        },
        {
          name: "Tool executions",
          value: s.tool_accumulated_us.total,
          key: "tool",
        },
      ],
      "Accumulated work",
    )}${s.model_accumulated_us.unknown || s.tool_accumulated_us.unknown ? hint("Some durations are unknown; bars show measured work only.") : ""}</section><section class="obs-panel"><h2>Model response time</h2>${hint("Average request start → first output · known samples")}${bars(
      s.models.map((m) => ({
        name: `${m.name || "Unspecified"} · ${purpose(m.purpose)}`,
        value: average(m.first_output_us),
        missing: m.first_output_us.unknown,
        key: "model",
      })),
      "Average model first output",
    )}</section></div><section class="obs-panel"><h2>Tools</h2>${hint("Select a tool to inspect its calls in a run")}${toolTable(s)}</section><section class="obs-panel"><h2>Model usage</h2>${modelTable(s)}</section>`;
  }
  function timelineView(s) {
    if (!chosen) return notice("Choose a run to see its timeline.");
    const records = tool
      ? timeline.filter((r) => r.step.tool === tool)
      : timeline;
    const layout = timelineLayout(records);
    const selected = timeline.find(
      (r) => String(r.cursor) === String(selectedStep),
    );
    return `<div class="obs-grid"><section class="obs-panel"><h2>Time by phase</h2>${bars(
      phases
        .map(([key, name]) => ({
          key,
          name,
          value: s.exclusive_observed_us[key],
        }))
        .filter((r) => r.value > 0),
      "Run phase duration",
    )}</section><section class="obs-panel"><h2>Usage by purpose</h2>${tokensByPurpose(s)}</section></div><section class="obs-panel obs-timeline"><div class="obs-panel-title"><div><h2>Turn timeline</h2>${hint(`${timeline.length} loaded records · select a step for details`)}</div>${tool ? `<button class="obs-text" data-obs="clear-tool">Clear tool filter</button>` : ""}</div>${tool ? hint(`Tool: ${tool}`) : ""}<div class="obs-axis"><span>0</span><span>${duration(layout.to - layout.from)}</span></div><div class="obs-lanes">${layout.rows.map((r) => `<button class="obs-lane" data-obs-step="${r.cursor}" aria-pressed="${String(selectedStep) === String(r.cursor)}"><span class="obs-lane-name" title="${esc(stepName(r.step))}">${esc(stepName(r.step))}<small>${esc(r.step.operation === "model.attempt" ? purpose(r.step.purpose) : r.step.outcome)}</small></span><span class="obs-lane-track"><i class="obs-mark obs-${stepKind(r.step)}" data-obs-left="${r.left}" data-obs-width="${r.width}"></i></span><span class="obs-number">${duration(r.step.duration_us)}</span></button>`).join("") || hint("No timeline records in this view.")}</div>${timelinePage?.next !== null && timelinePage?.next !== undefined ? (timeline.length < 1000 ? '<button class="button" data-obs="more-timeline">Load more records</button>' : hint("The browser limit of 1,000 records was reached. The summary uses its separate bounded scan.")) : ""}<div id="obs-step-detail">${selected ? stepDetail(selected) : hint("Select a step to inspect timing, usage and outcome.")}</div><div id="obs-timeline-error" role="status"></div>${hint("Bars use recorded starts and measured durations. Unknown duration is a marker, not zero. Late records can appear before earlier pages.")}</section>`;
  }
  function stepDetail(r) {
    const s = r.step;
    return `<div class="obs-step-detail"><div><strong>${esc(stepName(s))}</strong><span>${esc(s.outcome)} · ${esc(s.operation)}</span></div><dl>${[
      ["Duration", duration(s.duration_us)],
      ["First output", duration(s.first_output_us)],
      ["Input tokens", count(s.usage?.input)],
      ["Output tokens", count(s.usage?.output)],
      ["Result bytes", count(s.output_bytes)],
      ["Attempt", count(s.attempt)],
    ]
      .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`)
      .join(
        "",
      )}</dl><details><summary>Record details</summary><pre>${esc(JSON.stringify(r, null, 2))}</pre></details></div>`;
  }
  function runDirectory() {
    if (chosen) return "";
    const runs = selection?.selection?.runs || [];
    return `<section class="obs-panel"><h2>Runs</h2>${rowTable(
      ["Run", "Agent", "Status", "Submitted"],
      runs.map(
        (r, i) =>
          `<tr><td><button class="obs-text obs-run-name" data-obs-run="${i}">${esc(r.name || r.turn_id)}</button><span class="cell-subtitle mono">${esc(r.turn_id)}</span></td>${cell(r.agent)}${cell(r.status)}${cell(new Date(r.created_ms).toLocaleString())}</tr>`,
      ),
      "Authorized runs",
    )}<div class="obs-pagination">${pageStack.length ? '<button class="button" data-obs="previous">Previous page</button>' : ""}${selection?.selection?.next_cursor ? '<button class="button" data-obs="next">Next 25 runs</button>' : ""}</div></section>`;
  }
  async function load() {
    const n = ++serial;
    loadController?.abort();
    const c = controller();
    loadController = c;
    summary = null;
    content.innerHTML = notice("Loading recordings…");
    scope();
    try {
      if (chosen) {
        const page = await read(perRun(chosen) + "/timeline?limit=100", c);
        if (stopped || n !== serial) return;
        timeline = page.page.steps;
        timelinePage = page.page;
        timelineThrough = page.page.through;
        selectedStep = null;
        // One watermark aligns the first timeline page and summary. It is not
        // a cross-request database snapshot; late commits/retention can differ.
        const value = await read(
          perRun(chosen) + "/summary?" + query({ through: timelineThrough }),
          c,
        );
        if (stopped || n !== serial) return;
        chosen = value.run;
        summary = value.summary;
      } else {
        const value = await read(
          "/v1/observability/summary?" +
            query({ limit: 25, after: pageCursor }),
          c,
        );
        if (stopped || n !== serial) return;
        selection = value;
        summary = selection.summary;
      }
      draw();
    } catch (e) {
      if (stopped || n !== serial || c.signal.aborted) return;
      // Old successful metrics are removed before each read; never retain them
      // as a silent fallback after scope change, logout or recording failure.
      content.innerHTML = notice(failure(e));
      scope();
    }
  }
  function closeSearch() {
    menu.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }
  function drawResults() {
    $("#obs-run-options").innerHTML = results
      .map(
        (r, i) =>
          `<button type="button" class="obs-run-option" role="option" id="obs-option-${i}" aria-selected="${i === focusedIndex}" data-obs-option="${i}"><strong>${esc(r.name || r.turn_id)}</strong><span>${esc(r.agent)} · ${esc(r.status)}</span><small>${esc(r.turn_id)}</small></button>`,
      )
      .join("");
    $("#obs-search-more").hidden = !resultNext;
    if (focusedIndex >= 0)
      input.setAttribute("aria-activedescendant", `obs-option-${focusedIndex}`);
    else input.removeAttribute("aria-activedescendant");
  }
  async function search(more = false) {
    const n = ++searchSerial;
    searchController?.abort();
    const c = controller();
    searchController = c;
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
    $("#obs-search-state").textContent = "Searching…";
    try {
      const page = await read(
        "/v1/observability/runs?" +
          query({ q: searchText, limit: 10, after: more ? resultNext : null }),
        c,
      );
      if (stopped || n !== searchSerial) return;
      results = more
        ? [
            ...results,
            ...page.runs.filter(
              (r) => !results.some((x) => runKey(x) === runKey(r)),
            ),
          ]
        : page.runs;
      resultNext = page.next_cursor;
      focusedIndex = -1;
      drawResults();
      $("#obs-search-state").textContent = results.length
        ? `${results.length} matching runs${resultNext ? " · more available" : ""}`
        : "No matching runs.";
    } catch (e) {
      if (stopped || n !== searchSerial || c.signal.aborted) return;
      results = [];
      resultNext = null;
      drawResults();
      $("#obs-search-state").textContent = "Unable to search runs. Try again.";
    }
  }
  function choose(run) {
    if (!run) return;
    chosen = run;
    tool = null;
    tab = "timeline";
    input.value = "";
    searchText = "";
    closeSearch();
    ++searchSerial;
    searchController?.abort();
    clearTimeout(timer);
    load();
  }
  async function moreTimeline(button) {
    if (!chosen || !timelinePage?.next || button.disabled) return;
    button.disabled = true;
    const n = serial,
      c = controller();
    try {
      const value = await read(
        perRun(chosen) +
          "/timeline?" +
          query({
            after: timelinePage.next,
            through: timelineThrough,
            limit: Math.min(100, 1000 - timeline.length),
          }),
        c,
      );
      if (stopped || n !== serial) return;
      const cursors = new Set(timeline.map((r) => r.cursor));
      timeline.push(...value.page.steps.filter((r) => !cursors.has(r.cursor)));
      timelinePage = value.page;
      draw();
    } catch (e) {
      if (!stopped && n === serial && !c.signal.aborted) {
        $("#obs-timeline-error").textContent = failure(e);
        button.disabled = false;
      }
    }
  }
  async function loadHealth() {
    if (health || healthError || stopped) return;
    const el = $("#obs-health-content");
    el.textContent = "Loading…";
    const c = controller();
    try {
      health = await read("/v1/observability/health", c);
      if (stopped) return;
      el.innerHTML =
        hint(
          "Serving API instance only. Worker and other API counters are not aggregated here.",
        ) +
        `<dl class="obs-health-values"><div><dt>Recorder</dt><dd>${health.recording_enabled ? (health.recording ? "Enabled" : "Not reporting") : "Disabled"}</dd></div><div><dt>Exporter</dt><dd>${health.exporter_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Recording drops</dt><dd>${count(health.recording?.dropped)}</dd></div><div><dt>Write failures</dt><dd>${count(health.recording?.failed_writes)}</dd></div><div><dt>Export failures</dt><dd>${count(health.exporter?.export_failures)}</dd></div></dl><details><summary>Process counters</summary><pre>${esc(JSON.stringify(health, null, 2))}</pre></details>`;
    } catch (e) {
      if (!stopped && !c.signal.aborted) {
        healthError = true;
        el.textContent =
          e.status === 403
            ? "Administrator access required."
            : "Process health unavailable. Refresh to try again.";
      }
    }
  }
  root.addEventListener(
    "click",
    (e) => {
      const option = e.target.closest("[data-obs-option]");
      if (option) {
        choose(results[Number(option.dataset.obsOption)]);
        return;
      }
      const run = e.target.closest("[data-obs-run]");
      if (run) {
        choose(selection?.selection.runs[Number(run.dataset.obsRun)]);
        return;
      }
      const step = e.target.closest("[data-obs-step]");
      if (step) {
        selectedStep = step.dataset.obsStep;
        draw();
        root.querySelector(`[data-obs-step="${selectedStep}"]`)?.focus();
        return;
      }
      const t = e.target.closest("[data-obs-tool]");
      if (t) {
        if (!chosen) {
          content.prepend(
            notice("Choose a run to inspect individual tool calls."),
          );
          return;
        }
        tool = t.dataset.obsTool;
        tab = "timeline";
        draw();
        return;
      }
      const action = e.target.closest("[data-obs]")?.dataset.obs;
      if (action === "refresh") {
        health = null;
        healthError = false;
        if ($("#obs-health")?.open) loadHealth();
        load();
      }
      if (action === "recent") {
        chosen = null;
        tool = null;
        tab = "usage";
        pageCursor = null;
        pageStack = [];
        load();
      }
      if (action === "usage" || action === "timeline") {
        tab = action;
        draw();
      }
      if (action === "next") {
        pageStack.push(pageCursor);
        pageCursor = selection.selection.next_cursor;
        load();
      }
      if (action === "previous") {
        pageCursor = pageStack.pop() ?? null;
        load();
      }
      if (action === "more-timeline") moreTimeline(e.target.closest("button"));
      if (action === "clear-tool") {
        tool = null;
        draw();
      }
    },
    { signal: lifetime.signal },
  );
  input.addEventListener(
    "input",
    () => {
      searchText = input.value;
      clearTimeout(timer);
      ++searchSerial;
      searchController?.abort();
      results = [];
      resultNext = null;
      focusedIndex = -1;
      drawResults();
      menu.hidden = false;
      input.setAttribute("aria-expanded", "true");
      $("#obs-search-state").textContent = "Searching…";
      timer = setTimeout(() => search(), 250);
    },
    { signal: lifetime.signal },
  );
  input.addEventListener(
    "focus",
    () => {
      if (menu.hidden) {
        searchText = input.value;
        search();
      }
    },
    { signal: lifetime.signal },
  );
  input.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Escape") {
        ++searchSerial;
        searchController?.abort();
        clearTimeout(timer);
        closeSearch();
        e.preventDefault();
      }
      if (["ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        if (menu.hidden) {
          search();
          return;
        }
        focusedIndex = Math.max(
          0,
          Math.min(
            results.length - 1,
            focusedIndex + (e.key === "ArrowDown" ? 1 : -1),
          ),
        );
        drawResults();
        $("#obs-run-options")
          .querySelector('[aria-selected="true"]')
          ?.scrollIntoView({ block: "nearest" });
      }
      if (e.key === "Enter" && focusedIndex >= 0) {
        e.preventDefault();
        choose(results[focusedIndex]);
      }
    },
    { signal: lifetime.signal },
  );
  $("#obs-search-more").addEventListener(
    "click",
    () => {
      if (results.length >= 100) {
        $("#obs-search-state").textContent =
          "Narrow your search to see more matches.";
        return;
      }
      search(true);
    },
    { signal: lifetime.signal },
  );
  document.addEventListener(
    "click",
    (e) => {
      if (!root.querySelector(".obs-picker").contains(e.target)) {
        closeSearch();
      }
    },
    { signal: lifetime.signal },
  );
  document.addEventListener(
    "focusin",
    (e) => {
      if (!root.querySelector(".obs-picker").contains(e.target)) closeSearch();
    },
    { signal: lifetime.signal },
  );
  $("#obs-health")?.addEventListener(
    "toggle",
    () => {
      if ($("#obs-health").open) loadHealth();
    },
    { signal: lifetime.signal },
  );
  load();
  return () => {
    stopped = true;
    serial++;
    searchSerial++;
    clearTimeout(timer);
    lifetime.abort();
    for (const c of controllers) c.abort();
    controllers.clear();
    root.replaceChildren();
  };
}
