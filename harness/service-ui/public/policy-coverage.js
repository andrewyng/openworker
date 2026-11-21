import { esc } from "./observability-data.js";

const day = 86_400_000;
const windows = { 1: "Last 24 hours", 7: "Last 7 days", 30: "Last 30 days" };
const stages = { userInput: "User input", modelInput: "Model input", modelOutput: "Model output",
  toolCall: "Tool call", toolResult: "Tool result", event: "Event" };
const actions = { block: "Block", redact: "Redact", callback: "Callback", hook: "Hook" };
const number = new Intl.NumberFormat();
const time = new Intl.DateTimeFormat(undefined, {
  timeZone: "UTC", month: "short", day: "numeric", hour: "numeric", minute: "numeric",
});

function table(title, headers, rows, empty) {
  return rows.length ? '<div class="cp-table-wrap"><table class="cp-table" aria-label="' + title +
    '"><thead><tr>' + headers.map(label => '<th scope="col">' + label + '</th>').join("") +
    '</tr></thead><tbody>' + rows.join("") + '</tbody></table></div>' : '<p class="cp-empty">' + empty + '</p>';
}

function evidenceLink(row, days) {
  const params = new URLSearchParams({ policy: row.policy, days, outcome: "unavailable" });
  if (row.release || row.agent) params.set("agent", row.release || row.agent);
  return '<a class="cp-session-link" href="#compliance?' + esc(params.toString()) + '">View decisions</a>';
}

export function mountPolicyCoverage(root, { api, params, signal }) {
  const selected = params.get("release") || "";
  const days = Object.hasOwn(windows, params.get("days")) ? params.get("days") : "7";
  root.innerHTML = '<form class="cp-filters cp-toolbar">' +
    '<select name="release" aria-label="Agent release"><option value="">All releases</option></select>' +
    '<select name="days" aria-label="Time range">' +
    Object.entries(windows).map(([value, label]) => '<option value="' + value + '">' + label + '</option>').join("") +
    '</select><button class="button" type="submit">Apply</button></form>' +
    '<div class="cp-coverage" aria-live="polite"><p class="cp-empty">Loading policy coverage…</p></div>';
  const form = root.querySelector("form");
  const result = root.querySelector(".cp-coverage");
  form.elements.days.value = days;
  if (selected) form.elements.release.add(new Option(selected, selected, true, true));
  let generation = 0;

  async function load(release, range) {
    const request = ++generation;
    const until = Date.now() + 1;
    const query = new URLSearchParams({ from_ms: until - Number(range) * day, until_ms: until });
    if (release) query.set("release", release);
    result.setAttribute("aria-busy", "true");
    try {
      const coverage = await api("/v1/compliance/coverage?" + query, "GET", undefined, undefined, signal);
      if (signal.aborted || request !== generation) return;
      form.elements.release.replaceChildren(new Option("All releases", ""),
        ...coverage.releases.map(name => new Option(name, name)));
      form.elements.release.value = release;
      const configured = coverage.configured.map(row => '<tr><td><strong>' + esc(row.policy) +
        '</strong></td><td>' + esc(row.policy_version) + '</td><td>' + esc(stages[row.phase]) +
        '</td><td>' + esc(actions[row.action]) + '</td><td>' +
        esc(row.tools.length ? row.tools.join(", ") : "—") + '</td></tr>');
      const observed = coverage.observed.map(row => '<tr><td><strong>' + esc(row.policy) +
        '</strong><small class="cell-subtitle">' + esc(row.policy_version || "Version unavailable") +
        '</small></td><td>' + esc(row.release || row.agent || "Before session creation") +
        '</td><td>' + esc(stages[row.phase]) + '</td><td>' + number.format(row.checks) +
        '</td><td>' + (row.unavailable ? '<span class="cp-pill" data-tone="unavailable"><i></i>' +
          number.format(row.unavailable) + '</span> ' + evidenceLink(row, range) : "0") +
        '</td><td><time datetime="' + new Date(row.last_ms).toISOString() + '">' +
        esc(time.format(row.last_ms)) + '</time></td></tr>');
      result.innerHTML = '<section><h2>Configured checks</h2><p>' +
        (coverage.release_defaults_resolved === false ? "Release defaults could not be resolved. Showing deployment checks." :
          release ? "Defaults for new sessions using " + esc(release) + "." :
          "Deployment checks. Select a release to include its policy defaults.") + '</p>' +
        table("Configured checks", ["Policy", "Version", "Stage", "Action", "Tools"], configured, "No checks configured here.") +
        '</section><section><h2>Recorded checks</h2><p>' +
        (coverage.truncated ? "Showing 100 groups. Narrow the time range or choose a release for more detail." :
          "Journaled checks in the selected time range.") +
        '</p>' + table("Recorded checks", ["Policy", "Agent release", "Stage", "Checks", "Unavailable", "Last seen (UTC)"],
          observed, "No checks recorded in this time range.") + '</section>';
    } catch (error) {
      if (!signal.aborted && request === generation) {
        if (error.status === 404) {
          form.elements.release.replaceChildren(new Option("All releases", ""));
        }
        result.innerHTML = '<p class="cp-empty" role="alert">' +
          (error.status === 404 ? "This release is unavailable. Apply the filter to see all releases." :
            "Couldn’t load policy coverage. Check your access or try again.") + '</p>';
      }
    } finally {
      if (request === generation) result.removeAttribute("aria-busy");
    }
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    const release = form.elements.release.value;
    const range = form.elements.days.value;
    const url = new URLSearchParams({ days: range });
    if (release) url.set("release", release);
    history.replaceState(null, "", "#compliance/coverage?" + url);
    void load(release, range);
  }, { signal });
  void load(selected, days);
}
