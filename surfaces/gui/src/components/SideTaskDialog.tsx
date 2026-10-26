// "+ ▸ Side task…": start a side task (coworker/sidetasks.py) from the user's own title and
// brief, without going through the model — a model may read "in a separate chat" as a
// figure of speech and do the work in place. The side chat runs on this chat's coworker,
// folder and model; its report comes back as a card. Escape or the scrim cancels.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface Props {
  onSubmit: (title: string, brief: string) => Promise<{ ok: boolean; error?: string }>;
  onCancel: () => void;
}

export function SideTaskDialog({ onSubmit, onCancel }: Props) {
  const { t } = useTranslation();
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const ready = title.trim().length > 0 && brief.trim().length > 0 && !busy;
  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setError("");
    try {
      const res = await onSubmit(title.trim(), brief.trim());
      if (!res.ok) setError(res.error || t("sidetask.dialog_error"));
    } catch {
      setError(t("sidetask.dialog_error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate-overlay" onClick={onCancel}>
      <div
        className="w-[460px] bg-panel border border-line rounded-xl2 shadow-2xl p-[18px]"
        data-testid="side-task-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-body font-semibold text-ink mb-1">{t("sidetask.dialog_title")}</h3>
        <p className="text-ui text-muted mb-3">{t("sidetask.dialog_sub")}</p>
        <label className="block text-label text-faint mb-1" htmlFor="side-task-title">
          {t("sidetask.dialog_name")}
        </label>
        <input
          id="side-task-title"
          className="w-full mb-3 px-2.5 py-1.5 rounded-md bg-paper border border-line text-ui text-ink outline-none focus:border-accent"
          value={title}
          autoFocus
          placeholder={t("sidetask.dialog_name_placeholder")}
          onChange={(e) => setTitle(e.target.value)}
        />
        <label className="block text-label text-faint mb-1" htmlFor="side-task-brief">
          {t("sidetask.dialog_brief")}
        </label>
        <textarea
          id="side-task-brief"
          className="w-full mb-3 px-2.5 py-1.5 rounded-md bg-paper border border-line text-ui text-ink outline-none focus:border-accent resize-y"
          rows={5}
          value={brief}
          placeholder={t("sidetask.dialog_brief_placeholder")}
          onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
          }}
        />
        {error && (
          <p className="text-ui text-danger mb-2" role="alert">
            {error}
          </p>
        )}
        <div className="flex items-center justify-end gap-2">
          <button type="button" className="btn" onClick={onCancel}>
            {t("sidetask.dialog_cancel")}
          </button>
          <button
            type="button"
            className="btn primary"
            data-testid="side-task-start"
            disabled={!ready}
            onClick={() => void submit()}
          >
            {busy ? t("sidetask.dialog_starting") : t("sidetask.dialog_start")}
          </button>
        </div>
      </div>
    </div>
  );
}
