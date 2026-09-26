import { useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  addConfiguration,
  deleteConfiguration,
  editConfiguration,
  getAllSessions,
  getConfigurations,
  getMachineSettings,
  getPersonasIndex,
  getSettings,
  listSkills,
  removeCloudSubscription,
  type CloudSubscription,
  type Configuration,
  type ConfigurationTarget,
  type Persona,
} from "../../api";
import type { SessionInfo } from "../../types";
type SessionRow = SessionInfo;
import { FOOT, GRP, GRP_H, PILL_ACCENT, PILL_LINE, ROW, XBTN } from "./ui";

// UX-050 (owner, 2026-09-04): the GitHub glance page IS its configurations.
// A row = repositories × event × who may trigger it × where it goes. Add and
// Delete; Edit changes who and the target only (repositories and the event
// are fixed — delete and add). A session's own repo subscription (from its
// Access panel) shows here as "Mention → existing session". The installation
// default (the routing line + People) is the first row; it is what answers a
// plain @mention nothing else covers.

// label / hint are locale keys (hint "" = none).
export const EVENTS: { value: string; label: string; hint: string }[] = [
  { value: "mention", label: "connconfig.event_mention", hint: "connconfig.event_mention_hint" },
  { value: "named_mention", label: "connconfig.event_named_mention", hint: "connconfig.event_named_mention_hint" },
  { value: "pr_open", label: "connconfig.event_pr_open", hint: "connconfig.event_pr_open_hint" },
  { value: "pr_merge", label: "connconfig.event_pr_merge", hint: "" },
  { value: "issue_open", label: "connconfig.event_issue_open", hint: "" },
];

// Spec §11.5: the three approval modes a configuration may pick for the sessions it spawns
// (Custom is not offered). Wire values match the box's Mode enum. label / hint are locale keys.
const APPROVAL_MODES: { value: string; label: string; hint: string }[] = [
  { value: "auto-approve", label: "composer.mode.auto_approve", hint: "connconfig.mode_auto_approve_hint" },
  { value: "interactive", label: "connconfig.mode_manual", hint: "connconfig.mode_manual_hint" },
  { value: "bypass-approvals", label: "composer.mode.auto", hint: "connconfig.mode_bypass_hint" },
];

const eventLabel = (e: string, t: TFunction) => {
  const ev = EVENTS.find((x) => x.value === e);
  return ev ? t(ev.label) : e;
};
const shortModel = (m: string) => (m.includes(":") ? m.split(":").slice(1).join(":") : m);
const INPUT = "text-ui px-2 py-1 rounded-lg border border-line bg-paper text-ink outline-none";
const NAME_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

export interface MachineRef {
  id: string; // broker id ("desktop" | machine id)
  name: string;
  glyph: string;
  guiId: string | null; // the GUI id for proxied reads (null = the local engine)
}

