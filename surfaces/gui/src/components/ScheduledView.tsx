import { useEffect, useState } from "react";
import {
  createAutomation,
  deleteAutomation,
  getAutomation,
  getAutomationSuggestions,
  getAutomations,
  markAutomationSeen,
  announceAutomationsChanged,
  updateAutomation,
  type Automation,
  type AutomationRun,
  type AutomationSuggestion,
} from "../api";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { AutomationQuickstart } from "./AutomationQuickstart";

// Shared utility strings (the §28 page shell — mirrors IntegrationsView's constants).
const CARD = "rounded-xl2 border border-line bg-panel";

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

// Which rung of the ladder a cron sits on. Mirrors cadence_of() in
// coworker/automation/suggestions.py — the page groups by the same vocabulary the suggestion
// engine reasons in, so "you have no weekly review" lines up with a visibly empty group.
function cadenceOf(cron?: string | null): string {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return "other";
  const [, , dom, month, dow] = parts;
  if (dom === "*" && dow === "*") return "daily";
  if (dom === "*" && dow !== "*") return "weekly";
  if (dom !== "*" && month === "*") return "monthly";
  if (dom !== "*" && month.includes(",")) return "quarterly";
  if (dom !== "*" && /^\d+$/.test(month)) return "yearly";
  return "other";
}

const CADENCE_ORDER = ["daily", "weekly", "monthly", "quarterly", "yearly", "other"] as const;
const CADENCE_LABEL: Record<string, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  yearly: "Yearly",
  other: "Custom schedule",
  paused: "Paused",
};

// "in 4h" / "in 3d" — a tile has room for when it next fires, not a full timestamp.
function untilLabel(t: number | null): string {
  if (!t) return "—";
  const secs = t - Date.now() / 1000;
  if (secs < 0) return "due";
  if (secs < 3600) return `in ${Math.max(1, Math.round(secs / 60))}m`;
  if (secs < 86400) return `in ${Math.round(secs / 3600)}h`;
  return `in ${Math.round(secs / 86400)}d`;
}

