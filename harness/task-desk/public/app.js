const $ = (id) => document.getElementById(id),
  el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
let rows = [],
  next = null,
  selected = null,
  snapshot = null,
  source = null,
  revision = 0,
  timer = null,
  refreshTimer = null,
  busy = false,
  cursor = 0,
  delta = "";
const human = (s) => (s || "ready").replaceAll("_", " "),
  text = (v) =>
    typeof v === "string"
      ? v
      : Array.isArray(v)
        ? v
            .map((x) => x.text || "")
            .filter(Boolean)
            .join("\n")
        : "";
const notice = (s) => {
  $("notice").textContent = s || "";
  $("notice").hidden = !s;
};
function loggedOut() {
  revision++;
  source?.close();
  clearInterval(refreshTimer);
  $("app").hidden = true;
  $("login").hidden = false;
}
async function request(path, payload) {
  const r = await fetch(path, {
    ...(payload === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }),
  });
  if (r.status === 401) {
    loggedOut();
    throw Error("Please sign in to continue.");
  }
  if (!r.ok) {
    let data = {};
    try {
      data = await r.json();
    } catch {}
    throw Error(
      data.error === "outcome_unknown_retry_same_request"
        ? "Connection interrupted. Retry to reconcile the same request, without starting it twice."
        : `Request unavailable (${r.status}): ${data.error || "try again"}`,
    );
  }
  return r.json();
}
const api = (p, b) => request("/api" + p, b);
// Persist the original intent before sending: retries/reload reuse its IDs and payload.
async function mutation(key, path, payload) {
  const storage = "taskdesk:intent:" + key;
  let intent = JSON.parse(localStorage.getItem(storage) || "null");
  const comparable = (v) =>
    JSON.stringify(
      Object.fromEntries(
        Object.entries(v).filter(
          ([k]) => !["request_id", "submission_id"].includes(k),
        ),
      ),
    );
  if (
    intent &&
    (intent.path !== path || comparable(intent.payload) !== comparable(payload))
  ) {
    throw Error(
      "An earlier response has an uncertain outcome. Retry that original response first; a different decision cannot replace it silently.",
    );
  }
  if (!intent) {
    intent = { path, payload };
    localStorage.setItem(storage, JSON.stringify(intent));
  }
  const result = await api(intent.path, intent.payload);
  localStorage.removeItem(storage);
  return result;
}
async function guarded(fn) {
  if (busy) return;
  busy = true;
  notice("");
  try {
    await fn();
  } catch (e) {
    notice(e.message);
  } finally {
    busy = false;
  }
}
function renderRows() {
  $("count").textContent = rows.length + (next ? "+" : "");
  $("tasks").replaceChildren();
  for (const r of rows) {
    const b = el(
      "button",
      undefined,
      "task-row" + (r.id === selected ? " selected" : ""),
    );
    b.setAttribute("aria-pressed", String(r.id === selected));
    b.append(el("span", undefined, "dot " + r.status));
    const t = el("div");
    t.append(
      el("strong", r.name || "Untitled task"),
      el("small", human(r.status)),
    );
    b.append(t);
    b.onclick = () => guarded(() => select(r.id));
    $("tasks").append(b);
  }
  if (!rows.length)
    $("tasks").append(el("p", "Your next task starts here.", "muted"));
  $("more").hidden = !next;
}
async function list(more = false) {
  const r = await api(
    "/sessions?limit=50" +
      (more && next ? "&after=" + encodeURIComponent(next) : ""),
  );
  rows = more
    ? [...rows, ...r.sessions.filter((x) => !rows.some((y) => x.id === y.id))]
    : r.sessions;
  next = r.next_after;
  renderRows();
  $("connection").textContent = "Connected";
}
function home() {
  revision++;
  source?.close();
  selected = null;
  snapshot = null;
  location.hash = "";
  $("welcome").hidden = false;
  $("detail").hidden = true;
  $("breadcrumb").textContent = "Overview";
  renderRows();
}
function render(s) {
  snapshot = s;
  const status = s.turn?.status || "ready";
  $("task-title").textContent = s.session.name;
  $("breadcrumb").textContent = "Task";
  $("status").textContent = human(status);
  $("messages").replaceChildren();
  for (const m of s.messages) {
    const value = text(m.content);
    if (!value) continue;
    const card = el("article", undefined, "message " + m.role),
      label = el("div", undefined, "byline");
    label.append(
      el("span", m.role === "user" ? "YOU" : "OW"),
      el("strong", m.role === "user" ? "You" : "Coworker"),
    );
    card.append(label, el("p", value));
    $("messages").append(card);
  }
  const pending = ["queued", "running", "waiting", "recoverable"].includes(
    status,
  );
  $("send").disabled = pending;
  $("followup").disabled = pending;
  $("cancel").hidden = !pending;
  $("export").hidden = status !== "completed";
  $("turn-hint").textContent = pending
    ? "This task is still in progress."
    : "Continue the conversation";
  $("mode").textContent =
    s.mode === "scripted-test" ? "Deterministic test mode" : "";
  if (
    ["completed", "failed", "cancelled", "interrupted", "truncated"].includes(
      status,
    )
  ) {
    delta = "";
    $("delta").hidden = true;
  }
  if (status === "interrupted")
    notice(
      "Execution was interrupted. An external action may still have completed. Inspect the activity before starting another task.",
    );
  $("decisions").replaceChildren();
  for (const i of s.interactions.filter((x) => !x.response)) {
    const card = el("section", undefined, "decision");
    let args;
    try {
      args = JSON.parse(i.call?.arguments || "{}");
    } catch {
      args = {};
    }
    card.append(
      el(
        "div",
        i.kind === "approval" ? "YOUR APPROVAL" : "A QUICK QUESTION",
        "eyebrow",
      ),
      el(
        "h3",
        i.kind === "approval"
          ? `Allow ${i.call?.name || "this action"}?`
          : args.question || "How should I proceed?",
      ),
    );
    const d = el("details");
    d.append(
      el("summary", "View action details"),
      el("pre", JSON.stringify(args, null, 2)),
    );
    card.append(d);
    const actions = el("div", undefined, "actions");
    const answer = (value) =>
      guarded(async () => {
        const binding = Object.fromEntries(
          [
            "bundle_digest",
            "schema_digest",
            "policy_digest",
            "resource_policy_digest",
          ].map((k) => [k, i[k]]),
        );
        await mutation(
          "decision:" + i.id,
          `/sessions/${s.session.id}/controls`,
          {
            request_id: crypto.randomUUID(),
            turn_id: i.turn_id,
            action: {
              kind: "response",
              interaction_id: i.id,
              response: value,
              binding,
            },
          },
        );
        notice("Response submitted. Waiting for the worker to apply it.");
        await refresh();
      });
    if (i.kind === "approval") {
      for (const [label, value] of [
        ["Allow once", "once"],
        ["Allow for session", "session"],
        ["Deny", "deny"],
      ]) {
        const b = el("button", label, value === "once" ? "primary" : "");
        b.onclick = () => answer(value);
        actions.append(b);
      }
    } else {
      const input = el("input");
      input.placeholder = "Your answer";
      input.setAttribute("aria-label", "Answer");
      card.append(input);
      for (const value of args.options || []) {
        const b = el(
          "button",
          typeof value === "string" ? value : JSON.stringify(value),
        );
        b.onclick = () => answer(b.textContent);
        actions.append(b);
      }
      const b = el("button", "Send answer", "primary");
      b.onclick = () => input.value.trim() && answer(input.value.trim());
      actions.append(b);
    }
    card.append(actions);
    $("decisions").append(card);
  }
}
async function artifacts(id = selected, version = revision) {
  if (!id) return;
  const a = await api(`/sessions/${id}/artifacts`);
  if (version !== revision) return;
  $("artifacts").replaceChildren();
  for (const item of a.artifacts || a) {
    if (item.status !== "ready") continue;
    const link = el("a", undefined, "artifact");
    link.href = `/api/sessions/${id}/artifacts/${item.id}/content`;
    link.append(
      el("b", "↓"),
      el("span", item.name),
      el("small", `${Math.ceil(item.size / 1024) || 1} KB · Download`),
    );
    $("artifacts").append(link);
  }
  if (!$("artifacts").children.length)
    $("artifacts").append(
      el("p", "Save the completed response here.", "muted"),
    );
}
async function refresh(id = selected, version = revision) {
  if (!id) return;
  const s = await api("/sessions/" + id);
  if (version === revision) render(s);
}
async function select(id) {
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(id)) return;
  const version = ++revision;
  selected = id;
  location.hash = id;
  source?.close();
  cursor = 0;
  delta = "";
  $("delta").hidden = true;
  $("activity").replaceChildren();
  $("welcome").hidden = true;
  $("detail").hidden = false;
  renderRows();
  await refresh(id, version);
  if (version !== revision) return;
  await artifacts(id, version);
  if (version !== revision) return;
  source = new EventSource(`/api/sessions/${id}/events?after=0`);
  source.onopen = () => {
    $("stream-state").textContent = "Live";
  };
  source.onerror = () => {
    $("stream-state").textContent = "Reconnecting";
  };
  source.addEventListener("stream_error", () => guarded(() => refresh()));
  source.addEventListener("progress", (e) => {
    if (version !== revision) return;
    const event = JSON.parse(e.data);
    if (event.event_id <= cursor) return;
    if (cursor && event.event_id !== cursor + 1)
      notice("Refreshing after a progress gap.");
    cursor = event.event_id;
    if (event.type === "assistant_delta") {
      if (event.event_id > snapshot.cursor) {
        delta += event.data.text || event.data.delta || "";
        $("delta").textContent = delta;
        $("delta").hidden = !delta;
      }
    } else {
      const item = el(
        "li",
        human(event.type) + (event.data.name ? " · " + event.data.name : ""),
      );
      $("activity").prepend(item);
      while ($("activity").children.length > 35)
        $("activity").lastChild.remove();
    }
    if (!timer)
      timer = setTimeout(() => {
        timer = null;
        if (version === revision)
          Promise.all([
            refresh(id, version),
            artifacts(id, version),
            list(),
          ]).catch((e) => notice(e.message));
      }, 500);
  });
}
$("create-form").onsubmit = (e) => {
  e.preventDefault();
  guarded(async () => {
    const cache = "taskdesk:create";
    let intent = JSON.parse(localStorage.getItem(cache) || "null");
    if (!intent) {
      intent = {
        request_id: crypto.randomUUID(),
        submission_id: crypto.randomUUID(),
        text: $("prompt").value.trim(),
      };
      if (!intent.text) return;
      localStorage.setItem(cache, JSON.stringify(intent));
    }
    $("create-button").disabled = true;
    try {
      const made = await api("/sessions", {
        request_id: intent.request_id,
        name: intent.text.slice(0, 80),
      });
      await api(`/sessions/${made.session_id}/turns`, {
        submission_id: intent.submission_id,
        text: intent.text,
      });
      localStorage.removeItem(cache);
      $("prompt").value = "";
      await list();
      await select(made.session_id);
    } finally {
      $("create-button").disabled = false;
    }
  });
};
$("followup-form").onsubmit = (e) => {
  e.preventDefault();
  guarded(async () => {
    const value = $("followup").value.trim();
    if (!value) return;
    await mutation("turn:" + selected, `/sessions/${selected}/turns`, {
      submission_id: crypto.randomUUID(),
      text: value,
    });
    $("followup").value = "";
    await refresh();
  });
};
$("cancel").onclick = () =>
  guarded(async () => {
    await mutation(
      "cancel:" + snapshot.turn.id,
      `/sessions/${selected}/controls`,
      {
        request_id: crypto.randomUUID(),
        turn_id: snapshot.turn.id,
        action: { kind: "cancel" },
      },
    );
    notice(
      "Stop requested. Already-started external actions may still finish.",
    );
    await refresh();
  });
