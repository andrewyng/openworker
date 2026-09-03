import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TAG_QUIET, TAG_WARN } from "./ui";

// Shared grammar for per-tool review lists (owner ask 2026-08-30): the MCP server
// page and the catalog-connector pages sit side by side on Connectors, so their tool
// lists must speak one dialect — same row anatomy, same chip family, same count
// line, same save receipt. Both lists RENDER THROUGH these primitives; imitation
// would drift, extraction cannot.
//
// What deliberately stays per-surface (do not "unify" these — each difference is a
// security decision, not sloppiness):
// - read/asks-first chips exist ONLY for catalog connectors: their kinds are pinned
//   by our own audited code. An MCP server claiming "read-only" would be the server
//   describing itself — a label, not evidence — so MCP rows carry no risk chips and
//   the page states the blanket truth ("every tool asks…") once instead.
// - MCP names render mono and raw: the exact string IS the security identity
//   (trust rules and collision defenses key on it). Connector labels are ours.
// - The "new" badge is MCP-only (remote menus drift; the catalog ships with the app).
// - The first-review ceremony is MCP-only (it creates the fail-closed contract).

/** One tool row: checkbox · name (+badge) · description · right-aligned status. */
export function ToolRow({
  checked,
  onToggle,
  name,
  mono = false,
  badge,
  description,
  right,
  title,
  checkboxTestId,
}: {
  checked: boolean;
  onToggle: () => void;
  name: string;
  mono?: boolean;
  badge?: ReactNode;
  description?: ReactNode;
  right?: ReactNode;
  title?: string;
  checkboxTestId?: string;
}) {
  return (
    <label
      className="flex items-start gap-2.5 px-4 py-2 cursor-pointer hover:bg-paper/40"
      title={title}
    >
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        data-testid={checkboxTestId}
        onChange={onToggle}
      />
      <span className="min-w-0 flex-1">
        <span className={mono ? "font-mono text-[12px]" : "text-[13px] font-medium"}>{name}</span>
        {badge}
        {description}
      </span>
      {right}
    </label>
  );
}

/** The right-column chip answers ONE question: what happens when this is called?
 *  Offered only where the answer is OUR code's word (catalog connectors). */
export function ApprovalChip({ kind }: { kind: "read" | "asks_first" }) {
  const { t } = useTranslation();
  return kind === "read" ? (
    <span className={TAG_QUIET + " mt-0.5"} title={t("tools.read_tip")}>
      {t("tools.read")}
    </span>
  ) : (
    <span className={TAG_WARN + " mt-0.5"} title={t("tools.asks_first_tip")}>
      {t("tools.asks_first")}
    </span>
  );
}

/** "N of M enabled — unchecked tools never reach a session" — one phrasing, both
 *  pages; per-surface extras (growth note, always-allowed count) append after.
 *  Inside an expanded disclosure the summary row above already shows the count,
 *  so `showCount: false` renders the explanation clauses alone (owner catch
 *  2026-08-30: the count printed twice in adjacent lines). */
export function ToolsCountLine({
  checked,
  total,
  extra,
  showCount = true,
}: {
  checked: number;
  total: number;
  extra?: ReactNode;
  showCount?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span className="block text-[12px] text-faint">
      {showCount && (
        <>
          {t("tools.enabled_count", { checked, total })}
          {" — "}
        </>
      )}
      {t("tools.never_reach")}
      {extra}
    </span>
  );
}

/** The save receipt: flash "Saved" for a moment after an auto-save lands. */
export function useSavedTick(): [boolean, () => void] {
  const [on, setOn] = useState(false);
  const flash = () => {
    setOn(true);
    window.setTimeout(() => setOn(false), 1500);
  };
  return [on, flash];
}

export function SavedTick({ show, testId }: { show: boolean; testId?: string }) {
  const { t } = useTranslation();
  if (!show) return null;
  return (
    <span className="text-[12px] text-ok" data-testid={testId}>
      {t("tools.saved")}
    </span>
  );
}
