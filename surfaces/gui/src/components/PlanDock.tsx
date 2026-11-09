import { useEffect, useRef, useState } from "react";
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
  // A finished plan stays docked as the receipt ("Last plan — done") until the user
  // clears it with the ✕ or a new plan supersedes it (owner ruling 2026-11-07).
  // Dismissal is keyed to the plan's CONTENT, so the next todo_write — even one
  // replacing a half-finished plan — brings the dock back for the new plan.
  const [dismissed, setDismissed] = useState(false);
  const signature = todo.map((i) => i.content).join("\u0000");
  const lastSignature = useRef(signature);
  useEffect(() => {
    if (signature !== lastSignature.current) {
      lastSignature.current = signature;
      setDismissed(false);
    }
  }, [signature]);
  const done = todo.filter((i) => i.status === "done").length;
  const allDone = todo.length > 0 && done === todo.length;
  // The plan finished: fold the card back to the quiet line on its own.
  useEffect(() => {
    if (allDone) setOpen(false);
  }, [allDone]);
  if (!todo.length || dismissed) return null;
  const current =
    todo.find((i) => i.status === "in_progress") || todo.find((i) => i.status !== "done");
  const pct = Math.round((done / todo.length) * 100);
  return (
    <div className={"plandock max-w-3xl mx-auto" + (allDone ? " done" : "")} data-testid="plan-dock">
      <button
        className="plandock-head"
        data-testid="plan-dock-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="plandock-tag">
          {allDone ? t("plandock.tag_finished") : t("plandock.tag")}
        </span>
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
      {allDone && (
        <button
          className="plandock-x"
          data-testid="plan-dock-clear"
          title={t("plandock.clear")}
          aria-label={t("plandock.clear")}
          onClick={() => setDismissed(true)}
        >
          <Icon name="x" size={12} />
        </button>
      )}
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
