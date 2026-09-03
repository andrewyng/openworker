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
  type MemoryEntry,
  type MemorySettings,
} from "../api";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { Toggle } from "./Toggle";

// MEMORY-SPEC §5.3: the one memory screen. A plain-language list of remembered facts
// (edit/delete per row), the on/off toggle, delete-all, and the User Rules textarea —
// no scope vocabulary, no markdown, no files. Everything else memory does happens in
// chat (toast §5.1, attribution §5.2).
const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[13px] font-medium text-ink";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";

export function MemorySection() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  // State-change copy (§5.3): shown under the toggle / list after an action.
  const [toggleMsg, setToggleMsg] = useState<string | null>(null);
  const [listMsg, setListMsg] = useState<string | null>(null);

  const refresh = () => {
    getMemorySettings().then(setSettings).catch(() => setSettings(null));
    getMemory().then(setEntries).catch(() => setEntries([]));
  };
  useEffect(refresh, []);
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
    if (!settings) return;
    const next = await setMemorySettings({ enabled: !settings.enabled });
    setSettings(next);
    setToggleMsg(next.enabled ? t("memory.on_msg") : t("memory.off_msg"));
  };

  const wipeAll = async () => {
    if (!window.confirm(t("memory.wipe_confirm"))) return;
    await deleteAllMemory();
    setListMsg(t("memory.wiped_msg"));
    refresh();
  };

  if (!settings || entries === null)
    return <div className="text-[13px] text-muted">{t("memory.loading")}</div>;

  return (
    <section>
      <PanelHead title={t("settings.tab.memory")} sub={t("memory.section_sub")} />

      {/* On/off — one switch, no other setup (§5.4). */}
      <div className={CARD + " p-4 mb-4"} data-testid="memory-toggle-card">
        <div className="flex items-center gap-3">
          <Toggle checked={settings.enabled} onChange={toggleEnabled} title={t("memory.toggle_title")} />
          <div className="min-w-0 flex-1">
            <div className={FIELD_LABEL}>{t("memory.toggle_label")}</div>
            <div className="text-[12px] text-muted mt-0.5">{t("memory.toggle_help")}</div>
          </div>
        </div>
        {toggleMsg && (
          <div className="text-[13px] text-muted mt-3 pt-3 border-t border-line" data-testid="memory-toggle-msg">
            {toggleMsg}
          </div>
        )}
      </div>

      {/* What I've learned (§5.3): directly under the toggle that governs it — the off
          message ("what I already know is kept, delete it below") points here. */}
      <div className={CARD + " p-4 mb-4"} data-testid="memory-list-card">
        <div className="flex items-center gap-2">
          <div className={FIELD_LABEL + " flex-1"}>{t("memory.learned_title")}</div>
          {entries.length > 0 && (
            <button
              className="text-[12px] text-danger/80 hover:text-danger"
              data-testid="memory-delete-all"
              onClick={wipeAll}
            >
              {t("memory.forget_all")}
            </button>
          )}
        </div>
        <div className={FIELD_HELP}>{t("memory.learned_help")}</div>
        {listMsg && (
          <div className="text-[13px] text-muted mt-2.5" data-testid="memory-list-msg">
            {listMsg}
          </div>
        )}
        {entries.length === 0 ? (
          !listMsg && (
            <div className="text-[12px] text-muted mt-3" data-testid="memory-empty">
              {t("memory.empty")}
            </div>
          )
        ) : (
          <div className="mt-3 divide-y divide-line">
            {entries.map((m) => (
              <MemoryRow key={m.id} entry={m} onChanged={refresh} />
            ))}
          </div>
        )}
      </div>

      {/* Your instructions (§6): user-authored, toggle-independent — so it sits apart
          from the auto-memory pair above. The agent never edits these. */}
      <UserRulesCard settings={settings} onSaved={setSettings} />
    </section>
  );
}

function UserRulesCard({
  settings,
  onSaved,
}: {
  settings: MemorySettings;
  onSaved: (s: MemorySettings) => void;
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
        placeholder={t("memory.rules_placeholder")}
        data-testid="user-rules-input"
        className="w-full mt-2.5 px-3 py-2.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent resize-y leading-relaxed"
      />
      <div className="flex items-center gap-3 mt-2">
        <button
          className={BTN_ACCENT}
          onClick={save}
          disabled={draft === settings.user_rules}
          data-testid="user-rules-save"
        >
          {t("memory.save")}
        </button>
        {savedMsg && (
          <span className="text-[13px] text-muted">{t("memory.rules_saved")}</span>
        )}
      </div>
    </div>
  );
}

function MemoryRow({ entry, onChanged }: { entry: MemoryEntry; onChanged: () => void }) {
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
          className="w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent resize-y leading-relaxed"
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
          <button className="text-[13px] text-muted hover:text-ink" onClick={() => setEditing(false)}>
            {t("manage.cancel")}
          </button>
        </div>
      </div>
    );

  return (
    <div className="py-2.5 flex items-start gap-2.5 group" data-testid={`memory-row-${entry.id}`}>
      <div className="min-w-0 flex-1 text-[13px] leading-relaxed">{entry.content}</div>
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
    </div>
  );
}
