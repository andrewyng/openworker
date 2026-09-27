import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteAllMemory,
  deleteMemory,
  getMemory,
  getMemorySettings,
  setMemorySettings,
  updateMemory,
  MEMORY_CHANGED,
  type Machine,
  type MemoryEntry,
  type MemorySettings,
} from "../api";
import { useMachineData } from "../useMachineData";
import { CachedNote, LoadingRow, UnreachableRow } from "./ScopedStatus";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { Toggle } from "./Toggle";

// MEMORY-SPEC §5.3: the one memory screen. A plain-language list of remembered facts
// (edit/delete per row), the on/off toggle, delete-all, and the User Rules textarea —
// no scope vocabulary, no markdown, no files. Everything else memory does happens in
// chat (toast §5.1, attribution §5.2).
const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-ui font-medium text-ink";
const FIELD_HELP = "text-meta text-muted mt-1.5 leading-relaxed";
const BTN_ACCENT =
  "text-ui px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";

export function MemorySection({
  machine,
  onAskWorker,
}: {
  // UX-046 machine scope: a remote machine's memory is VIEW-ONLY (owner
  // ruling 2026-08-30) — memory belongs to that machine's worker; changing
  // it is a conversation, and `onAskWorker` starts one on that machine.
  machine?: Machine | null;
  onAskWorker?: (machineId: string) => void;
} = {}) {
  const { t } = useTranslation();
  const mid = machine?.id ?? null;
  const readOnly = !!machine;
  const mem = useMachineData(`memory:${mid ?? "local"}`, async () => ({
    settings: await getMemorySettings(mid),
    entries: await getMemory(mid),
  }));
  const settings = mem.data?.settings ?? null;
  const entries = mem.data?.entries ?? null;
  // State-change copy (§5.3): shown under the toggle / list after an action.
  const [toggleMsg, setToggleMsg] = useState<string | null>(null);
  const [listMsg, setListMsg] = useState<string | null>(null);

  const refresh = mem.refresh;
  // Stay current while the screen is open: a save/edit landing in a conversation, or
  // the window regaining focus after one did. Without this the list is a snapshot from
  // whenever the page mounted — it showed "Nothing yet" seconds after a real save
  // (owner-hit 2026-07-28), which reads as "it didn't work".
  useEffect(() => {
    window.addEventListener(MEMORY_CHANGED, refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener(MEMORY_CHANGED, refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);

  const toggleEnabled = async () => {
    if (!settings || readOnly) return;
    const next = await setMemorySettings({ enabled: !settings.enabled });
    refresh();
    setToggleMsg(next.enabled ? t("memory.on_msg") : t("memory.off_msg"));
  };

  const wipeAll = async () => {
    if (!window.confirm(t("memory.wipe_confirm"))) return;
    await deleteAllMemory();
    setListMsg(t("memory.wiped_msg"));
    refresh();
  };

  if (!settings || entries === null) {
    if (mem.error && machine)
      return (
        <section>
          <PanelHead title={t("settings.tab.memory")} sub={t("onmachine.memory.on_machine", { machine: machine.name })} />
          <div className={CARD}>
            <UnreachableRow machineName={machine.name} />
          </div>
        </section>
      );
    return <LoadingRow what={machine ? t("onmachine.memory.loading_what_machine", { machine: machine.name }) : t("onmachine.memory.loading_what")} />;
  }

  return (
    <section>
      <PanelHead
        title={t("settings.tab.memory")}
        sub={
          machine
            ? t("onmachine.memory.remote_sub", { machine: machine.name })
            : t("memory.section_sub")
        }
      />
      {mem.cachedAt ? <CachedNote at={mem.cachedAt} /> : null}

      {readOnly && (
        <div className={CARD + " p-4 mb-4 flex items-center gap-3"} data-testid="memory-remote-cta">
          <div className="min-w-0 flex-1">
            <div className={FIELD_LABEL}>
              {settings.enabled ? t("onmachine.memory.remote_on") : t("onmachine.memory.remote_off")}
            </div>
            <div className="text-meta text-muted mt-0.5">
              {t("onmachine.memory.remote_help")}
            </div>
          </div>
          {onAskWorker && (
            <button
              className={BTN_ACCENT}
              onClick={() => onAskWorker(machine!.id)}
              data-testid="memory-ask-worker"
            >
              {t("onmachine.memory.ask_worker")}
            </button>
          )}
        </div>
      )}

      {/* On/off — one switch, no other setup (§5.4). */}
      {!readOnly && (
      <div className={CARD + " p-4 mb-4"} data-testid="memory-toggle-card">
        <div className="flex items-center gap-3">
          <Toggle checked={settings.enabled} onChange={toggleEnabled} title={t("memory.toggle_title")} />
          <div className="min-w-0 flex-1">
            <div className={FIELD_LABEL}>{t("memory.toggle_label")}</div>
            <div className="text-meta text-muted mt-0.5">{t("memory.toggle_help")}</div>
          </div>
        </div>
        {toggleMsg && (
          <div className="text-ui text-muted mt-3 pt-3 border-t border-line" data-testid="memory-toggle-msg">
            {toggleMsg}
          </div>
        )}
      </div>
      )}

      {/* What I've learned (§5.3): directly under the toggle that governs it — the off
          message ("what I already know is kept, delete it below") points here. */}
      <div className={CARD + " p-4 mb-4"} data-testid="memory-list-card">
        <div className="flex items-center gap-2">
          <div className={FIELD_LABEL + " flex-1"}>
            {readOnly ? t("onmachine.memory.learned_title") : t("memory.learned_title")}
          </div>
          {entries.length > 0 && !readOnly && (
            <button
              className="text-meta text-danger/80 hover:text-danger"
              data-testid="memory-delete-all"
              onClick={wipeAll}
            >
              {t("memory.forget_all")}
            </button>
          )}
        </div>
        <div className={FIELD_HELP}>
          {readOnly
            ? t("onmachine.memory.learned_help")
            : t("memory.learned_help")}
        </div>
        {listMsg && (
          <div className="text-ui text-muted mt-2.5" data-testid="memory-list-msg">
            {listMsg}
          </div>
        )}
        {entries.length === 0 ? (
          !listMsg && (
            <div className="text-meta text-muted mt-3" data-testid="memory-empty">
              {t("memory.empty")}
            </div>
          )
        ) : (
          <div className="mt-3 divide-y divide-line">
            {entries.map((m) => (
              <MemoryRow key={m.id} entry={m} onChanged={refresh} readOnly={readOnly} />
            ))}
          </div>
        )}
      </div>

      {/* Your instructions (§6): user-authored, toggle-independent — so it sits apart
          from the auto-memory pair above. The agent never edits these. */}
      <UserRulesCard settings={settings} onSaved={() => refresh()} readOnly={readOnly} />
    </section>
  );
}

function UserRulesCard({
  settings,
  onSaved,
  readOnly,
}: {
  settings: MemorySettings;
  onSaved: (s: MemorySettings) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(settings.user_rules);
  const [savedMsg, setSavedMsg] = useState(false);

  const save = async () => {
    const next = await setMemorySettings({ user_rules: draft });
    onSaved(next);
    setSavedMsg(true);
    window.setTimeout(() => setSavedMsg(false), 3000);
  };

  return (
    <div className={CARD + " p-4"} data-testid="user-rules-card">
      <div className={FIELD_LABEL}>{t("memory.rules_title")}</div>
      <div className={FIELD_HELP}>{t("memory.rules_help")}</div>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
        disabled={readOnly}
        placeholder={t("memory.rules_placeholder")}
        data-testid="user-rules-input"
        className="w-full mt-2.5 px-3 py-2.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent resize-y leading-relaxed"
      />
      <div className="flex items-center gap-3 mt-2">
        {!readOnly && (
        <button
          className={BTN_ACCENT}
          onClick={save}
          disabled={draft === settings.user_rules}
          data-testid="user-rules-save"
        >
          {t("memory.save")}
        </button>
        )}
        {savedMsg && (
          <span className="text-ui text-muted">{t("memory.rules_saved")}</span>
        )}
      </div>
    </div>
  );
}

function MemoryRow({
  entry,
  onChanged,
  readOnly,
}: {
  entry: MemoryEntry;
  onChanged: () => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.content);

  const save = async () => {
    const text = draft.trim();
    if (text && text !== entry.content) await updateMemory(entry.id, text);
    setEditing(false);
    onChanged();
  };
  const remove = async () => {
    await deleteMemory(entry.id);
    onChanged();
  };

  if (editing)
    return (
      <div className="py-2.5" data-testid={`memory-edit-${entry.id}`}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          autoFocus
          className="w-full px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent resize-y leading-relaxed"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void save();
            }
            if (e.key === "Escape") setEditing(false);
          }}
        />
        <div className="flex items-center gap-2.5 mt-1.5">
          <button className={BTN_ACCENT} onClick={() => void save()}>
            {t("memory.save")}
          </button>
          <button className="text-ui text-muted hover:text-ink" onClick={() => setEditing(false)}>
            {t("manage.cancel")}
          </button>
        </div>
      </div>
    );

  return (
    <div className="py-2.5 flex items-start gap-2.5 group" data-testid={`memory-row-${entry.id}`}>
      <div className="min-w-0 flex-1 text-ui leading-relaxed">{entry.content}</div>
      {!readOnly && (<>
      <button
        className="text-faint hover:text-ink shrink-0 mt-0.5"
        title={t("memory.fix_tip")}
        data-testid={`memory-edit-btn-${entry.id}`}
        onClick={() => {
          setDraft(entry.content);
          setEditing(true);
        }}
      >
        <Icon name="pencil" size={14} />
      </button>
      <button
        className="text-faint hover:text-danger shrink-0 mt-0.5"
        title={t("memory.delete_tip")}
        data-testid={`memory-delete-${entry.id}`}
        onClick={() => void remove()}
      >
        <Icon name="trash" size={14} />
      </button>
      </>)}
    </div>
  );
}
