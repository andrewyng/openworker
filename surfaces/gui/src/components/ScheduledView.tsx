import { useEffect, useState } from "react";
import {
  createAutomation,
  clearAutomationRuns,
  deleteAutomation,
  getConnectors,
  getPersonas,
  getRecentChannels,
  getAutomation,
  getAutomations,
  markAutomationSeen,
  announceAutomationsChanged,
  updateAutomation,
  type Automation,
  type AutomationDelivery,
  type AutomationRun,
  type Connector,
  type Persona,
  type RecentChannel,
} from "../api";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { AutomationQuickstart } from "./AutomationQuickstart";
import { ChannelPicker } from "./SubscriptionsChip";

// Shared utility strings (the §28 page shell — mirrors IntegrationsView's constants).
const CARD = "rounded-xl2 border border-line bg-panel";
const RUN_PAGE_SIZE = 20;

// Parse a simple "min hour * * dow" cron back into the time + frequency the editor uses.
// Falls back to 09:00 / daily for anything it doesn't recognize (e.g. agent-written crons).
function fromCron(cron?: string | null): { time: string; freq: string } {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return { time: "09:00", freq: "daily" };
  const [m, h, , , dow] = parts;
  const hh = String(Math.min(23, Math.max(0, parseInt(h, 10) || 9))).padStart(2, "0");
  const mm = String(Math.min(59, Math.max(0, parseInt(m, 10) || 0))).padStart(2, "0");
  const freq = dow === "1-5" ? "weekdays" : dow === "0,6" || dow === "6,0" ? "weekends" : "daily";
  return { time: `${hh}:${mm}`, freq };
}

const fmt = (t: number | null) =>
  t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

// Map a simple time-of-day + frequency selection to a 5-field cron string.
function toCron(time: string, freq: string): string {
  const [h, m] = (time || "09:00").split(":").map((x) => parseInt(x, 10) || 0);
  const dow = freq === "weekdays" ? "1-5" : freq === "weekends" ? "0,6" : "*";
  return `${m} ${h} * * ${dow}`;
}

// The §28 page shell: full-bleed main, centered ≤4xl column — same as Connectors/Activity/Inbox.
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-4xl mx-auto px-7 py-6">{children}</div>
      </div>
    </main>
  );
}

interface Props {
  // `task` gives the opened run session its context (banner + "Back to runs"; owner ask 2026-07-04).
  onOpenRun: (
    sessionId: string,
    workspace: string,
    agent: string,
    task?: { id: string; title: string },
  ) => void;
  onRunNow: (taskId: string, title?: string) => void;
  // Open directly on a task's detail (set by the run banner's "Back to runs").
  initialOpenId?: string | null;
}