// The clock time a cron fires at. The group header states the cadence, so repeating
// "Every day at" on every tile would only cost width.
function timeLabel(cron?: string | null): string {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return "";
  const h = parseInt(parts[1], 10);
  const m = parseInt(parts[0], 10);
  if (Number.isNaN(h) || Number.isNaN(m)) return "";
  const ampm = h < 12 ? "AM" : "PM";
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${ampm}`;
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
  // Suggestions are derived server-side from this machine's own activity; templates are the
  // same for everyone. Both live on the page, but only the suggestions are shown by default —
  // a generic template grid under a full schedule is noise.
  const [suggestions, setSuggestions] = useState<AutomationSuggestion[]>([]);
  const [showTemplates, setShowTemplates] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

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

  // Suggestions shell out to git behind a server-side cache, so they are fetched once per
  // visit and after each mutation — never on the 5s task poll.
  const refreshSuggestions = () =>
    getAutomationSuggestions().then(setSuggestions).catch(() => setSuggestions([]));
  useEffect(() => {
    refreshSuggestions();
  }, []);

  // Create from a payload, refresh the list, and open the new task's detail. `permissions`
  // rides through for quickstart recipes (§25 write grants).
  const create = async (payload: {
    title: string;
    instructions: string;
    cron?: string;
    permissions?: { tool: string; target: string; access: "read" | "write" }[];
  }) => {
    setBusy(payload.title);
    try {
      const res = await createAutomation(payload);
      announceAutomationsChanged(); // the sidebar count updates right away
      await refresh();
      void refreshSuggestions(); // an accepted suggestion must stop being suggested
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
  // Grouped by cadence, paused last — a disabled automation is not part of any rhythm, and
  // mixing it into Daily makes the schedule read as busier than it is.
  const groups: [string, Automation[]][] = [];
  for (const cadence of [...CADENCE_ORDER, "paused"]) {
    const list = tasks.filter((t) =>
      cadence === "paused" ? !t.enabled : t.enabled && cadenceOf(t.schedule_raw?.cron) === cadence,
    );
    if (list.length) groups.push([cadence, list]);
  }

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

      {/* Suggestions first, templates behind a disclosure: a suggestion states the evidence
          for itself ("19 commits to workstation-stack, nothing watches it"), a template is the
          same card for everybody. Under a full schedule the generic grid is noise. */}
      <SuggestionShelf
        suggestions={suggestions.filter((x) => !dismissed.has(x.key))}
        busy={busy}
        onDismiss={(key) => setDismissed((d) => new Set(d).add(key))}
        onAdd={(x) =>
          create({ title: x.title, instructions: x.instructions, cron: x.cron })
        }
        onBrowseTemplates={() => setShowTemplates((v) => !v)}
        templatesOpen={showTemplates}
      />

      {(empty || showForm || showTemplates) && (
        <AutomationQuickstart busy={busy !== null} onCreate={create} />
      )}

      {empty ? (
        !showForm && (
          <div className={CARD + " p-4 text-[12.5px] text-muted"}>
            No scheduled tasks yet — take a suggestion above, click{" "}
            <strong>+ New automation</strong>, or just ask OpenWorker in a session.
          </div>
        )
      ) : (
        <div className="flex flex-col gap-6" data-testid="automation-groups">
          {groups.map(([cadence, list]) => (
            <section key={cadence}>
              <div className="flex items-baseline gap-2 mb-2">
                <h2 className="text-[11px] uppercase tracking-[0.07em] text-faint font-semibold">
                  {CADENCE_LABEL[cadence] || cadence}
                </h2>
                <span className="text-[11px] text-faint tabular-nums">{list.length}</span>
              </div>
              {/* auto-rows-fr keeps a row's tiles equal height however long a title wraps. */}
              <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3 auto-rows-fr">
                {list.map((t) => (
                  <TaskTile
                    key={t.id}
                    task={t}
                    onOpen={() => setOpenId(t.id)}
                    onDelete={async () => {
                      await deleteAutomation(t.id);
                      announceAutomationsChanged();
                      refresh();
                      void refreshSuggestions();
                    }}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Shell>
  );
}

// One automation as a tile. The old page listed these full-width, one per row, which at
// fifteen automations was a column of near-identical bars you had to read linearly. A tile
// states the four things worth glancing at — name, when it fires, how it last went, whether
// there is anything unread — and the grid lets you compare them at once.
function TaskTile({
  task,
  onOpen,
  onDelete,
}: {
  task: Automation;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const unseen = task.unseen_runs || 0;
  const bad = task.last_status === "error" || task.last_status === "incomplete";
  return (
    <div
      className={
        CARD +
        " sched-tile relative h-full flex flex-col gap-2 px-3.5 py-3 cursor-pointer" +
        " hover:border-lineStrong transition-colors"
      }
      data-testid={`scheduled-${task.id}`}
      onClick={onOpen}
    >
      <div className="flex items-start gap-2">
        <span className="flex-1 min-w-0 text-[13.5px] font-semibold leading-snug line-clamp-2">
          {task.title}
        </span>
        {unseen > 0 && (
          <span
            className="text-[10px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
            title={
              task.unseen_failed
                ? `${unseen} new run${unseen > 1 ? "s" : ""} — the latest failed`
                : `${unseen} new run${unseen > 1 ? "s" : ""}`
            }
          >
            {unseen}
          </span>
        )}
      </div>

      <div className="mt-auto flex items-center gap-1.5 text-[11.5px] text-faint">
        <Icon name="clock" size={12} className="shrink-0" />
        {task.enabled ? (
          <>
            <span className="tabular-nums">{timeLabel(task.schedule_raw?.cron) || task.schedule}</span>
            <span aria-hidden>·</span>
            <span className="tabular-nums">{untilLabel(task.next_run)}</span>
          </>
        ) : (
          <span>Paused</span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {/* Last outcome as a word, not a colour: the sidebar deliberately has no status
              colour, and a red dot here would be the page's loudest element. */}
          {task.last_status && (
            <span className={bad ? "text-warnInk" : ""}>{task.last_status}</span>
          )}
          <button
            className="sched-card-del"
            title="Delete automation"
            aria-label={`Delete ${task.title}`}
            onClick={(e) => {
              e.stopPropagation();
              void onDelete();
            }}
          >
            <Icon name="trash" size={13} />
          </button>
        </span>
      </div>
    </div>
  );
}

// Suggestions derived from what this machine is actually doing (server-side; see
// coworker/automation/suggestions.py). Each card leads with its EVIDENCE — that line is the
// difference between a marketplace that knows your work and a list of templates.
function SuggestionShelf({
  suggestions,
  busy,
  onAdd,
  onDismiss,
  onBrowseTemplates,
  templatesOpen,
}: {
  suggestions: AutomationSuggestion[];
  busy: string | null;
  onAdd: (s: AutomationSuggestion) => void;
  onDismiss: (key: string) => void;
  onBrowseTemplates: () => void;
  templatesOpen: boolean;
}) {
  return (
    <div className="mb-6" data-testid="suggestion-shelf">
      <div className="flex items-baseline gap-2 mb-2">
        <h2 className="text-[11px] uppercase tracking-[0.07em] text-faint font-semibold">
          Suggested for you
        </h2>
        <button
          className="ml-auto text-[12px] text-muted hover:text-accent"
          data-testid="browse-templates"
          onClick={onBrowseTemplates}
        >
          {templatesOpen ? "Hide templates" : "Browse templates"}
        </button>
      </div>
      {suggestions.length === 0 ? (
        <div className={CARD + " p-3.5 text-[12.5px] text-muted"}>
          Nothing to suggest — every pattern this machine can see is already scheduled. New
          suggestions appear as your work changes.
        </div>
      ) : (
        <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3 auto-rows-fr">
          {suggestions.map((s) => (
            <div
              key={s.key}
              className={CARD + " h-full flex flex-col gap-1.5 px-3.5 py-3"}
              data-testid={`suggestion-${s.key}`}
            >
              <div className="flex items-start gap-2">
                <span className="flex-1 text-[13.5px] font-semibold leading-snug">{s.title}</span>
                <span className="text-[10.5px] uppercase tracking-[0.05em] text-faint shrink-0 mt-0.5">
                  {s.cadence}
                </span>
              </div>
              <span className="text-[12px] text-muted leading-relaxed">{s.blurb}</span>
              <span className="text-[11.5px] text-accent leading-relaxed">{s.reason}</span>
              <div className="mt-auto pt-1.5 flex items-center gap-2">
                <button
                  className="btn-primary sm"
                  disabled={busy !== null}
                  data-testid={`add-${s.key}`}
                  onClick={() => onAdd(s)}
                >
                  {busy === s.title ? "Adding…" : "Add"}
                </button>
                <button className="link text-[12px]" onClick={() => onDismiss(s.key)}>
                  not now
                </button>
                {s.requires.length > 0 && (
                  <span className="ml-auto text-[11px] text-faint">
                    needs {s.requires.join(", ")}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NewAutomationForm({
  busy,
  onCancel,
  onCreate,
}: {
  busy: boolean;
  onCancel: () => void;
  onCreate: (p: { title: string; instructions: string; cron?: string }) => void;
}) {
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [time, setTime] = useState("09:00");
  const [freq, setFreq] = useState("daily");

  const valid = title.trim() && instructions.trim();

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
              cron: toCron(time, freq),
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
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [time, setTime] = useState("09:00");
  const [freq, setFreq] = useState("daily");
  const [saving, setSaving] = useState(false);

  // The seen mark AS OF opening — the "new" pills compare against this frozen value
  // while mark-seen advances the stored one (badge clears; highlights survive).
  const [seenMark, setSeenMark] = useState<number | null>(null);

  const refresh = () =>
    getAutomation(id)
      .then((d) => {
        if (!d.task) {
          // Deleted (or a stale reopen target): "Loading…" forever is a trap —
          // fall back to the overview (owner-hit 2026-07-20).
          onBack();
          return;
        }
        setTask(d.task);
        setRuns(d.runs || []);
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
    setEditing(true);
  };
  const saveEdit = async () => {
    setSaving(true);
    try {
      await updateAutomation(id, {
        title: title.trim(),
        instructions: instructions.trim(),
        cron: toCron(time, freq),
      });
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
                <button className="btn-primary sm" disabled={saving || !title.trim() || !instructions.trim()} onClick={saveEdit}>
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

        <div className="sa-sub">Runs</div>
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
              <span>
                {seenMark !== null && r.started_at > seenMark && (
                  <span className="run-new-pill" data-testid="run-new">new</span>
                )}
                {fmt(r.started_at)} · <span className={"run-" + r.status}>{r.status}</span> · {r.trigger}
                {r.artifacts.length > 0 && <span className="dim"> · {r.artifacts.length} file(s)</span>}
              </span>
              <span className="sched-run-go" aria-hidden>
                Open ›
              </span>
            </div>
            {r.result_text && <div className="sched-run-peek">{r.result_text}</div>}
            {r.error && <div className="mcp-error">{r.error}</div>}
          </div>
        ))}
      </div>
    </Shell>
  );
}
