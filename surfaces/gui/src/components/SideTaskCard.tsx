// A side task's report (coworker/sidetasks.py): one quiet line that expands to the summary.
// Display-only — this chat's model never saw it. The user decides: add it to the chat (it
// goes into the composer as a text attachment and enters the context only when sent),
// dismiss it, or open the side chat. Nothing happens on its own.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Item } from "../types";
import { Markdown } from "./Markdown";

export type SideTaskItem = Extract<Item, { kind: "sidetask" }>;
export type SideTaskAction = "add" | "dismiss" | "open";

export function SideTaskCard({
  item,
  onAction,
}: {
  item: SideTaskItem;
  onAction?: (item: SideTaskItem, action: SideTaskAction) => void;
}) {
  const { t } = useTranslation();
  // A fresh card opens so the report is read in place; a decided one folds to its line.
  const [open, setOpen] = useState(item.status === "new");
  const verdict =
    item.status === "added"
      ? t("sidetask.added")
      : item.status === "dismissed"
        ? t("sidetask.dismissed")
        : null;
  return (
    <div className="sidetask-card" data-testid="sidetask-card" data-status={item.status}>
      <button
        type="button"
        className="sidetask-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="sidetask-label">{t("sidetask.report_arrived")}</span>
        <span className="sidetask-title">{item.title}</span>
        {verdict && (
          <span className="sidetask-verdict" data-testid="sidetask-verdict">
            {verdict}
          </span>
        )}
        <span className="sidetask-chev" aria-hidden>
          {open ? "⌃" : "⌄"}
        </span>
      </button>
      {open && (
        <div className="sidetask-body">
          <Markdown text={item.summary} />
          {item.reportPath && (
            <div className="sidetask-path">
              {t("sidetask.full_report")} <code>{item.reportPath}</code>
            </div>
          )}
          <div className="sidetask-actions">
            {item.status === "new" && (
              <button
                type="button"
                className="btn primary"
                data-testid="sidetask-add"
                onClick={() => onAction?.(item, "add")}
              >
                {t("sidetask.add_to_chat")}
              </button>
            )}
            {item.status === "new" && (
              <button
                type="button"
                className="btn"
                data-testid="sidetask-dismiss"
                onClick={() => onAction?.(item, "dismiss")}
              >
                {t("sidetask.dismiss")}
              </button>
            )}
            <button
              type="button"
              className="btn"
              data-testid="sidetask-open"
              onClick={() => onAction?.(item, "open")}
            >
              {t("sidetask.open_chat")}
            </button>
          </div>
          <div className="sidetask-note">{t("sidetask.note")}</div>
        </div>
      )}
    </div>
  );
}
