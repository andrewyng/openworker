import { esc, duration, count } from "./observability-data.js";
const id = (s) => encodeURIComponent(s);
const path = (q) =>
  `/v1/sessions/${id(q.sessionId)}/observability/${id(q.turnId)}`;
/** Transport stays in the trusted application; the component never stores credentials. */
export function serviceTracingAdapter(request) {
  const get = (p, signal) => request(p, signal);
  return {
    search: async (q, signal) =>
      get(
        "/v1/observability/runs?" +
          new URLSearchParams({
            q: q.q || "",
            limit: "25",
            ...(q.after ? { after: q.after } : {}),
          }),
        signal,
      ),
    // The existing run directory is authorized and cursor-bound. Exact matching is
    // essential: its text search can also match another session's partial ID.
    turns: async (q, signal) => {
      const p = await get(
        "/v1/observability/runs?" +
          new URLSearchParams({
            q: q.sessionId,
            limit: "100",
            ...(q.after ? { after: q.after } : {}),
          }),
        signal,
      );
      return {
        turns: p.runs.filter((r) => r.session_id === q.sessionId),
        next_cursor: p.next_cursor,
      };
    },
    timeline: (q, signal) =>
      get(
        path(q) +
          "/timeline?" +
          new URLSearchParams({
            limit: "100",
            after: String(q.after ?? 0),
            ...(q.through != null ? { through: String(q.through) } : {}),
          }),
        signal,
      ),
    summary: (q, signal) => get(path(q) + "/summary", signal),
    children: (q, signal) => get(path(q) + "/children", signal),
  };
}
const svg = (kind) => {
  const shapes = {
    model: '<path d="m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3Z"/>',
    tool: '<path d="m8 5-5 7 5 7M16 5l5 7-5 7M14 4l-4 16"/>',
    agent:
      '<rect x="4" y="5" width="16" height="14" rx="4"/><path d="M9 10h.01M15 10h.01M9 14h6M12 2v3"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[kind] || shapes.agent}</svg>`;
};
const kind = (s) =>
  s.operation.startsWith("model.")
    ? "model"
    : s.operation.startsWith("tool.")
      ? "tool"
      : "agent";
const name = (s) =>
  (s.tool && s.tool.replaceAll("__", " · ").replaceAll("_", " ")) ||
  (s.model && s.operation === "model.attempt" && `Model · ${s.model}`) ||
  {
    "agent.execute": "Execution segment",
    "model.requested": "Request started",
    "policy.review": "Policy check",
    "approval.wait": "Approval wait",
    "question.wait": "Question wait",
    "environment.operation": "Prepare environment",
  }[s.operation] ||
  s.operation;
const key = (s) => s.trace_id + "/" + s.span_id;
const reason = (e) =>
  e?.status === 403
    ? "You cannot read this session."
    : e?.status === 404
      ? "This recording is no longer available."
      : e?.status === 503
        ? "Recording is disabled or unavailable."
        : e?.status === 429
          ? "Recording readers are busy. Try Refresh."
          : "Unable to load recording. Try Refresh.";
const button = (action, text, extra = "") =>
  `<button type="button" data-tr="${action}" ${extra}>${text}</button>`;
const scope = (r) => ({ sessionId: r.session_id, turnId: r.turn_id });
/** Session-oriented trace browser. No evaluations or combined team view. */
export function mountSessionTracing(
  root,
  { adapter, initialRun = null, onOverview } = {},
) {
  if (!adapter) throw Error("A tracing adapter is required");
  const lifetime = new AbortController(),
    signal = lifetime.signal;
  let disposed = false,
    searchGen = 0,
    sessionGen = 0,
    detailGen = 0,
    timer;
  let results = [],
    nextSearch = null,
    searchText = "",
    session = null,
    turns = [],
    nextTurns = null,
    history = [],
    cache = new Map(),
    selected = null,
    details = null,
    mode = "tree",
    tab = "preview",
    pending = false;
  root.classList.add("ow-session-tracing");
  root.innerHTML = `<div class="tr-heading"><div><h1>Tracing</h1><p>Follow a session, one turn at a time.</p></div><div>${onOverview ? button("overview", "Usage overview ↗") : ""}${button("refresh", "Refresh")}</div></div><div class="tr-toolbar"><input type="search" aria-label="Search sessions" placeholder="Search session name or ID…" maxlength="200"><span class="tr-directory-state" role="status"></span></div><div class="tr-workbench"><aside class="tr-directory"><div class="tr-panehead">Sessions</div><div class="tr-results"></div><div class="tr-directory-footer"></div></aside><section class="tr-execution"><div class="tr-panehead"><span>Execution</span><div class="tr-segment" role="group" aria-label="Execution view">${button("tree", "Tree", 'aria-pressed="true"')}${button("waterfall", "Waterfall", 'aria-pressed="false"')}</div></div><div class="tr-context"></div><div class="tr-turns"></div></section><section class="tr-inspector"><div class="tr-panehead">Step details</div><div class="tr-details"></div></section></div>`;
  const $ = (s) => root.querySelector(s);
  const query = () => $(".tr-toolbar input").value;
  function sessions() {
    const map = new Map();
    for (const r of results)
      if (!map.has(r.session_id)) map.set(r.session_id, r);
    return [...map.values()];
  }
  function directory() {
    const rs = sessions();
    $(".tr-results").innerHTML =
      rs
        .map(
          (r) =>
            `<button class="tr-session ${session?.session_id === r.session_id ? "selected" : ""}" data-tr="session" data-tr-session="${esc(r.session_id)}"><strong>${esc(r.name || r.agent || r.session_id)}</strong><span><i class="tr-status">${esc(r.status || "Unknown")}</i><small>${r.created_ms ? esc(new Date(r.created_ms).toLocaleDateString()) : ""}</small></span><code>${esc(r.session_id)}</code></button>`,
        )
        .join("") ||
      '<p class="tr-note">No matching sessions on this page.</p>';
    $(".tr-directory-footer").innerHTML = nextSearch
      ? button("more-search", "More sessions")
      : "";
  }
  async function search(more = false) {
    const gen = ++searchGen,
      q = searchText;
    $(".tr-directory-state").textContent = "Searching…";
    try {
      const page = await adapter.search(
        { q, after: more ? nextSearch : undefined },
        signal,
      );
      if (disposed || gen !== searchGen) return;
      results = (more ? [...results, ...page.runs] : page.runs).slice(0, 1000);
      nextSearch = results.length < 1000 ? page.next_cursor : null;
      $(".tr-directory-state").textContent =
        `${sessions().length} sessions loaded${nextSearch ? " · more available" : ""}`;
      directory();
      if (!session && results.length) await openSession(results[0]);
    } catch (e) {
      if (disposed || gen !== searchGen) return;
      results = [];
      nextSearch = null;
      directory();
      $(".tr-directory-state").textContent = reason(e);
    }
  }
  function crumb() {
    return `<div class="tr-breadcrumb">${button("home", "Sessions")}${history.length ? `<span>›</span>${button("back", esc(history.at(-1).session.name || history.at(-1).session.agent || "Parent agent"))}<span>›</span>${button("back", esc(history.at(-1).parentTurn))}` : ""}<span>›</span><strong>${esc(session?.name || session?.agent || "Session")}</strong></div>`;
  }
  function context() {
    if (!session) return;
    $(".tr-context").innerHTML =
      crumb() +
      `<h2>${esc(session.name || session.agent || session.session_id)}</h2><div class="tr-chips"><span>${esc(session.status || "Unknown")}</span><span>${turns.length} ${turns.length === 1 ? "turn" : "turns"} loaded${nextTurns ? " · more available" : ""}</span><code>${esc(session.session_id)}</code></div>`;
  }
  const visibleSteps = (c) =>
    (c.steps || []).filter((s) => s.operation !== "model.requested");
  function body(t, c) {
    if (c.error) return `<p class="tr-error" role="status">${esc(c.error)}</p>`;
    if (c.loading)
      return '<p class="tr-note" role="status">Loading recording…</p>';
    const ss = visibleSteps(c),
      known = ss.filter(
        (s) => Number.isFinite(s.started_ms) && Number.isFinite(s.duration_us),
      ),
      start = known.length ? Math.min(...known.map((s) => s.started_ms)) : 0,
      end = known.length
        ? Math.max(...known.map((s) => s.started_ms + s.duration_us / 1000))
        : 1,
      range = Math.max(1, end - start),
      summary = c.summary;
    const total = summary?.usage;
    const tokens =
      total?.input?.total != null && total?.output?.total != null
        ? `${count(total.input.total + total.output.total)}${total.input.unknown || total.output.unknown ? " + unknown" : ""}`
        : "Not reported";
    let html = `<div class="tr-turn-metrics"><span>${esc(duration(summary?.wall_elapsed_us))} elapsed</span><span>${tokens} tokens</span><span>${ss.length} steps loaded</span></div>`;
    if (mode === "waterfall")
      html += `<div class="tr-axis">${[0, 1, 2, 3, 4].map((i) => `<span>${i ? esc(duration(((range * i) / 4) * 1000)) : "0 s"}</span>`).join("")}</div>`;
    // Indentation follows recorded parent IDs only. Missing parents are roots;
    // cycles are bounded and labelled, never silently invented.
    const map = new Map(ss.map((s) => [key(s), s]));
    const depth = (s) => {
      let n = 0,
        p = s,
        seen = new Set([key(s)]);
      while (n < 6) {
        const parent = map.get(p.parent_trace_id + "/" + p.parent_span_id);
        if (!parent || seen.has(key(parent))) break;
        seen.add(key(parent));
        n++;
        p = parent;
      }
      return n;
    };
    for (const s of ss) {
      const d = depth(s),
        k = kind(s),
        timing =
          Number.isFinite(s.started_ms) && Number.isFinite(s.duration_us);
      html += `<button class="tr-step tr-${k} ${selected?.step && key(selected.step) === key(s) ? "selected" : ""} ${mode === "waterfall" ? "tr-waterfall" : ""}" data-tr="step" data-turn="${esc(t.turn_id)}" data-step="${esc(key(s))}" data-depth="${d}"><span class="tr-step-name">${svg(k)}<span>${esc(name(s))}</span></span>${mode === "tree" ? `<span class="tr-duration">${esc(duration(s.duration_us))}</span>` : `<span class="tr-track">${timing ? `<i data-left="${Math.min(99, Math.max(0, ((s.started_ms - start) / range) * 100))}" data-width="${Math.min(100, (s.duration_us / 1000 / range) * 100)}" class="${s.outcome === "error" ? "tr-failed" : ""}"></i>` : "<small>Unknown timing</small>"}</span>`}<span class="tr-outcome ${s.outcome === "error" ? "tr-error" : ""}">${esc(s.outcome || "Unknown")}</span></button>`;
    }
    if (c.next != null)
      html +=
        c.steps.length < 1000
          ? button(
              "more-steps",
              "Load more steps",
              `data-turn="${esc(t.turn_id)}"`,
            )
          : '<p class="tr-note">Showing 1,000 steps. Narrow the query through the SDK to inspect further records.</p>';
    if (!ss.length)
      html += '<p class="tr-note">No recorded steps for this turn.</p>';
    html += `<div class="tr-children">${(c.children || []).map((child, i) => `<div><span>${svg("agent")}${esc(child.agent_id)} <small>${esc(child.phase)}</small></span>${child.child_turn_id ? button("child", `Open session ↗`, `data-turn="${esc(t.turn_id)}" data-child="${i}" aria-label="Open ${esc(child.agent_id)} session"`) : "<small>Initial turn unavailable</small>"}</div>`).join("")}</div>`;
    if (c.childError)
      html +=
        '<p class="tr-note">Child links are unavailable for this turn.</p>';
    const cov = summary?.coverage;
    if (
      cov?.scan_truncated ||
      cov?.hierarchy_incomplete ||
      cov?.unknown_outcome_steps ||
      cov?.unknown_timing_steps
    )
      html +=
        '<p class="tr-note">Partial coverage: some timing, hierarchy or outcomes are incomplete. Recording is best effort.</p>';
    return html;
  }
  function render() {
    context();
    directory();
    $(".tr-turns").innerHTML =
      turns
        .map((t) => {
          const c = cache.get(t.turn_id);
          return `<section class="tr-turn"><button data-tr="turn" data-turn="${esc(t.turn_id)}" class="tr-turn-heading" aria-expanded="${!!c?.open}"><span class="tr-chevron ${c?.open ? "open" : ""}">${svg("chevron")}</span><span><b>${esc(t.label || "Turn · " + t.turn_id.slice(0, 14) + "…")}</b><small>${esc(t.status || "Status not reported")}</small></span></button>${c?.open ? `<div class="tr-calls">${body(t, c)}</div>` : ""}</section>`;
        })
        .join("") +
      (nextTurns != null ? button("more-turns", "Load more turns") : "") +
      (pending ? '<p class="tr-note" role="status">Loading turns…</p>' : "");
    root
      .querySelectorAll(".tr-segment button")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.tr === mode)),
      );
    for (const el of root.querySelectorAll("[data-depth]"))
      el.style.setProperty("--indent", el.dataset.depth);
    for (const el of root.querySelectorAll("[data-left]")) {
      el.style.left = el.dataset.left + "%";
      el.style.width = el.dataset.width + "%";
    }
  }
  async function loadTurns(more = false, target) {
    if (more && pending) return;
    const gen = sessionGen,
      sid = session.session_id;
    pending = true;
    render();
    try {
      const p = await adapter.turns(
        { sessionId: sid, after: more ? nextTurns : undefined },
        signal,
      );
      if (disposed || gen !== sessionGen) return;
      const incoming = p.turns.filter((t) => t.session_id === sid);
      if (!more && incoming[0])
        session = { ...session, status: incoming[0].status };
      const unique = new Map(
        (more ? [...turns, ...incoming] : incoming).map((t) => [t.turn_id, t]),
      );
      turns = [...unique.values()];
      turns = turns.slice(0, 1000);
      nextTurns = turns.length < 1000 ? p.next_cursor : null;
      pending = false;
      if (target && !turns.some((t) => t.turn_id === target.turn_id))
        turns.unshift({ ...target, session_id: sid });
      render();
      if (!more && turns.length) {
        const t = turns.find((t) => t.turn_id === target?.turn_id) || turns[0];
        await loadTurn(t);
      }
    } catch (e) {
      if (disposed || gen !== sessionGen) return;
      pending = false;
      turns = [];
      nextTurns = null;
      render();
      $(".tr-turns").innerHTML =
        `<p class="tr-error" role="status">${esc(reason(e))}</p>`;
    }
  }
  async function openSession(r, target) {
    sessionGen++;
    detailGen++;
    session = r;
    turns = [];
    cache = new Map();
    selected = null;
    details = null;
    nextTurns = null;
    $(".tr-details").innerHTML =
      '<p class="tr-note">Select a model or tool call.</p>';
    await loadTurns(false, target || r);
  }
  async function loadTurn(t, more = false) {
    const gen = sessionGen,
      old = cache.get(t.turn_id) || {},
      q = scope(t);
    const c = { ...old, open: true, loading: true };
    cache.set(t.turn_id, c);
    render();
    try {
      const [page, s, child] = await Promise.all([
        adapter.timeline(
          {
            ...q,
            after: more ? old.next : undefined,
            through: more ? old.through : undefined,
            limit: 100,
          },
          signal,
        ),
        more
          ? Promise.resolve({ summary: old.summary })
          : adapter.summary(q, signal),
        more
          ? Promise.resolve({ children: old.children })
          : adapter.children
            ? adapter
                .children(q, signal)
                .catch(() => ({ children: [], unavailable: true }))
            : Promise.resolve({ children: [] }),
      ]);
      if (disposed || gen !== sessionGen || cache.get(t.turn_id) !== c) return;
      const steps = [
        ...(more ? old.steps || [] : []),
        ...page.page.steps.map((x) => x.step),
      ];
      Object.assign(c, {
        steps: steps.slice(0, 1000),
        next: page.page.next,
        through: page.page.through,
        summary: s.summary,
        children: child.children,
        childError: child.unavailable,
        loading: false,
        error: null,
      });
      render();
    } catch (e) {
      if (disposed || gen !== sessionGen) return;
      Object.assign(c, {
        loading: false,
        error: reason(e),
        steps: [],
        children: [],
      });
      if (selected?.turn.turn_id === t.turn_id) {
        selected = null;
        details = null;
        detailGen++;
        $(".tr-details").innerHTML =
          '<p class="tr-note">Selected recording is unavailable.</p>';
      }
      render();
    }
  }
  function inspector() {
    const s = selected?.step;
    if (!s) return;
    const payload = (v) =>
      v === undefined || v === null
        ? '<div class="tr-empty">Content was not captured or is not available to this viewer.</div>'
        : `<pre>${esc(typeof v === "string" ? v : JSON.stringify(v, null, 2))}</pre>`;
    $(".tr-details").innerHTML =
      `<div class="tr-detail-title"><small>${esc(s.operation)}</small><h2>${esc(name(s))}</h2><div class="tr-chips"><span>${esc(s.outcome)}</span><span>${esc(duration(s.duration_us))}</span></div></div><div class="tr-tabs" role="group" aria-label="Step detail view">${["preview", "attributes", "json"].map((x) => button(x, x === "json" ? "JSON" : x[0].toUpperCase() + x.slice(1), `aria-pressed="${tab === x}"`)).join("")}</div><div class="tr-detail-body">${
        tab === "json"
          ? `<pre>${esc(JSON.stringify(s, null, 2))}</pre>`
          : tab === "attributes"
            ? `<dl>${Object.entries(s)
                .filter(([, v]) => v != null)
                .map(
                  ([k, v]) =>
                    `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`,
                )
                .join("")}</dl>`
            : `<h3>Input</h3>${payload(details?.input)}<h3>Output</h3>${payload(details?.output)}${details?.error ? `<p class="tr-note">${esc(details.error)}</p>` : ""}<h3>Execution</h3><dl><dt>Duration</dt><dd>${esc(duration(s.duration_us))}</dd><dt>First output</dt><dd>${esc(duration(s.first_output_us))}</dd><dt>Model</dt><dd>${esc(s.model || "—")}</dd><dt>Token usage</dt><dd>${s.usage ? esc(JSON.stringify(s.usage)) : "Not reported"}</dd><dt>Span</dt><dd>${esc(s.span_id)}</dd></dl>`
      }</div>`;
  }
  async function select(t, s) {
    selected = { turn: t, step: s };
    details = null;
    tab = "preview";
    const gen = ++detailGen,
      sessionVersion = sessionGen;
    render();
    inspector();
    if (adapter.stepDetails) {
      try {
        const value = await adapter.stepDetails(
          { ...scope(t), traceId: s.trace_id, spanId: s.span_id },
          signal,
        );
        if (disposed || gen !== detailGen || sessionVersion !== sessionGen)
          return;
        details = value;
        inspector();
      } catch (e) {
        if (!disposed && gen === detailGen) {
          details = { error: reason(e) };
          inspector();
        }
      }
    }
  }
  async function click(e) {
    const b = e.target.closest("button[data-tr]");
    if (!b || !root.contains(b)) return;
    const action = b.dataset.tr,
      t = turns.find((t) => t.turn_id === b.dataset.turn);
    if (action === "session") {
      history = [];
      await openSession(
        results.find((r) => r.session_id === b.dataset.trSession),
      );
    } else if (action === "turn") {
      const c = cache.get(t.turn_id);
      if (c?.open) {
        c.open = false;
        render();
      } else if (c?.steps) {
        c.open = true;
        render();
      } else await loadTurn(t);
    } else if (action === "more-steps") await loadTurn(t, true);
    else if (action === "step") {
      const s = cache
        .get(t.turn_id)
        ?.steps.find((s) => key(s) === b.dataset.step);
      if (s) await select(t, s);
    } else if (action === "child") {
      const c = cache.get(t.turn_id).children[Number(b.dataset.child)];
      history.push({
        session,
        turns,
        nextTurns,
        cache,
        selected,
        details,
        tab,
        mode,
        parentTurn: t.label || "Turn · " + t.turn_id.slice(0, 14) + "…",
        scroll: $(".tr-execution").scrollTop,
      });
      await openSession(
        {
          session_id: c.child_session_id,
          turn_id: c.child_turn_id,
          name: c.agent_id,
          status: c.phase,
        },
        { session_id: c.child_session_id, turn_id: c.child_turn_id },
      );
      $(".tr-execution").scrollTop = 0;
    } else if (action === "back") {
      const h = history.pop();
      if (h) {
        sessionGen++;
        detailGen++;
        ({ session, turns, nextTurns, cache, selected, details, tab, mode } =
          h);
        pending = false;
        render();
        $(".tr-execution").scrollTop = h.scroll;
        inspector();
      }
    } else if (action === "home") {
      history = [];
      if (results[0]) await openSession(results[0]);
    } else if (["tree", "waterfall"].includes(action)) {
      mode = action;
      render();
    } else if (["preview", "attributes", "json"].includes(action)) {
      tab = action;
      inspector();
    } else if (action === "more-search") await search(true);
    else if (action === "more-turns") await loadTurns(true);
    else if (action === "refresh") {
      if (session) await openSession(session, selected?.turn);
      else await search();
    } else if (action === "overview") onOverview?.();
  }
  root.addEventListener("click", click, { signal });
  $(".tr-toolbar input").addEventListener(
    "input",
    () => {
      clearTimeout(timer);
      searchText = query();
      searchGen++;
      timer = setTimeout(() => search(), 250);
    },
    { signal },
  );
  if (initialRun) openSession(initialRun);
  if (adapter.search) search();
  else $(".tr-toolbar").hidden = true;
  return {
    refresh: () => (session ? openSession(session) : search()),
    dispose() {
      disposed = true;
      searchGen++;
      sessionGen++;
      detailGen++;
      clearTimeout(timer);
      lifetime.abort();
      root.replaceChildren();
      root.classList.remove("ow-session-tracing");
    },
  };
}