export function ConfigurationsSection({
  holders,
  installationId,
  owner,
  cloudSubs,
  onChanged,
}: {
  holders: MachineRef[];
  installationId: string;
  owner: string; // the installation's account login ("All repositories in <owner>")
  cloudSubs: CloudSubscription[]; // legacy repo subscriptions (github:*)
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Configuration[]>([]);
  const [dialog, setDialog] = useState<{ mode: "add" } | { mode: "edit"; cfg: Configuration } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => {
    getConfigurations("github").then(setRows).catch(() => setRows([]));
  };
  useEffect(load, [installationId]);
  const reload = () => {
    load();
    onChanged();
  };
  const holderName = (id: string) => holders.find((h) => h.id === id);
  // The " · " line is a list of independent segments, each one its own key.
  const targetLine = (tg: ConfigurationTarget) => {
    const m = holderName(tg.machine_id);
    const where = m ? `${m.glyph} ${m.name}` : tg.machine_id;
    if (tg.kind === "existing") return [t("connconfig.existing"), tg.title || tg.session_id, where].join(" · ");
    const modeKey = APPROVAL_MODES.find((x) => x.value === (tg.approval_mode || "auto-approve"))?.label;
    const mode = modeKey ? t(modeKey) : tg.approval_mode;
    return [
      t("connconfig.new_session"),
      tg.persona || t("connconfig.default_coworker_lower"),
      ...(tg.models?.[0] ? [shortModel(tg.models[0])] : []),
      where,
      ...(tg.base_dir ? [t("connconfig.target_session_under", { dir: tg.base_dir })] : []),
      tg.unattended === false ? mode : t("connconfig.target_mode_to_inbox", { mode }),
    ].join(" · ");
  };
  const repoLabel = (r: string) => (r.includes("/") ? r.split("/")[1] : t("connconfig.all_repos"));
  // The installation default: the owner-level Mention row, created at connect.
  const isDefault = (c: Configuration) => c.event === "mention" && c.repos.length === 1 && !c.repos[0].includes("/");
  const subRows = cloudSubs.filter((s) => s.source.startsWith("github:"));

  return (
    <>
      <div className={GRP_H + " flex items-center"}>
        <span>{t("connconfig.heading", { n: rows.length + subRows.length })}</span>
        <button className={PILL_LINE + " ml-auto !py-0.5"} data-testid="cfg-add" onClick={() => setDialog({ mode: "add" })}>
          {t("connconfig.add_configuration_btn")}
        </button>
      </div>
      <div className={GRP} data-testid="glance-configurations">
        {rows.map((c) => (
          <div className={ROW} key={c.config_id} data-testid={`glance-cfg-${c.config_id}`}>
            <div className="min-w-0 flex-1">
              <div className="text-ui text-ink truncate">
                <span className="font-medium">{c.repos.map(repoLabel).join(", ")}</span>
                <span className="text-muted"> · {eventLabel(c.event, t)}</span>
                {c.event === "named_mention" && <code className="ml-1 text-meta bg-paper rounded px-1">{c.name}</code>}
                {isDefault(c) && <span className="text-muted"> · {t("connconfig.default_tag")}</span>}
              </div>
              <div className="text-meta text-muted truncate">
                {c.state === "orphan" ? (
                  <span className="text-warnInk">{t("connconfig.orphan_notice", { name: c.target.title || c.target.session_id })} · </span>
                ) : null}
                {targetLine(c.target)} · {c.who.length ? t("connconfig.who_you_plus", { n: c.who.length }) : t("connconfig.who_you")}
              </div>
            </div>
            <button className={PILL_LINE + " !py-1"} data-testid={`cfg-edit-${c.config_id}`} onClick={() => setDialog({ mode: "edit", cfg: c })}>
              {t("connconfig.edit")}
            </button>
            <button
              className={XBTN}
              title={t("connconfig.delete")}
              data-testid={`cfg-delete-${c.config_id}`}
              onClick={async () => {
                await deleteConfiguration(c.config_id);
                reload();
              }}
            >
              ×
            </button>
          </div>
        ))}
        {subRows.map((s) => {
          const m = holderName(s.machine_id);
          return (
            <div className={ROW} key={s.source} data-testid={`glance-sub-${s.source}`}>
              <div className="min-w-0 flex-1">
                <div className="text-ui text-ink truncate">
                  <span className="font-medium">{s.source.slice("github:".length).includes("/") ? s.source.slice("github:".length).split("/")[1] : t("connconfig.all_repos")}</span>
                  <span className="text-muted"> · {t("connconfig.event_mention")}</span>
                </div>
                <div className="text-meta text-muted truncate">
                  {t("connconfig.existing")} · {s.state === "orphan" ? <span className="text-warnInk">{t("connconfig.session_gone")}</span> : s.title || s.session_id} · {m ? `${m.glyph} ${m.name}` : s.machine_id} · {t("connconfig.subscribed_from_session")}
                </div>
              </div>
              <button
                className={PILL_LINE + " !py-1"}
                data-testid={`glance-unsub-${s.source}`}
                onClick={async () => {
                  await removeCloudSubscription(s.source);
                  reload();
                }}
              >
                {s.state === "orphan" ? t("common.remove") : t("connconfig.unsubscribe")}
              </button>
            </div>
          );
        })}
        {rows.length + subRows.length === 0 ? (
          <div className={ROW}>
            <span className="text-meta text-muted">{t("connconfig.empty")}</span>
          </div>
        ) : null}
      </div>
      <div className={FOOT}>
        {t("connconfig.foot")}
      </div>
      {err && <div className="text-meta text-red-600 mt-2">{err}</div>}
      {dialog && (
        <ConfigurationDialog
          mode={dialog.mode}
          cfg={dialog.mode === "edit" ? dialog.cfg : undefined}
          holders={holders}
          installationId={installationId}
          owner={owner}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            setErr(null);
            reload();
          }}
        />
      )}
    </>
  );
}

