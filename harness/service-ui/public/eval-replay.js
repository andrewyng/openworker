import { esc } from "./observability-data.js";
import { datasetHref, evalEndpoint } from "./eval-lists.js";

const id = encodeURIComponent;
const path = evalEndpoint + "/replays";
const href = (job) => "#evals/replays/" + id(job);

export function replayForm() {
  return `<details class="ev-section ev-disclosure ev-run-selection" data-replay-config>
    <summary>Replay and compare releases</summary>
    <form class="ev-capture ev-evaluator-editor">
      <p class="ev-muted">Replay up to four cases with saved requests. Each sandbox starts with the case’s saved workspace files.</p>
      <label>Baseline release<select name="baseline" required></select></label>
      <label>Candidate release<select name="candidate" required></select></label>
      <label>Runtime profile<select name="runtime_profile"></select></label>
      <p role="alert" class="ev-error"></p>
      <div><button class="button primary" type="submit" disabled>Queue replay</button></div>
    </form>
  </details>`;
}

export function bindReplayForm(panel, { api,dataset,signal }) {
  if (!panel) return;
  const form = panel.querySelector("form");
  const button = form.querySelector("button");
  const error = form.querySelector("[role=alert]");
  let loaded = false, previous;
  panel.addEventListener("toggle", async () => {
    if (!panel.open || loaded) return;
    loaded = true;
    try {
      const [releases, profiles] = await Promise.all(
        ["/v1/releases", "/v1/profiles"].map((url) => api(url,"GET",undefined,undefined,signal)),
      );
      if (signal.aborted) return;
      const choices = releases.entries.filter((entry) => entry.activated);
      for (const field of ["baseline","candidate"]) {
        form.elements[field].replaceChildren(new Option("Choose release",""), ...choices.map((r) => new Option(r.id,r.id)));
      }
      form.elements.runtime_profile.replaceChildren(new Option("Release defaults",""),
        ...profiles.entries.filter((p) => p.activated).map((p) => new Option(p.id,p.id)));
      button.disabled = choices.length < 2;
      if (choices.length < 2) error.textContent = "Replay requires two visible, activated release revisions.";
    } catch {
      loaded = false;
      if (!signal.aborted) error.textContent = "Couldn’t load releases and runtime profiles.";
    }
  }, { signal });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    body.dataset_version = dataset.version;
    body.runtime_profile ||= null;
    if (body.baseline === body.candidate) {
      error.textContent = "Choose different release revisions.";
      return;
    }
    const snapshot = JSON.stringify(body);
    if (previous?.snapshot !== snapshot) previous = { snapshot, key: crypto.randomUUID() };
    error.textContent = "";
    button.disabled = true;
    try {
      const job = await api(evalEndpoint + "/datasets/" + id(dataset.id) + "/replays","POST",body,previous.key,signal);
      if (!signal.aborted) location.hash = href(job.id);
    } catch {
      if (!signal.aborted) error.textContent = "Couldn’t queue replay. Check release access and use a version with one to four saved requests.";
    } finally {
      button.disabled = false;
    }
  }, { signal });
}

function cell(run) {
  const result = run.result;
  const label = !result ? "Waiting" : result.status === "incomplete" ? "Incomplete" : result.passed ? "Passed" : "Failed";
  const tone = { Waiting:"queued",Incomplete:"error",Passed:"passed",Failed:"failed" }[label];
  const link = run.session_id && run.turn_id
    ? `<a class="ev-secondary-link" href="#observability/${id(run.session_id)}/${id(run.turn_id)}">Open execution ↗</a>` : "";
  const metrics = result?.status === "scored" ? `<small class="ev-table-subtitle">
    ${result.latency_ms == null ? "Latency unavailable" : esc((result.latency_ms / 1000).toFixed(2)) + " s"}
    · ${result.input_tokens ?? "—"} in / ${result.output_tokens ?? "—"} out</small>` : "";
  return `<td><span class="ev-pill ev-${tone}"><i></i>${label}</span>
    ${result?.reason ? `<small class="ev-table-subtitle">${esc(result.reason)}</small>` : ""}
    ${metrics}<small class="ev-table-subtitle">${link}</small></td>`;
}

