import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TodoItem } from "../types";
import { Icon } from "./Icon";

// The plan dock (owner mock approved 2026-11-07): the coworker's plan for the CURRENT
// task, fused to the top of the composer — the transcript stays untouched, and the
// glance lives where the eye already is when typing. Collapsed: a PLAN tag, the step
// in progress, "2 of 4" and a micro meter. Expanded: the step list. It feeds off the
// same todo_write state as the rail's Progress section (which remains the per-session
// summary), auto-collapses when every step is done, and renders nothing with no plan.

export function PlanDock({ todo }: { todo: TodoItem[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const done = todo.filter((i) => i.status === "done").length;
  const allDone = todo.length > 0 && done === todo.length;
  // The plan finished: fold the card back to the quiet line on its own.
  useEffect(() => {
    if (allDone) setOpen(false);
  }, [allDone]);
  if (!todo.length) return null;
  const current =
    todo.find((i) => i.status === "in_progress") || todo.find((i) => i.status !== "done");
  const pct = Math.round((done / todo.length) * 100);
  return (
    <div className="plandock max-w-3xl mx-auto" data-testid="plan-dock">
      <button
        className="plandock-head"
        data-testid="plan-dock-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="plandock-tag">{t("plandock.tag")}</span>
        {!open && (
          <span className="plandock-now">
            {allDone ? t("plandock.all_done") : current?.content || ""}
          </span>
        )}
        <span className="plandock-count">{t("plandock.count", { done, total: todo.length })}</span>
        <span className="plandock-meter" aria-hidden>
          <i style={{ width: `${pct}%` }} />
        </span>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={13} className="plandock-chev" />
      </button>
      {open && (
        <div className="plandock-body" data-testid="plan-dock-steps">
          {todo.map((item, index) => (
            <div className={"plandock-step " + item.status} key={index}>
              <span className="plandock-mark" />
              <span className="plandock-text">{item.content}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
