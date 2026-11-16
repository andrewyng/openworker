const $ = (id) => document.getElementById(id);
let rows = [],
  after = null,
  selected = null,
  snapshot = null,
  events = [],
  stream = null,
  revision = 0,
  refreshTimer = null;
const human = (s) => (s || "idle").replaceAll("_", " ");
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
async function api(path) {
  const r = await fetch("/api" + path);
  if (r.status === 401) {
    loggedOut();
    throw Error("Sign in again to continue.");
  }
  if (!r.ok)
    throw Error(
      r.status === 503
        ? "The APIs are unavailable. Displayed data may be stale."
        : `Request unavailable (${r.status}).`,
    );
  return r.json();
}
function notice(text) {
  $("notice").hidden = !text;
  $("notice").textContent = text || "";
}
function loggedOut() {
  revision++;
  stream?.close();
  clearInterval(refreshTimer);
  $("app").hidden = true;
  $("login").hidden = false;
}
function renderRows() {
  const term = $("search").value.toLowerCase();
  $("session-rows").replaceChildren();
  for (const s of rows.filter((r) =>
    [r.name, r.id, r.coworker].join(" ").toLowerCase().includes(term),
  )) {
    const b = node(
      "button",
      undefined,
      "session-row" + (s.id === selected ? " selected" : ""),
    );
    b.setAttribute("aria-pressed", String(s.id === selected));
    const title = node("div", undefined, "row-title");
    title.append(
      node("span", s.name || "Untitled session", "row-name"),
      node("span", undefined, `tiny-status ${s.status}`),
    );
    const meta = node("div", undefined, "row-meta");
    meta.append(
      node("span", human(s.closed ? "closed" : s.status)),
      node("span", "·"),
      node("span", s.coworker),
    );
    b.append(title, meta);
    b.onclick = () => select(s.id);
    $("session-rows").append(b);
  }
  if (!$("session-rows").children.length)
    $("session-rows").append(
      node(
        "p",
        rows.length
          ? "No matching sessions."
          : "No sessions yet. Start a task through your app or the reference client.",
        "small-empty",
      ),
    );
  $("more").hidden = !after;
  $("session-count").textContent = rows.length + (after ? "+" : "");
  $("nav-count").textContent = rows.length;
  $("waiting-count").textContent = rows.filter((r) => r.pending > 0).length;
}
async function list(more = false) {
  try {
    const r = await api(
      "/sessions?limit=50" +
        (more && after ? "&after=" + encodeURIComponent(after) : ""),
    );
    rows = more
      ? [...rows, ...r.sessions.filter((s) => !rows.some((x) => x.id === s.id))]
      : r.sessions;
    after = r.next_after;
    renderRows();
    $("connection").textContent = "Connected";
    $("connection").className = "connection good";
    notice("");
  } catch (e) {
    notice(e.message);
    $("connection").textContent = "Reconnecting";
    $("connection").className = "connection";
  }
}
function textOf(v) {
  if (typeof v === "string") return v;
  if (Array.isArray(v))
    return v
      .map((x) => (typeof x.text === "string" ? x.text : ""))
      .filter(Boolean)
      .join("\n");
  return "";
}
function renderSnapshot(s) {
  snapshot = s;
  const session = s.session,
    status = session.closed ? "closed" : s.turn?.status || "idle";
  $("session-title").textContent = session.name || "Untitled session";
  $("session-status").textContent = human(status);
  $("session-status").className = "badge " + status;
  $("copy-id").textContent = session.id + " ⧉";
  $("coworker").textContent = session.coworker;
  $("model").textContent =
    session.model_selection?.model || session.model || "—";
  $("cursor").textContent = s.cursor;
  $("mode").textContent =
    s.mode === "scripted-test" ? "Scripted qualification" : "";
  $("messages").replaceChildren();
  for (const m of s.messages) {
    const text = textOf(m.content);
    if (!text) continue;
    const row = node("div", undefined, "message"),
      agent = m.role !== "user";
    row.append(
      node("div", agent ? "ow" : "U", "avatar" + (agent ? " agent" : "")),
    );
    const content = node("div");
    content.append(
      node("strong", agent ? "Coworker" : "User"),
      node("p", text),
    );
    row.append(content);
    $("messages").append(row);
  }
  if (!$("messages").children.length)
    $("messages").append(
      node(
        "p",
        "The conversation will appear here when the first turn is accepted.",
        "small-empty",
      ),
    );
  $("truncated").hidden = !s.history_truncated;
  $("decisions").replaceChildren();
  for (const i of s.interactions) {
    const card = node("div", undefined, "decision");
    card.append(
      node(
        "p",
        i.kind === "approval" ? "DECISION NEEDED" : "QUESTION WAITING",
        "eyebrow",
      ),
    );
    let args;
    try {
      args = JSON.parse(i.call?.arguments || "{}");
    } catch {
      args = {};
    }
    card.append(
      node(
        "h3",
        i.kind === "approval"
          ? `Approval requested · ${i.call?.name || "tool"}`
          : args.question || "The agent has a question",
      ),
    );
    card.append(
      node(
        "p",
        i.kind === "approval"
          ? "Execution is paused until an authorized response is recorded."
          : (args.options || []).join(" · "),
      ),
    );
    const details = node("details");
    details.append(
      node("summary", "Inspect requested action"),
      node(
        "pre",
        JSON.stringify(
          { interaction_id: i.id, tool: i.call?.name, arguments: args },
          null,
          2,
        ),
      ),
    );
    card.append(
      details,
      node(
        "p",
        "Respond through your application or the reference CLI.",
        "fine",
      ),
    );
    $("decisions").append(card);
  }
  const uncertain = ["interrupted", "recoverable"].includes(s.turn?.status);
  $("recovery").replaceChildren();
  if (uncertain)
    $("recovery").append(
      node(
        "p",
        s.turn.status === "interrupted"
          ? "Execution was interrupted. An external action may have completed or may still be running. Inspect its original environment before retrying."
          : "This session was recovered. Its durable pending decision is preserved; recovery is not approval to repeat an action.",
        "recovery",
      ),
    );
}
async function details(id, version) {
  try {
    const s = await api("/sessions/" + id);
    if (version === revision) renderSnapshot(s);
  } catch (e) {
    if (version === revision) notice(e.message);
  }
}
async function artifacts(id, version) {
  try {
    const r = await api(`/sessions/${id}/artifacts`);
    if (version !== revision) return;
    $("artifacts").replaceChildren();
    $("artifact-count").textContent = r.artifacts.length;
    $("artifact-note").textContent = "";
    for (const a of r.artifacts) {
      const row = node("div", undefined, "artifact"),
        body = node("div");
      body.append(
        node("strong", a.name),
        node(
          "p",
          `${(a.size / 1024).toFixed(1)} KB · ${a.content_type} · ${a.status}`,
        ),
        node("code", "SHA-256 " + a.sha256),
      );
      row.append(body);
      if (a.status === "ready") {
        const link = node("a", "Download ↓");
        link.href = `/api/sessions/${id}/artifacts/${a.id}/content`;
        link.setAttribute("download", "");
        row.append(link);
      }
      $("artifacts").append(row);
    }
    if (!r.artifacts.length)
      $("artifacts").append(
        node(
          "p",
          "No saved artifacts in this session. Files appear here only after the application explicitly registers them.",
          "small-empty",
        ),
      );
  } catch {
    if (version === revision) {
      $("artifacts").replaceChildren(
        node(
          "p",
          "Artifact storage is unavailable or not configured.",
          "small-empty",
        ),
      );
      $("artifact-count").textContent = "—";
    }
  }
}
function renderEvents() {
  const container = $("events");
  container.replaceChildren();
  for (const e of events.slice(-80).reverse()) {
    const row = node("div", undefined, "event");
    row.append(
      node("span", String(e.event_id).padStart(3, "0"), "event-id"),
      node("span", human(e.type), "event-name"),
      node("span", e.data?.status || "", "event-kind"),
    );
    container.append(row);
  }
  $("event-count").textContent = events.length === 80 ? "80+" : events.length;
  if (!events.length)
    container.append(node("p", "Waiting for execution events…", "small-empty"));
}
async function select(id) {
  selected = id;
  location.hash = id;
  const version = ++revision;
  stream?.close();
  events = [];
  renderRows();
  renderEvents();
  $("empty").hidden = true;
  $("selected").hidden = false;
  $("session-title").textContent = "Loading session…";
  $("decisions").replaceChildren();
  $("messages").replaceChildren();
  $("recovery").replaceChildren();
  $("stream-state").textContent = "Connecting";
  await details(id, version);
  if (version !== revision) return;
  await artifacts(id, version);
  if (version !== revision) return;
  stream = new EventSource(`/api/sessions/${id}/events?after=0`);
  let last = 0,
    timer;
  stream.onopen = () => {
    if (version === revision) $("stream-state").textContent = "● Live";
  };
  stream.onerror = () => {
    if (version === revision) $("stream-state").textContent = "Reconnecting…";
  };
  stream.addEventListener("stream_error", () => {
    if (version === revision) {
      $("stream-state").textContent = "Reconciling…";
      details(id, version);
    }
  });
  stream.addEventListener("progress", (message) => {
    if (version !== revision) return;
    try {
      const e = JSON.parse(message.data);
      if (e.event_id <= last) return;
      if (last && e.event_id !== last + 1) {
        notice("A progress gap was detected. Refreshing durable state.");
        details(id, version);
      }
      last = e.event_id;
      events.push(e);
      if (events.length > 80) events.shift();
      renderEvents();
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (version === revision) {
          details(id, version);
          artifacts(id, version);
        }
      }, 350);
    } catch {
      notice("Progress could not be decoded. Refresh the session.");
    }
  });
}
async function begin() {
  try {
    await api("/readyz");
    $("login").hidden = true;
    $("app").hidden = false;
    const c = await (await fetch("/config")).json();
    if (c.grafana_url) {
      $("dashboards").href = c.grafana_url;
      $("dashboards").hidden = false;
    }
    await list();
    const id = location.hash.slice(1);
    if (/^[A-Za-z0-9_-]{1,80}$/.test(id)) select(id);
    clearInterval(refreshTimer);
    refreshTimer = setInterval(() => list(), 15000);
  } catch {
    loggedOut();
  }
}
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const r = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: $("key").value }),
    });
    if (!r.ok)
      throw Error(
        r.status === 429
          ? "Please wait a minute before trying again."
          : "That access key was not accepted.",
      );
    $("key").value = "";
    await begin();
  } catch (e) {
    $("login-error").textContent = e.message;
  }
};
$("logout").onclick = async () => {
  await fetch("/logout", { method: "POST" });
  loggedOut();
};
$("search").oninput = renderRows;
$("more").onclick = () => list(true);
$("refresh").onclick = async () => {
  await list();
  if (selected) select(selected);
};
$("sessions-nav").onclick = () => list();
$("copy-id").onclick = () =>
  navigator.clipboard.writeText(selected).then(() => {
    $("copy-id").textContent = "Copied session ID";
  });
for (const b of document.querySelectorAll("[data-tab]"))
  b.onclick = () => {
    for (const t of document.querySelectorAll("[data-tab]")) {
      const on = t === b;
      t.setAttribute("aria-selected", String(on));
      $("panel-" + t.dataset.tab).hidden = !on;
    }
  };
begin();
