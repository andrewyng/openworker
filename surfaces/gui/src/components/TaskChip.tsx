import { createContext, useContext, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Board } from "../api";

export const OPEN_TASK_EVENT = "ocw-open-task";
export const TaskBoardContext = createContext<{
  board: Board | null;
  sessionId: string;
}>({ board: null, sessionId: "" });

export function taskDot(state: string, waiting = false) {
  return (
    "board-dot " +
    (waiting || state === "blocked"
      ? "blocked"
      : state === "in_progress"
        ? "work"
        : state === "review"
          ? "review"
          : state === "done"
            ? "done"
            : "idle")
  );
}

export function TaskChip({
  id,
  children,
  space,
}: {
  id: string;
  children: ReactNode;
  space?: string;
}) {
  const { t } = useTranslation();
  const { board, sessionId } = useContext(TaskBoardContext);
  const n = /^[1-9]\d*$/.test(id) ? Number(id) : NaN;
  const item = Number.isSafeInteger(n)
    ? board?.items.find((i) => i.id === n)
    : undefined;
  if (!item || !board?.space || (space && space !== board.space))
    return <>{children}</>;
  return (
    <button
      type="button"
      className="task-chip"
      data-testid={`task-chip-${item.id}`}
      title={t("teamview.open_task", { title: item.title })}
      onClick={() =>
        window.dispatchEvent(
          new CustomEvent(OPEN_TASK_EVENT, {
            detail: { id: item.id, sessionId, space: board.space },
          }),
        )
      }
    >
      <span
        className={taskDot(item.state, !!item.waiting)}
        aria-hidden="true"
      />
      <span>{item.title || children}</span>
    </button>
  );
}