$("export").onclick = () =>
  guarded(async () => {
    await api(`/sessions/${selected}/result`, { turn_id: snapshot.turn.id });
    await artifacts();
  });
$("new-task").onclick = home;
$("more").onclick = () => guarded(() => list(true));
for (const b of document.querySelectorAll("[data-prompt]"))
  b.onclick = () => {
    $("prompt").value = b.dataset.prompt;
    $("prompt").focus();
  };
$("logout").onclick = () =>
  guarded(async () => {
    await request("/logout", {});
    loggedOut();
  });
async function begin() {
  const config = await request("/config");
  $("mode").textContent =
    config.mode === "scripted-test" ? "Deterministic test mode" : "";
  $("login").hidden = true;
  $("app").hidden = false;
  await list();
  if (location.hash) await select(location.hash.slice(1));
  else home();
  const pending = JSON.parse(localStorage.getItem("taskdesk:create") || "null");
  if (pending) {
    $("prompt").value = pending.text;
    notice(
      "An earlier submission needs reconciliation. Start task will retry its original IDs.",
    );
  }
  clearInterval(refreshTimer);
  refreshTimer = setInterval(
    () =>
      list().catch(() => {
        $("connection").textContent = "Reconnecting";
      }),
    15000,
  );
}
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await request("/login", { key: $("key").value });
    $("key").value = "";
    $("login-error").textContent = "";
    await begin();
  } catch (e) {
    $("login-error").textContent = e.message;
  }
};
document.addEventListener("keydown", (e) => {
  if (
    e.key === "n" &&
    !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) &&
    !$("app").hidden
  )
    home();
});
begin().catch(() => loggedOut());