export function ScheduledView({ onOpenRun, onRunNow, initialOpenId }: Props) {
  const [tasks, setTasks] = useState<Automation[]>([]);
  const [openId, setOpenId] = useState<string | null>(initialOpenId ?? null);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // The sidebar's Scheduled band can retarget an ALREADY-open Automations surface —
  // initial state alone would ignore the change (UX-023).
  useEffect(() => {
    if (initialOpenId) setOpenId(initialOpenId);
  }, [initialOpenId]);

  const refresh = () => getAutomations().then(setTasks).catch(() => setTasks([]));
  useEffect(() => {
    refresh();
    const h = setInterval(refresh, 5000);
    return () => clearInterval(h);
  }, []);

  // Create from a payload, refresh the list, and open the new task's detail. `permissions`
  // rides through for quickstart recipes (§25 write grants).
  const create = async (payload: {
    title: string;
    instructions: string;
    agent?: string;
    cron?: string;
    sources: string[];
    delivery: AutomationDelivery;
  }) => {
    setBusy(payload.title);
    try {
      const res = await createAutomation(payload);
      announceAutomationsChanged(); // new entry shows in the sidebar band right away
      await refresh();
      if (res.ok && res.task) {
        setShowForm(false);
        setOpenId(res.task.id);
      } else if (res.error) {
        alert(res.error);
      }
    } finally {
      setBusy(null);
    }
  };

  if (openId) {
    return (
      <TaskDetail
        id={openId}
        onBack={() => { setOpenId(null); refresh(); }}
        onOpenRun={onOpenRun}
        onRunNow={onRunNow}
      />
    );
  }

  const empty = tasks.length === 0;

  return (
    <Shell>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <PanelHead title="Automations" sub="Recurring tasks OpenWorker runs on a schedule." />
        </div>
        <button
          className="text-[12.5px] px-3 py-1.5 rounded-lg border border-lineStrong bg-panel hover:border-accent hover:text-accent shrink-0"
          onClick={() => setShowForm((v) => !v)}
        >
          + New automation
        </button>
      </div>

      <div className="text-[12px] text-faint flex gap-1.5 mb-4">
        <span aria-hidden>ⓘ</span>
        <span>
          Runs only while openworker-server is up — a missed schedule catches up once when it next
          starts.
        </span>
      </div>

      {showForm && (
        <NewAutomationForm
          busy={busy !== null}
          onCancel={() => setShowForm(false)}
          onCreate={create}
        />
      )}

      {/* The quickstart (§29): ONE template system — role recipes + generic templates, each
          card with §27 connector dots; picking one expands the configure card. */}
      {(empty || showForm) && <AutomationQuickstart busy={busy !== null} onCreate={create} />}

      {empty ? (
        !showForm && (
          <div className={CARD + " p-4 text-[12.5px] text-muted"}>
            No scheduled tasks yet — use a template above, click <strong>+ New automation</strong>,
            or just ask OpenWorker in a session.
          </div>
        )
      ) : (
        <div className="flex flex-col gap-2.5">
          {tasks.map((t) => (
            <div
              className={CARD + " sched-card px-4 py-3 cursor-pointer hover:border-lineStrong transition-colors"}
              key={t.id}
              onClick={() => setOpenId(t.id)}
            >
              <div className="flex items-center justify-between gap-2.5 mb-1">
                <span className="text-[13.5px] font-semibold truncate">{t.title}</span>
                <button
                  className="sched-card-del"
                  title="Delete automation"
                  aria-label={`Delete ${t.title}`}
                  onClick={async (e) => {
                    e.stopPropagation();
                    await deleteAutomation(t.id);
                    refresh();
                  }}
                >
                  <Icon name="trash" size={14} />
                </button>
              </div>
              <div className="flex items-center gap-1.5 text-[12px] text-muted">
                <Icon name="clock" size={13} className="text-faint shrink-0" />
                {t.enabled ? t.schedule : "Paused"} · next {fmt(t.next_run)} · {t.run_count} run{t.run_count === 1 ? "" : "s"}
                {t.last_status ? ` · last ${t.last_status}` : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </Shell>
  );
}

function NewAutomationForm({
  busy,
  onCancel,
  onCreate,
}: {
  busy: boolean;
  onCancel: () => void;
  onCreate: (p: {
    title: string;
    instructions: string;
    agent: string;
    cron?: string;
    sources: string[];
    delivery: AutomationDelivery;
  }) => void;
}) {
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [time, setTime] = useState("09:00");
  const [freq, setFreq] = useState("daily");
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [agent, setAgent] = useState("cowork");
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [deliveryConnector, setDeliveryConnector] = useState("app");
  const [channel, setChannel] = useState("");

  useEffect(() => {
    getConnectors().then(setConnectors).catch(() => setConnectors([]));
    getRecentChannels().then(setRecent).catch(() => setRecent([]));
    getPersonas()
      .then((loaded) => {
        const enabled = loaded.filter((persona) => persona.enabled);
        setPersonas(loaded);
        setAgent((current) =>
          enabled.some((persona) => persona.id === current)
            ? current
            : enabled.find((persona) => persona.default)?.id || enabled[0]?.id || current,
        );
      })
      .catch(() => setPersonas([]));
  }, []);

  const enabledPersonas = personas.filter((persona) => persona.enabled);
  const sourceCandidates = connectors.filter((c) => c.source_capable);
  const deliveryCandidates = connectors.filter(
    (c) => c.connected && c.delivery_capable,
  );
  const toggleSource = (name: string) =>
    setSources((current) =>
      current.includes(name) ? current.filter((source) => source !== name) : [...current, name],
    );

  const target = channel.trim().includes(":")
    ? channel.trim()
    : `${deliveryConnector}:${channel.trim()}`;
  const valid = title.trim()
    && instructions.trim()
    && enabledPersonas.some((persona) => persona.id === agent)
    && (deliveryConnector === "app" || channel.trim());

  return (
    <div className={CARD + " tmpl-form p-4 mb-4"}>
      <div className="text-[11px] uppercase tracking-[0.05em] text-faint mb-2.5">
        New automation
      </div>
      <input
        className="tmpl-input"
        placeholder="Title (e.g. Daily standup notes)"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        className="tmpl-input tmpl-textarea"
        placeholder="What should it do each run? (e.g. Summarize today's calendar and open tasks.)"
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
      />
      <label className="tmpl-field mt-3">
        <span>Run as</span>
        <select
          aria-label="Run as"
          className="tmpl-input tmpl-select"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        >
          {enabledPersonas.map((persona) => (
            <option key={persona.id} value={persona.id}>
              {persona.name}
            </option>
          ))}
        </select>
      </label>
      <label className="tmpl-field mt-3">
        <span>Sources</span>
        <span className="text-[11px] text-faint">
          The agent can query only the selected integrations; built-in tools and web search stay available.
        </span>
      </label>
      <div className="flex flex-wrap gap-x-4 gap-y-2 mt-1">
        {sourceCandidates.length ? sourceCandidates.map((connector) => (
          <label
            key={connector.name}
            className={"inline-flex items-center gap-1.5 text-[12.5px] " + (connector.connected ? "text-muted" : "text-faint")}
            title={connector.connected ? undefined : "Connect this source in Integrations first"}
          >
            <input
              type="checkbox"
              checked={sources.includes(connector.name)}
              disabled={!connector.connected}
              onChange={() => toggleSource(connector.name)}
            />
            {connector.title}
            {!connector.connected && <span className="text-[11px]">Connect first</span>}
          </label>
        )) : <span className="text-[12px] text-faint">No connected data integrations are available.</span>}
      </div>
      <label className="tmpl-field mt-3">
        <span>Deliver to</span>
        <select
          className="tmpl-input tmpl-select"
          value={deliveryConnector}
          onChange={(e) => {
            setDeliveryConnector(e.target.value);
            setChannel("");
          }}
        >
          <option value="app">This run in OpenWorker</option>
          {deliveryCandidates.map((connector) => (
            <option key={connector.name} value={connector.name}>
              {connector.title}
            </option>
          ))}
        </select>
      </label>
      {deliveryConnector === "slack" && (
        <div className="mt-1">
          <ChannelPicker value={channel} onChange={setChannel} recent={recent} />
        </div>
      )}
      {deliveryConnector !== "app" && deliveryConnector !== "slack" && (
        <input
          className="tmpl-input mt-1"
          placeholder={
            deliveryConnector === "feishu"
              ? "feishu:<open_id (ou_...) or chat_id (oc_...)>"
              : `${deliveryConnector}:<chat_id>`
          }
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
        />
      )}
      <div className="tmpl-sched">
        <label className="tmpl-field">
          <span>At</span>
          <input
            type="time"
            className="tmpl-input tmpl-time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
        </label>
        <label className="tmpl-field">
          <span>Repeat</span>
          <select
            className="tmpl-input tmpl-select"
            value={freq}
            onChange={(e) => setFreq(e.target.value)}
          >
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekends">Weekends</option>
          </select>
        </label>
      </div>
      <div className="tmpl-form-actions">
        <button
          className="btn-primary sm"
          disabled={!valid || busy}
          onClick={() =>
            onCreate({
              title: title.trim(),
              instructions: instructions.trim(),
              agent,
              cron: toCron(time, freq),
              sources,
              delivery:
                deliveryConnector !== "app"
                  ? { kind: "channel", connector: deliveryConnector, target }
                  : { kind: "app" },
            })
          }
        >
          {busy ? "Creating…" : "Create automation"}
        </button>
        <button className="link" onClick={onCancel}>cancel</button>
      </div>
    </div>
  );
}

function TaskDetail({
  id,
  onBack,
  onOpenRun,
  onRunNow,
}: {
  id: string;
  onBack: () => void;
  onOpenRun: (
    sessionId: string,
    workspace: string,
    agent: string,
    task?: { id: string; title: string },
  ) => void;
  onRunNow: (taskId: string, title?: string) => void;
}) {
  const [task, setTask] = useState<Automation | null>(null);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [totalRuns, setTotalRuns] = useState(0);
  const [nextRunsOffset, setNextRunsOffset] = useState<number | null>(null);
  const [loadingMoreRuns, setLoadingMoreRuns] = useState(false);
  const [managingRuns, setManagingRuns] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [time, setTime] = useState("09:00");
  const [freq, setFreq] = useState("daily");
  const [saving, setSaving] = useState(false);
  const [runRetentionDays, setRunRetentionDays] = useState<number | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [agent, setAgent] = useState("");
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [deliveryConnector, setDeliveryConnector] = useState("app");
  const [channel, setChannel] = useState("");

  // The seen mark AS OF opening — the "new" pills compare against this frozen value
  // while mark-seen advances the stored one (badge clears; highlights survive).
  const [seenMark, setSeenMark] = useState<number | null>(null);

  const refresh = () =>
    getAutomation(id, { limit: RUN_PAGE_SIZE })
      .then((d) => {
        if (!d.task) {
          // Deleted (or a stale reopen target): "Loading…" forever is a trap —
          // fall back to the overview (owner-hit 2026-07-20).
          onBack();
          return;
        }
        setTask(d.task);
        setRuns(d.runs || []);
        setTotalRuns(d.total_runs ?? (d.runs || []).length);
        setNextRunsOffset(d.has_more ? d.next_offset ?? null : null);
        setSeenMark((cur) => (cur === null ? d.task?.seen_runs_at ?? 0 : cur));
      })
      .catch(() => {});
  useEffect(() => {
    setSeenMark(null);
    refresh();
    // Opening the detail IS reading it: advance the seen mark and nudge the
    // sidebar so the badge clears immediately (UX-023).
    markAutomationSeen(id)
      .then(() => announceAutomationsChanged())
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    getConnectors().then(setConnectors).catch(() => setConnectors([]));
    getRecentChannels().then(setRecent).catch(() => setRecent([]));
    getPersonas().then(setPersonas).catch(() => setPersonas([]));
  }, []);

  if (!task)
    return (
      <Shell>
        <div className="text-[13px] text-muted">Loading…</div>
      </Shell>
    );

  const startEdit = () => {
    setTitle(task.title);
    setInstructions(task.instructions);
    const { time: t, freq: f } = fromCron(task.schedule_raw?.cron);
    setTime(t);
    setFreq(f);
    setAgent(task.agent);
    setRunRetentionDays(task.run_retention_days ?? null);
    setSources(task.sources || []);
    const delivery = task.delivery;
    const connector = delivery?.kind === "channel" ? delivery.connector || "app" : "app";
    const target = delivery?.kind === "channel" ? delivery.target || "" : "";
    setDeliveryConnector(connector);
    setChannel(target.startsWith(`${connector}:`) ? target.slice(connector.length + 1) : target);
    setEditing(true);
  };
  const sourceCandidates = connectors.filter((c) => c.source_capable);
  const enabledPersonas = personas.filter((persona) => persona.enabled);
  const selectedPersona = personas.find((persona) => persona.id === agent);
  const taskPersona = personas.find((persona) => persona.id === task.agent);
  const deliveryCandidates = connectors.filter(
    (c) => c.connected && c.delivery_capable,
  );
  const toggleSource = (name: string) =>
    setSources((current) =>
      current.includes(name) ? current.filter((source) => source !== name) : [...current, name],
    );
  const deliveryTarget = channel.trim().includes(":")
    ? channel.trim()
    : `${deliveryConnector}:${channel.trim()}`;
  const saveEdit = async () => {
    setSaving(true);
    try {
      const res = await updateAutomation(id, {
        title: title.trim(),
        instructions: instructions.trim(),
        agent,
        run_retention_days: runRetentionDays,
        cron: toCron(time, freq),
        sources,
        delivery:
          deliveryConnector === "app"
            ? { kind: "app" }
            : { kind: "channel", connector: deliveryConnector, target: deliveryTarget },
      });
      if (!res.ok) {
        alert(res.error || "Could not update automation");
        return;
      }
      await refresh();
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };
  const toggle = async () => {
    await updateAutomation(id, { enabled: !task.enabled });
    refresh();
  };
  const remove = async () => {
    await deleteAutomation(id);
    announceAutomationsChanged(); // the sidebar band must not wait out its poll
    onBack();
  };
  const loadMoreRuns = async () => {
    if (nextRunsOffset === null) return;
    setLoadingMoreRuns(true);
    try {
      const page = await getAutomation(id, {
        limit: RUN_PAGE_SIZE,
        offset: nextRunsOffset,
      });
      setRuns((current) => [...current, ...(page.runs || [])]);
      setTotalRuns(page.total_runs ?? totalRuns);
      setNextRunsOffset(page.has_more ? page.next_offset ?? null : null);
    } finally {
      setLoadingMoreRuns(false);
    }
  };
  const completedRuns = runs.filter((run) => run.finished_at !== null);
  const allShownSelected = completedRuns.length > 0
    && completedRuns.every((run) => selectedRunIds.has(run.run_id));
  const toggleRunSelection = (runId: string) =>
    setSelectedRunIds((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  const toggleAllShownRuns = () =>
    setSelectedRunIds((current) => {
      const next = new Set(current);
      if (allShownSelected) completedRuns.forEach((run) => next.delete(run.run_id));
      else completedRuns.forEach((run) => next.add(run.run_id));
      return next;
    });
  const clearRuns = async (runIds?: string[]) => {
    const selected = runIds !== undefined;
    const prompt = selected
      ? `Clear ${runIds.length} selected completed run${runIds.length === 1 ? "" : "s"}?`
      : "Clear all completed run history? Running tasks will be kept.";
    if (!window.confirm(prompt)) return;
    const result = runIds === undefined
      ? await clearAutomationRuns(id)
      : await clearAutomationRuns(id, runIds);
    if (!result.ok) {
      alert(result.error || "Could not clear run history");
      return;
    }
    setSelectedRunIds(new Set());
    setManagingRuns(false);
    await refresh();
  };

  return (
    <Shell>
      <button className="text-[13px] text-muted hover:text-ink mb-3" onClick={onBack}>
        ← Automations
      </button>
      <div className="sched-detail">
        <div className="sched-detail-head">
          {editing ? (
            <input
              className="tmpl-input sched-edit-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Title"
            />
          ) : (
            <h2 className="text-[18px] font-semibold tracking-tight">{task.title}</h2>
          )}
          <div className="sched-actions">
            {editing ? (
              <>
                <button
                  className="btn-primary sm"
                  disabled={
                    saving
                    || !title.trim()
                    || !instructions.trim()
                    || !enabledPersonas.some((persona) => persona.id === agent)
                    || (deliveryConnector !== "app" && !channel.trim())
                  }
                  onClick={saveEdit}
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button className="link" onClick={() => setEditing(false)}>cancel</button>
              </>
            ) : (
              <>
                <button className="btn-primary sm" onClick={() => onRunNow(id, task.title)}>
                  ▶ Run now
                </button>
                <button className="btn sm" onClick={startEdit}>Edit</button>
                <button className="btn sm danger-btn" onClick={remove}>
                  <Icon name="trash" size={14} /> Delete
                </button>
              </>
            )}
          </div>
        </div>

        {editing ? (
          <div className="tmpl-sched sched-edit-sched">
            <label className="tmpl-field">
              <span>At</span>
              <input type="time" className="tmpl-input tmpl-time" value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
            <label className="tmpl-field">
              <span>Repeat</span>
              <select className="tmpl-input tmpl-select" value={freq} onChange={(e) => setFreq(e.target.value)}>
                <option value="daily">Every day</option>
                <option value="weekdays">Weekdays</option>
                <option value="weekends">Weekends</option>
              </select>
            </label>
          </div>
        ) : (
          <div className="conn-meta">
            <label className="switch">
              <input type="checkbox" checked={task.enabled} onChange={toggle} />
              <span className="slider" />
            </label>{" "}
            {task.enabled ? `Active · next ${fmt(task.next_run)}` : "Paused"} · {task.schedule}
          </div>
        )}

        <div className="sa-sub">Instructions</div>
        {editing ? (
          <textarea
            className="tmpl-input tmpl-textarea sched-edit-instr"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        ) : (
          <div className="sched-instructions">{task.instructions}</div>
        )}

        <div className="sa-sub">Agent</div>
        {editing ? (
          <select
            aria-label="Run as"
            className="tmpl-input tmpl-select"
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
          >
            {selectedPersona && !selectedPersona.enabled && (
              <option value={selectedPersona.id} disabled>
                {selectedPersona.name} (disabled)
              </option>
            )}
            {enabledPersonas.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.name}
              </option>
            ))}
          </select>
        ) : (
          <div className="dim" style={{ marginBottom: 8, fontSize: 12.5 }}>
            {taskPersona?.name || task.agent}
          </div>
        )}

        <div className="sa-sub">Sources</div>
        {editing ? (
          <>
            <div className="dim" style={{ marginBottom: 6, fontSize: 12.5 }}>
              The agent can query only selected integrations; built-in tools and web search stay available.
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-2" style={{ marginBottom: 8 }}>
              {sourceCandidates.length ? sourceCandidates.map((connector) => (
                <label
                  key={connector.name}
                  className={"inline-flex items-center gap-1.5 text-[12.5px] " + (connector.connected ? "text-muted" : "text-faint")}
                  title={connector.connected ? undefined : "Connect this source in Integrations first"}
                >
                  <input
                    type="checkbox"
                    checked={sources.includes(connector.name)}
                    disabled={!connector.connected}
                    onChange={() => toggleSource(connector.name)}
                  />
                  {connector.title}
                  {!connector.connected && <span className="text-[11px]">Connect first</span>}
                </label>
              )) : <span className="text-[12px] text-faint">No connected data integrations are available.</span>}
            </div>
          </>
        ) : (
          <div className="dim" style={{ marginBottom: 8, fontSize: 12.5 }}>
            {task.sources === null
              ? "Legacy task: uses the persona's effective connected integrations."
              : task.sources.length
                ? task.sources.join(", ")
                : "No integration sources selected. The agent can still use built-in tools and web search."}
          </div>
        )}

        <div className="sa-sub">Delivery</div>
        {editing ? (
          <div style={{ marginBottom: 8 }}>
            <select
              aria-label="Deliver to"
              className="tmpl-input tmpl-select"
              value={deliveryConnector}
              onChange={(e) => {
                setDeliveryConnector(e.target.value);
                setChannel("");
              }}
            >
              <option value="app">This run in OpenWorker</option>
              {deliveryCandidates.map((connector) => (
                <option key={connector.name} value={connector.name}>
                  {connector.title}
                </option>
              ))}
            </select>
            {deliveryConnector === "slack" && (
              <div className="mt-1">
                <ChannelPicker value={channel} onChange={setChannel} recent={recent} />
              </div>
            )}
            {deliveryConnector !== "app" && deliveryConnector !== "slack" && (
              <input
                className="tmpl-input mt-1"
                placeholder={
                  deliveryConnector === "feishu"
                    ? "feishu:<open_id (ou_...) or chat_id (oc_...)>"
                    : `${deliveryConnector}:<chat_id>`
                }
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
              />
            )}
          </div>
        ) : (
          <div className="dim" style={{ marginBottom: 8, fontSize: 12.5 }}>
            {task.delivery?.kind === "channel"
              ? `${task.delivery.connector} → ${task.delivery.target}`
              : "This run in OpenWorker"}
          </div>
        )}

        <div className="sa-sub">Run history</div>
        {editing ? (
          <label className="tmpl-field" style={{ marginBottom: 8 }}>
            <span>Automatically clear completed runs</span>
            <select
              aria-label="Run history retention"
              className="tmpl-input tmpl-select"
              value={runRetentionDays ?? ""}
              onChange={(e) => setRunRetentionDays(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">Never</option>
              <option value="7">After 7 days</option>
              <option value="30">After 30 days</option>
              <option value="90">After 90 days</option>
              <option value="365">After 1 year</option>
            </select>
          </label>
        ) : (
          <div className="dim" style={{ marginBottom: 8, fontSize: 12.5 }}>
            {task.run_retention_days
              ? `Completed runs are cleared after ${task.run_retention_days} days.`
              : "Completed runs are kept until you clear them."}
          </div>
        )}

        {(task.always_allowed || []).length > 0 && (
          <>
            <div className="sa-sub">Allowed without asking</div>
            <div className="dim" style={{ marginBottom: 8, fontSize: 12.5 }}>
              Standing approvals this automation may use — everything else still asks first.
            </div>
            <div className="sched-grants" data-testid="task-grants">
              {(task.always_allowed || []).map((rule) => (
                <div className="sched-grant" key={rule.entry}>
                  <span className="sched-grant-rule">
                    <code>{rule.tool}</code>
                    {rule.target && <span className="sched-grant-target"> → {rule.target}</span>}
                  </span>
                  <button
                    className="link"
                    title="This automation will ask for approval again"
                    onClick={async () => {
                      await updateAutomation(id, { revoke: rule.entry });
                      refresh();
                    }}
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          </>
        )}

        <div className="flex items-center justify-between gap-3">
          <div className="sa-sub mb-0">Runs{totalRuns ? ` (${totalRuns})` : ""}</div>
          {runs.length > 0 && (
            <div className="flex flex-wrap justify-end gap-x-3 gap-y-1">
              {managingRuns ? (
                <>
                  <button className="link" onClick={toggleAllShownRuns} disabled={completedRuns.length === 0}>
                    {allShownSelected ? "Deselect shown" : "Select all shown"}
                  </button>
                  <button
                    className="link text-danger"
                    disabled={selectedRunIds.size === 0}
                    onClick={() => clearRuns([...selectedRunIds])}
                  >
                    Clear selected ({selectedRunIds.size})
                  </button>
                  <button className="link text-danger" onClick={() => clearRuns()}>
                    Clear all
                  </button>
                  <button
                    className="link"
                    onClick={() => {
                      setSelectedRunIds(new Set());
                      setManagingRuns(false);
                    }}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  className="link text-danger"
                  onClick={() => {
                    setSelectedRunIds(new Set());
                    setManagingRuns(true);
                  }}
                >
                  Manage history
                </button>
              )}
            </div>
          )}
        </div>
        <div className="dim" style={{ marginBottom: 8, fontSize: 12.5 }}>
          Each run is a live conversation — open one to see what the agent did and ask a follow-up.
        </div>
        {runs.length === 0 && <div className="dim">No runs yet.</div>}
        {runs.map((r) => (
          <div
            className="sched-run open"
            key={r.run_id}
            onClick={() =>
              r.session_id &&
              onOpenRun(r.session_id, task.workspace, task.agent, {
                id: task.id,
                title: task.title,
              })
            }
            title="Open this run's conversation"
          >
            <div className="sched-run-row">
              {managingRuns && (
                <input
                  type="checkbox"
                  aria-label={`Select run ${r.run_id}`}
                  checked={selectedRunIds.has(r.run_id)}
                  disabled={r.finished_at === null}
                  title={r.finished_at === null ? "Running tasks cannot be cleared" : undefined}
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => toggleRunSelection(r.run_id)}
                />
              )}
              <span>
                {seenMark !== null && r.started_at > seenMark && (
                  <span className="run-new-pill" data-testid="run-new">new</span>
                )}
                {fmt(r.started_at)} · <span className={"run-" + r.status}>{r.status}</span> · {r.trigger}
                {r.artifacts.length > 0 && <span className="dim"> · {r.artifacts.length} file(s)</span>}
                {r.delivery_status === "sent" && <span className="dim"> · delivered</span>}
                {r.delivery_status === "failed" && <span className="mcp-error"> · delivery failed</span>}
              </span>
              <span className="sched-run-go" aria-hidden>
                Open ›
              </span>
            </div>
            {r.result_text && <div className="sched-run-peek">{r.result_text}</div>}
            {r.error && <div className="mcp-error">{r.error}</div>}
            {r.delivery_status === "failed" && r.delivery_error && (
              <div className="mcp-error">Delivery error: {r.delivery_error}</div>
            )}
          </div>
        ))}
        {nextRunsOffset !== null && (
          <button className="btn sm mt-2" disabled={loadingMoreRuns} onClick={loadMoreRuns}>
            {loadingMoreRuns ? "Loading…" : `Load ${Math.min(RUN_PAGE_SIZE, totalRuns - runs.length)} more`}
          </button>
        )}
      </div>
    </Shell>
  );
}