// --- the dialog --------------------------------------------------------------

function ConfigurationDialog({
  mode,
  cfg,
  holders,
  installationId,
  owner,
  onClose,
  onSaved,
}: {
  mode: "add" | "edit";
  cfg?: Configuration;
  holders: MachineRef[];
  installationId: string;
  owner: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<string[]>(cfg?.repos ?? []);
  const [repoDraft, setRepoDraft] = useState("");
  const [event, setEvent] = useState<string>(cfg?.event ?? "mention");
  const [name, setName] = useState(cfg?.name ?? "");
  const [who, setWho] = useState<string[]>(cfg?.who ?? []);
  const [whoDraft, setWhoDraft] = useState("");
  const [kind, setKind] = useState<"new" | "existing">(cfg?.target.kind ?? "new");
  const [machineId, setMachineId] = useState(cfg?.target.machine_id || holders[0]?.id || "desktop");
  const [persona, setPersona] = useState(cfg?.target.persona ?? "");
  const [models, setModels] = useState<string[]>(cfg?.target.models ?? []);
  const [baseDir, setBaseDir] = useState(cfg?.target.base_dir ?? "");
  // Spec §11.5 (owner rulings): Approval mode defaults to Auto-approve; "Send approvals to
  // Inbox" (the Unattended dial) defaults on — nobody is at the keyboard for these sessions.
  const [approvalMode, setApprovalMode] = useState(cfg?.target.approval_mode ?? "auto-approve");
  const [unattended, setUnattended] = useState(cfg?.target.unattended ?? true);
  const [skills, setSkills] = useState<string[]>(cfg?.target.skills ?? []);
  const [instructions, setInstructions] = useState(cfg?.target.instructions ?? "");
  const [advanced, setAdvanced] = useState(false);
  const [board, setBoard] = useState(cfg?.target.board ?? "");
  const [memory, setMemory] = useState(cfg?.target.memory ?? "");
  const [sessionId, setSessionId] = useState(cfg?.target.session_id ?? "");
  const [sessionTitle, setSessionTitle] = useState(cfg?.target.title ?? "");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectable, setSelectable] = useState<string[]>([]);
  const [skillRows, setSkillRows] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const machine = holders.find((h) => h.id === machineId);
  const eventHint = EVENTS.find((e) => e.value === event)?.hint;
  const modeHint = APPROVAL_MODES.find((m) => m.value === approvalMode)?.hint;

  // What the chosen machine has: its coworkers, its runnable models, its skills.
  useEffect(() => {
    let live = true;
    const gui = machine?.guiId ?? null;
    getPersonasIndex(gui).then((r) => live && setPersonas(r.personas.filter((p) => p.enabled))).catch(() => live && setPersonas([]));
    (gui ? getMachineSettings(gui) : getSettings())
      .then((s) => live && setSelectable(s.models || []))
      .catch(() => live && setSelectable([]));
    listSkills(undefined, gui).then((rows) => live && setSkillRows(rows.filter((r) => r.enabled).map((r) => r.name))).catch(() => live && setSkillRows([]));
    return () => {
      live = false;
    };
  }, [machineId]);
  const personaRow = personas.find((p) => p.id === persona);
  // The coworker's own list when it has one, else the machine's selectable models.
  const offered = useMemo(() => (personaRow?.models?.length ? personaRow.models : selectable), [personaRow, selectable]);
  useEffect(() => {
    // Keep the chosen models within what is offered; default to the first runnable.
    const kept = models.filter((m) => offered.includes(m));
    if (kept.length !== models.length) setModels(kept);
    if (kept.length === 0 && offered.length) {
      const first = offered.find((m) => selectable.includes(m)) ?? offered[0];
      setModels([first]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offered.join("|")]);

  const addRepo = (raw: string) => {
    const r = raw.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "").replace(/\/$/, "");
    if (!r) return;
    if (!/^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)?$/.test(r)) {
      setErr(t("connconfig.err_repo_format"));
      return;
    }
    setErr(null);
    setRepos((list) => (list.includes(r) ? list : [...list, r]));
    setRepoDraft("");
  };
  const addWho = (raw: string) => {
    const w = raw.trim().replace(/^@/, "");
    if (!w) return;
    setWho((list) => (list.includes(w) ? list : [...list, w]));
    setWhoDraft("");
  };
  const target = (): ConfigurationTarget =>
    kind === "existing"
      ? { kind: "existing", machine_id: machineId, session_id: sessionId, title: sessionTitle }
      : { kind: "new", machine_id: machineId, persona, models, base_dir: baseDir.trim(), skills, instructions: instructions.trim(), board: board.trim(), memory: memory.trim(), approval_mode: approvalMode, unattended };

  const save = async () => {
    setErr(null);
    if (mode === "add" && repos.length === 0) return setErr(t("connconfig.err_pick_repo"));
    if (event === "named_mention" && !NAME_RE.test(name.trim().toLowerCase())) return setErr(t("connconfig.err_name_format"));
    if (kind === "existing" && !sessionId) return setErr(t("connconfig.err_pick_session"));
    setBusy(true);
    const r =
      mode === "add"
        ? await addConfiguration({ installation_id: installationId, repos, event, name: name.trim().toLowerCase(), who, target: target() })
        : await editConfiguration(cfg!.config_id, { who, target: target() });
    setBusy(false);
    if (r.ok) return onSaved();
    const held = (r as { held_by?: Record<string, unknown> }).held_by as { key?: string; title?: string; source?: string; configuration?: { key?: string } } | undefined;
    if (r.error === "name_taken") setErr(t("connconfig.err_name_taken", { name: name.trim().toLowerCase() }));
    else if (r.error === "subscribed") setErr(t("connconfig.err_subscribed", { repo: held?.source?.replace(/^github:/, "") || t("connconfig.that_repository") }));
    else if (r.error === "held") setErr(t("connconfig.err_held", { key: held?.key || held?.configuration?.key || t("connconfig.that_repository_and_event") }));
    else setErr(r.error);
  };

  return (
    <div className="fixed inset-0 z-40" data-testid="cfg-dialog">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        className="absolute left-1/2 top-[6%] -translate-x-1/2 w-[560px] max-w-[calc(100vw-2rem)] max-h-[88vh] overflow-auto bg-panel rounded-2xl border border-line shadow-2xl p-5"
        role="dialog"
        aria-label={mode === "add" ? t("connconfig.add_title") : t("connconfig.edit_title")}
      >
        <div className="text-heading font-semibold mb-1">{mode === "add" ? t("connconfig.add_title") : t("connconfig.edit_title")}</div>
        <div className="text-ui text-muted mb-4">
          {mode === "add"
            ? t("connconfig.add_blurb")
            : t("connconfig.edit_blurb", { repos: cfg?.repos.join(", "), event: eventLabel(cfg?.event ?? "", t) })}
        </div>

        <div className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-3 items-start">
          {mode === "add" && (
            <>
              <Label>{t("connconfig.label_repositories")}</Label>
              <div>
                <div className="flex flex-wrap gap-1.5 mb-1.5">
                  {repos.map((r) => (
                    <Chip key={r} onRemove={() => setRepos((l) => l.filter((x) => x !== r))} testId={`cfg-repo-${r}`}>
                      {r.includes("/") ? r : t("connconfig.all_repos_in", { owner: r })}
                    </Chip>
                  ))}
                </div>
                <div className="flex gap-1.5">
                  <input
                    className={INPUT + " flex-1"}
                    placeholder={t("connconfig.repo_placeholder")}
                    value={repoDraft}
                    onChange={(e) => setRepoDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === ",") {
                        e.preventDefault();
                        addRepo(repoDraft);
                      }
                    }}
                    data-testid="cfg-repo-input"
                  />
                  {owner && !repos.includes(owner) && (
                    <button className={PILL_LINE + " !py-1"} data-testid="cfg-all-repos" onClick={() => addRepo(owner)}>
                      {t("connconfig.all_repos_in", { owner })}
                    </button>
                  )}
                </div>
              </div>

              <Label>{t("connconfig.label_event")}</Label>
              <div>
                <select className={INPUT} value={event} onChange={(e) => setEvent(e.target.value)} data-testid="cfg-event">
                  {EVENTS.map((e) => (
                    <option key={e.value} value={e.value}>
                      {t(e.label)}
                    </option>
                  ))}
                </select>
                {eventHint ? <div className="text-meta text-faint mt-1">{t(eventHint)}</div> : null}
              </div>
              {event === "named_mention" && (
                <>
                  <Label>{t("connconfig.label_name")}</Label>
                  <div>
                    <input className={INPUT + " font-mono w-60"} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("connconfig.name_placeholder")} data-testid="cfg-name" />
                    <div className="text-meta text-faint mt-1">{t("connconfig.name_hint", { name: name || t("connconfig.name_fallback") })}</div>
                  </div>
                </>
              )}
            </>
          )}

          <Label>{t("connconfig.label_who")}</Label>
          <div>
            <div className="flex flex-wrap gap-1.5 items-center">
              <span className="inline-flex items-center text-ui border border-line rounded-full px-2.5 py-0.5 text-muted">{t("connconfig.you")}</span>
              {who.map((w) => (
                <Chip key={w} onRemove={() => setWho((l) => l.filter((x) => x !== w))} testId={`cfg-who-${w}`}>
                  {w}
                </Chip>
              ))}
              <input
                className={INPUT + " w-40"}
                placeholder={t("connconfig.who_placeholder")}
                value={whoDraft}
                onChange={(e) => setWhoDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addWho(whoDraft);
                  }
                }}
                data-testid="cfg-who-input"
              />
            </div>
            <div className="text-meta text-faint mt-1">{t("connconfig.who_hint")}</div>
          </div>

          <Label>{t("connconfig.label_send_to")}</Label>
          <div className="space-y-1.5">
            <Radio checked={kind === "new"} onChange={() => setKind("new")} testId="cfg-target-new" label={t("connconfig.new_session")} hint={event === "mention" || event === "named_mention" ? t("connconfig.one_per_thread") : t("connconfig.one_per_pr_or_issue")} />
            <Radio checked={kind === "existing"} onChange={() => setKind("existing")} testId="cfg-target-existing" label={t("connconfig.existing_session")} hint="" />
          </div>

          <Label>{t("connconfig.label_machine")}</Label>
          <select className={INPUT} value={machineId} onChange={(e) => setMachineId(e.target.value)} data-testid="cfg-machine">
            {holders.map((h) => (
              <option key={h.id} value={h.id}>
                {h.glyph} {h.name}
              </option>
            ))}
          </select>

          {kind === "new" ? (
            <>
              <Label>{t("connconfig.label_coworker")}</Label>
              <select className={INPUT} value={persona} onChange={(e) => setPersona(e.target.value)} data-testid="cfg-coworker">
                <option value="">{t("connconfig.default_coworker")}</option>
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>

              <Label>{t("connconfig.label_models")}</Label>
              <div>
                <div className="flex flex-wrap gap-1.5">
                  {offered.map((m) => {
                    const i = models.indexOf(m);
                    const on = i >= 0;
                    const runnable = selectable.includes(m);
                    return (
                      <button
                        key={m}
                        className={"inline-flex items-center gap-1.5 text-meta rounded-full px-2 py-0.5 border " + (on ? "border-accent text-ink" : "border-line text-muted") + (runnable ? "" : " border-dashed")}
                        title={runnable ? m : t("connconfig.model_unavailable", { model: m, machine: machine?.name ?? t("connconfig.this_machine") })}
                        onClick={() => setModels((l) => (on ? l.filter((x) => x !== m) : [...l, m]))}
                        data-testid={`cfg-model-${m}`}
                      >
                        {on && <span className="text-[10px] font-medium bg-accent text-white rounded-full px-1.5">{i + 1}</span>}
                        {shortModel(m)}
                        {!runnable && <span className="text-faint">· {t("connconfig.not_here")}</span>}
                      </button>
                    );
                  })}
                  {offered.length === 0 && <span className="text-meta text-faint">{t("connconfig.no_models")}</span>}
                </div>
                <div className="text-meta text-faint mt-1">{t("connconfig.models_hint")}</div>
              </div>

              <Label>{t("connconfig.label_base_dir")}</Label>
              <div>
                <input className={INPUT + " w-full"} value={baseDir} onChange={(e) => setBaseDir(e.target.value)} placeholder={t("connconfig.base_dir_placeholder", { machine: machine?.name ?? t("connconfig.the_machine") })} data-testid="cfg-base-dir" />
                <div className="mt-2 text-meta text-faint">{t("connconfig.session_directory_hint")}</div>
              </div>

              <Label>{t("connconfig.label_approvals")}</Label>
              <div>
                <select className={INPUT} value={approvalMode} onChange={(e) => setApprovalMode(e.target.value)} data-testid="cfg-approval-mode">
                  {APPROVAL_MODES.map((m) => (
                    <option key={m.value} value={m.value}>
                      {t(m.label)}
                    </option>
                  ))}
                </select>
                <span className="block text-meta text-faint mt-1">
                  {modeHint ? t(modeHint) : null}
                  {approvalMode === "bypass-approvals" && (personaRow?.tools ?? []).includes("shell") ? " " + t("connconfig.shell_warning") : ""}
                </span>
                <label className="flex items-start gap-2 mt-2 text-ui">
                  <input type="checkbox" checked={unattended} onChange={(e) => setUnattended(e.target.checked)} data-testid="cfg-unattended" className="mt-1" />
                  <span>
                    {t("connconfig.approvals_to_inbox")}
                    <span className="block text-meta text-faint">
                      {t("connconfig.approvals_to_inbox_hint")}
                    </span>
                  </span>
                </label>
              </div>

              <Label>{t("connconfig.label_skills")}</Label>
              <div className="flex flex-wrap gap-1.5">
                {skillRows.map((sname) => {
                  const on = skills.includes(sname);
                  return (
                    <button
                      key={sname}
                      className={"text-meta rounded-full px-2 py-0.5 border " + (on ? "border-accent text-ink" : "border-line text-muted")}
                      onClick={() => setSkills((l) => (on ? l.filter((x) => x !== sname) : [...l, sname]))}
                      data-testid={`cfg-skill-${sname}`}
                    >
                      {sname}
                    </button>
                  );
                })}
                {skillRows.length === 0 && <span className="text-meta text-faint">{t("connconfig.no_skills")}</span>}
              </div>

              <Label>{t("connconfig.label_instructions")}</Label>
              <textarea className={INPUT + " w-full min-h-[64px]"} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder={t("connconfig.instructions_placeholder")} data-testid="cfg-instructions" />

              <div className="col-span-2 border-t border-line pt-2">
                <button className="text-ui text-muted" onClick={() => setAdvanced((a) => !a)} data-testid="cfg-advanced">
                  {advanced ? "▾" : "▸"} {t("connconfig.advanced")} <span className="text-faint">{t("connconfig.advanced_hint")}</span>
                </button>
                {advanced && (
                  <div className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-2 mt-2">
                    <Label>{t("connconfig.label_board")}</Label>
                    <input className={INPUT} value={board} onChange={(e) => setBoard(e.target.value)} placeholder={t("connconfig.board_placeholder")} data-testid="cfg-board" />
                    <Label>{t("connconfig.label_memory")}</Label>
                    <input className={INPUT} value={memory} onChange={(e) => setMemory(e.target.value)} placeholder={t("connconfig.memory_placeholder")} data-testid="cfg-memory" />
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <Label>{t("connconfig.label_session")}</Label>
              <SessionPicker
                holders={holders}
                machineId={machineId}
                value={sessionId}
                onPick={(row, brokerMachine) => {
                  setSessionId(row.session_id);
                  setSessionTitle(row.title || "");
                  setMachineId(brokerMachine);
                }}
              />
            </>
          )}
        </div>

        {err && <div className="text-meta text-red-600 mt-3" data-testid="cfg-error">{err}</div>}
        <div className="flex justify-end gap-2 mt-4">
          <button className={PILL_LINE} onClick={onClose}>
            {t("connconfig.cancel")}
          </button>
          <button className={PILL_ACCENT} onClick={() => void save()} disabled={busy} data-testid="cfg-save">
            {mode === "add" ? t("connconfig.add") : t("connconfig.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-ui text-muted pt-1">{children}</span>;
}

function Chip({ children, onRemove, testId }: { children: React.ReactNode; onRemove: () => void; testId?: string }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1 text-ui border border-line rounded-full pl-2.5 pr-1 py-0.5" data-testid={testId}>
      {children}
      <button className={XBTN} onClick={onRemove} title={t("common.remove")}>
        ×
      </button>
    </span>
  );
}

function Radio({ checked, onChange, testId, label, hint }: { checked: boolean; onChange: () => void; testId: string; label: string; hint: string }) {
  return (
    <label className={"flex items-center gap-2 px-2.5 py-1.5 rounded-lg border cursor-pointer " + (checked ? "border-accent bg-accentSoft/40" : "border-line")}>
      <input type="radio" checked={checked} onChange={onChange} data-testid={testId} />
      <span className="text-ui">{label}</span>
      {hint && <span className="text-meta text-faint ml-auto">{hint}</span>}
    </label>
  );
}

// The Existing-session picker (UX-050 third pass): search; grouped by machine,
// most recent first; pinned and lead sessions float to the top; sessions a
// mention started sit behind "Show all"; eight per machine before "Show all".
function SessionPicker({
  holders,
  machineId,
  value,
  onPick,
}: {
  holders: MachineRef[];
  machineId: string;
  value: string;
  onPick: (row: SessionRow, brokerMachine: string) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SessionInfo[] | null>(null); // null = still loading
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});
  useEffect(() => {
    getAllSessions().then(setRows).catch(() => setRows([]));
  }, []);
  // GUI machine id → broker id, through the holders the page knows.
  const brokerOf = (s: SessionInfo): string | null => {
    if (!s.machine) return holders.find((h) => h.id === "desktop") ? "desktop" : null;
    return holders.find((h) => h.guiId === s.machine)?.id ?? null;
  };
  const needle = q.trim().toLowerCase();
  const groups = holders
    .map((h) => {
      const all = (rows ?? [])
        .filter((s) => !s.archived && brokerOf(s) === h.id)
        .filter((s) => !needle || (s.title || "").toLowerCase().includes(needle) || (s.agent || "").toLowerCase().includes(needle) || h.name.toLowerCase().includes(needle))
        .sort((a, b) => Number(!!b.pinned || !!(b as SessionRow & { lead?: boolean }).lead) - Number(!!a.pinned || !!(a as SessionRow & { lead?: boolean }).lead) || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
      const primary = all.filter((s) => !s.origin);
      const rest = all.filter((s) => !!s.origin);
      const open = !!showAll[h.id] || !!needle;
      const shown = open ? [...primary, ...rest] : primary.slice(0, 8);
      const hidden = all.length - shown.length;
      return { h, shown, hidden, open };
    })
    .filter((g) => g.shown.length || g.hidden);
  return (
    <div className="border border-line rounded-lg overflow-hidden" data-testid="cfg-session-picker">
      <input className="w-full px-2.5 py-1.5 text-ui bg-transparent outline-none border-b border-line" placeholder={t("connconfig.session_search_placeholder")} value={q} onChange={(e) => setQ(e.target.value)} data-testid="cfg-session-search" />
      <div className="max-h-56 overflow-auto">
        {rows === null && <div className="px-2.5 py-2 text-meta text-faint">{t("connconfig.loading_sessions")}</div>}
        {rows !== null && groups.length === 0 && <div className="px-2.5 py-2 text-meta text-faint">{t("connconfig.no_sessions")}</div>}
        {groups.map(({ h, shown, hidden, open }) => (
          <div key={h.id}>
            <div className="px-2.5 pt-1.5 text-meta text-faint">
              {h.glyph} {h.name}
              {h.id === machineId ? "" : ""}
            </div>
            {shown.map((s) => (
              <button
                key={s.session_id}
                className={"w-full text-left px-4 py-1 text-ui hover:bg-paper " + (s.session_id === value ? "bg-accentSoft/40" : "")}
                onClick={() => onPick(s as SessionRow, h.id)}
                data-testid={`cfg-session-${s.session_id}`}
              >
                {s.pinned || (s as SessionRow & { lead?: boolean }).lead ? "📌 " : ""}
                {s.title || s.session_id} <span className="text-faint">· {s.agent}</span>
              </button>
            ))}
            {!open && hidden > 0 && (
              <button className="px-4 pb-1.5 text-meta text-faint" onClick={() => setShowAll((x) => ({ ...x, [h.id]: true }))} data-testid={`cfg-session-more-${h.id}`}>
                {t("connconfig.show_all_more", { n: hidden })}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
