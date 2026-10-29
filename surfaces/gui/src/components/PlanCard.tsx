import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Item, PlanStepView } from "../types";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

type PlanItem = Extract<Item, { kind: "planreq" }>;

// The agent proposed a plan via propose_plan. Mirrors the directory-request card, shown in
// the composer head.
// - Plan mode: the user approves it, choosing whether execution should keep asking per
//   action or run with full access, or sends it back with feedback.
// - Auto-approve (OPE-237): the plan lists its steps, risky ones marked. The user approves
//   all of it or sends it back; the mode does not change. Approved steps run without a card
//   for this request only.
export function PlanCard({
  item,
  onRespond,
}: {
  item: PlanItem;
  onRespond: (approved: boolean, mode?: string, feedback?: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const { t } = useTranslation();
  const steps = item.autoApprove ? item.steps : undefined;

  return (
    <div className="dirreq-card plan-card">
      <div className="dirreq-head">
        <Icon name="sparkle" size={16} className="ico" />
        <span>{t("plan.proposed")}</span>
      </div>
      <div className="plan-body">
        <Markdown text={item.plan} />
        {steps && steps.length > 0 && (
          <>
            <ol className="mt-2 flex flex-col gap-1" data-testid="plan-steps">
              {steps.map((step, i) => (
                <PlanStepRow step={step} key={i} />
              ))}
            </ol>
            <p className="mt-2 text-meta text-muted" data-testid="plan-steps-note">
              {t("plan.steps_note")}
            </p>
          </>
        )}
      </div>
      {rejecting ? (
        <div className="dirreq-actions">
          <input
            className="dirreq-path"
            placeholder={t("plan.feedback_placeholder")}
            value={feedback}
            autoFocus
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && feedback.trim()) onRespond(false, undefined, feedback.trim());
            }}
          />
          <button className="btn" onClick={() => setRejecting(false)}>
            {t("plan.back")}
          </button>
          <button
            className="btn primary"
            disabled={!feedback.trim()}
            onClick={() => onRespond(false, undefined, feedback.trim())}
          >
            {t("plan.send_feedback")}
          </button>
        </div>
      ) : steps ? (
        <div className="dirreq-actions">
          <button className="btn" onClick={() => setRejecting(true)}>
            {t("plan.request_changes")}
          </button>
          <span className="spacer" />
          <button className="btn primary" data-testid="plan-approve" onClick={() => onRespond(true, undefined)}>
            {t("plan.approve")}
          </button>
        </div>
      ) : (
        <div className="dirreq-actions">
          <button className="btn" onClick={() => setRejecting(true)}>
            {t("plan.request_changes")}
          </button>
          <span className="spacer" />
          <button className="btn" onClick={() => onRespond(true, "interactive")}>
            {t("plan.approve_ask")}
          </button>
          <button className="btn primary" onClick={() => onRespond(true, "auto")}>
            {t("plan.approve_and_run")}
          </button>
        </div>
      )}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  write: "plan.kind_write",
  delete: "plan.kind_delete",
  run: "plan.kind_run",
  send: "plan.kind_send",
};

function PlanStepRow({ step }: { step: PlanStepView }) {
  const { t } = useTranslation();
  return (
    <li
      className={
        "flex items-baseline gap-2 px-2 py-1 rounded-lg text-ui " +
        (step.risky ? "bg-warnSoft border border-lineStrong" : "border border-line")
      }
      data-testid="plan-step"
    >
      <span className="text-label text-muted shrink-0 w-14">
        {KIND_LABEL[step.kind] ? t(KIND_LABEL[step.kind]) : step.kind}
      </span>
      <span className="min-w-0 flex flex-col">
        <span className="font-mono text-meta text-ink break-all">
          {step.target}
          {step.destination && (
            <>
              <span className="text-muted font-sans"> {t("plan.to")} </span>
              {step.destination}
            </>
          )}
        </span>
        {step.note && (
          <span className="text-meta text-muted" data-testid="plan-step-note">
            {step.note}
          </span>
        )}
      </span>
      {step.risky && (
        <span className="ml-auto shrink-0 text-label px-1.5 rounded-full text-warnInk" data-testid="plan-step-risky">
          {t("plan.risky")}
        </span>
      )}
    </li>
  );
}