export async function mountReplays(root,{api,jobId,signal}) {
  let timer;
  signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  const breadcrumb = '<nav class="ev-breadcrumb" aria-label="Evaluation breadcrumb"><a href="#evals">Evals</a><span aria-hidden="true">/</span><a href="#evals/replays">Replays</a></nav>';
  async function load() {
    try {
      const data=await api(path+(jobId?"/"+id(jobId):""),"GET",undefined,undefined,signal);
      if (signal.aborted) return;
      if (!jobId) {
        root.innerHTML = breadcrumb + '<header class="ev-heading"><div><h1>Replays</h1><p>Compare agent releases against a saved dataset version.</p></div></header>' +
          `<section class="ev-section"><div class="ev-list">${data.replays.map((job) =>
            `<div class="ev-replay-row"><a href="${href(job.id)}">${esc(job.dataset_name)} · v${job.request.version}</a>
              <span>${esc(job.request.baseline)} → ${esc(job.request.candidate)}</span></div>`).join("") ||
            '<div class="ev-empty">Start a replay from a dataset to see it here.</div>'}</div></section>`;
        return;
      }
      const { runs,request }=data;
      const rows=[];
      const changes = new Map();
      for (let i=0;i<runs.length;i+=2) {
        const [baseline,candidate]=runs.slice(i,i+2);
        const change=baseline.result?.status==="scored" && candidate.result?.status==="scored"
          ? baseline.result.passed===candidate.result.passed ? "No change" : candidate.result.passed ? "Improved" : "Regression"
          : runs[i].result && runs[i+1].result ? "Incomplete" : "Waiting";
        changes.set(change, (changes.get(change) || 0) + 1);
        const fixtureCount = baseline.source.fixture_files || 0;
        const tone = { Regression:"failed", Improved:"passed", Incomplete:"error", Waiting:"queued", "No change":"queued" }[change];
        rows.push(`<tr><th scope="row">${esc(baseline.source.name || "Dataset case")}<small class="ev-table-subtitle"><span class="ev-pill ev-${tone}"><i></i>${change}</span>${fixtureCount ? ` · ${fixtureCount} fixture ${fixtureCount === 1 ? "file" : "files"}` : ""}</small></th>
          ${cell(baseline)}${cell(candidate)}</tr>`);
      }
      const counts = [["Regression","regression","regressions"], ["Improved","improvement","improvements"],
        ["Incomplete","incomplete","incomplete"], ["Waiting","waiting","waiting"], ["No change","unchanged","unchanged"]];
      const summary = counts.flatMap(([key, singular, plural]) => {
        const count = changes.get(key);
        return count ? [`${count} ${count === 1 ? singular : plural}`] : [];
      }).join(" · ");
      root.innerHTML = breadcrumb + `<header class="ev-heading"><div><h1>Replay comparison</h1>
        <p><a href="${datasetHref(data.dataset_id)}?version=${request.version}">${esc(data.dataset_name)} · v${request.version}</a></p></div></header>
        ${summary ? `<p class="ev-muted" role="status">${summary}</p>` : ""}
        <section class="ev-section ev-table-shell"><table class="ev-table" aria-label="Replay comparison">
          <thead><tr><th>Case</th><th>Baseline<small class="ev-table-subtitle">${esc(request.baseline)}</small></th>
          <th>Candidate<small class="ev-table-subtitle">${esc(request.candidate)}</small></th></tr></thead><tbody>${rows.join("")}</tbody>
        </table></section>`;
      if (runs.some((run) => !run.result)) timer=setTimeout(load,1500);
    } catch {
      if (!signal.aborted) root.innerHTML=breadcrumb+'<p class="ev-section ev-empty" role="alert">Couldn’t load this replay.</p>';
    }
  }
  await load();
}
