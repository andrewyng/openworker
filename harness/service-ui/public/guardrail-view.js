import { esc } from "./observability-data.js";
import { evalSelectionLink } from "./eval-selection.js";

const endpoint = "/v1/observability/guardrails";
const labels = { redacted: "Redacted", blocked: "Blocked", matched: "Matched", unavailable: "Check unavailable" };
const phaseLabel = (phase) => ({
  userInput: "User input", beforeInput: "Before user input",
  modelOutput: "Model output", beforeOutput: "Before output",
  toolArguments: "Tool arguments", beforeTool: "Before tool call",
  toolResult: "Tool result", afterTool: "After tool call",
}[phase] || phase);
const ruleLabel = (rule) => rule === "builtin.credit-card" ? "Credit card rule" : rule;

export function guardrailPath(filter = {}) {
  const query = new URLSearchParams(Object.entries(filter).filter(([, v]) => v != null && v !== ""));
  return query.size ? `${endpoint}?${query}` : endpoint;
}

function status(finding) {
  const type = finding.outcome === "blocked" ? "failed" : "error";
  return `<span class="ev-pill ev-${type}"><i></i>${esc(labels[finding.outcome] || finding.outcome)}</span>`;
}
function facts(finding) {
  return `<dl class="guardrail-facts">
    <dt>Rule ID</dt><dd><code>${esc(finding.rule)}</code></dd>
    ${finding.tag ? `<dt>Tag</dt><dd><code>${esc(finding.tag)}</code></dd>` : ""}
    ${finding.version ? `<dt>Version</dt><dd>${esc(finding.version)}</dd>` : ""}
    ${finding.match_count ? `<dt>Matches</dt><dd>${esc(finding.match_count)}</dd>` : ""}
    ${finding.duration_ms != null ? `<dt>Check duration</dt><dd>${esc(finding.duration_ms)} ms</dd>` : ""}
    <dt>Recorded</dt><dd>${esc(new Date(finding.occurred_ms).toLocaleString())}</dd>
  </dl>`;
}

export function guardrailRows(findings, linkToTrace = false, canEvaluate = false) {
  return `<div class="ev-list guardrail-list">${findings.map((finding) => {
    const link = `#observability/${encodeURIComponent(finding.session_id)}${finding.turn_id ? `/${encodeURIComponent(finding.turn_id)}` : ""}`;
    return `<details class="ev-case guardrail-finding">
      <summary>
        <span><strong>${esc(ruleLabel(finding.rule))}</strong>
          <small>${esc(phaseLabel(finding.phase))}${linkToTrace ? ` · ${esc(finding.session_name || finding.session_id)}` : ""}</small>
        </span>
        ${status(finding)}
      </summary>
      <div class="ev-case-detail">${facts(finding)}
        ${linkToTrace ? `<a href="${link}">${finding.turn_id ? "Open source turn" : "Open session"}</a>` : ""}
        ${canEvaluate && finding.turn_id && finding.tag ? `<a href="${evalSelectionLink(finding.tag)}">Evaluate matching turns</a>` : ""}
      </div>
    </details>`;
  }).join("")}</div>`;
}

export function mountGuardrailCatalog(root, request, canEvaluate) {
  const lifetime = new AbortController();
  const { signal } = lifetime;
  root.innerHTML = `
    <header class="ev-heading">
      <div><h1>Guardrails</h1><p>Find sessions where a guardrail matched, redacted, or blocked content.</p></div>
      <a class="button" href="#observability">Open tracing</a>
    </header>
    <section class="ev-section" aria-label="Guardrail activity">
      <form class="ev-list-toolbar">
        <label class="ev-list-search"><span class="ev-visually-hidden">Rule ID</span>
          <input name="rule" type="search" maxlength="128" placeholder="Filter by rule ID…">
        </label>
        <label><span class="ev-visually-hidden">Guardrail outcome</span><select name="outcome">
          <option value="">Guardrail hits</option><option value="redacted">Redacted</option>
          <option value="blocked">Blocked</option><option value="matched">Matched</option><option value="unavailable">Check unavailable</option>
        </select></label>
        <label><span class="ev-visually-hidden">Recorded time</span><select name="period">
          <option value="">Any time</option><option value="24">Past 24 hours</option><option value="168">Past 7 days</option>
        </select></label>
        <button class="button" type="submit">Apply</button>
      </form>
      <div data-guardrail-rows></div>
      <div class="ev-page-controls"><span class="ev-muted" role="status"></span><div>
        <button class="button" type="button" data-prev>Previous</button>
        <button class="button" type="button" data-next>Next</button>
      </div></div>
    </section>`;
  const form = root.querySelector("form");
  const result = root.querySelector("[data-guardrail-rows]");
  const info = root.querySelector("[role=status]");
  const prev = root.querySelector("[data-prev]");
  const next = root.querySelector("[data-next]");
  let generation = 0, cursors = [null], nextCursor, filters = {};
  async function fetchPage() {
    const current = ++generation;
    prev.disabled = next.disabled = true;
    info.textContent = "Loading guardrail activity…";
    try {
      const page = await request(guardrailPath({ ...filters, after: cursors.at(-1) }), signal);
      if (signal.aborted || current !== generation) return;
      result.innerHTML = page.findings.length ? guardrailRows(page.findings, true, canEvaluate)
        : '<div class="ev-empty"><h3>No matching guardrail activity</h3><p>Try another rule, outcome or time range.</p></div>';
      info.textContent = `Page ${cursors.length} · ${page.findings.length} shown`;
      nextCursor = page.next_cursor;
      next.disabled = !nextCursor;
    } catch {
      if (signal.aborted || current !== generation) return;
      result.innerHTML = '<div class="ev-empty" role="alert">Guardrail activity is unavailable.</div>';
      info.textContent = "";
    }
    if (!signal.aborted && current === generation) prev.disabled = cursors.length === 1;
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    filters = {
      rule: data.get("rule").trim(),
      outcome: data.get("outcome"),
      from_ms: data.get("period") ? Date.now() - Number(data.get("period")) * 3_600_000 : null,
    };
    cursors = [null];
    fetchPage();
  }, { signal });
  prev.addEventListener("click", () => {
    cursors.pop();
    fetchPage();
  }, { signal });
  next.addEventListener("click", () => {
    if (!nextCursor) return;
    cursors.push(nextCursor);
    fetchPage();
  }, { signal });
  fetchPage();
  return () => lifetime.abort();
}
