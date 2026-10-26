// A lead's decision on one of its workers' waiting tool calls (`decide_worker_call`,
// agent-teams spec §11.6), when that decision needs the human. The generic approval card
// made the human click "Allow" to DENY a command it never showed (owner catch
// 2026-09-17). This card shows both halves — what the worker wants to run and what the
// lead decided, with its reason — and the buttons say what will happen to the WORKER'S
// call: follow the lead, or do the opposite.
//
// `workerCall` is attached by the server (it looks the waiting call up by `call_id`).
// An older server sends none: the card still explains the decision and says the call
// itself cannot be shown.
import type { ReactNode } from "react";
import { ApprovalEscalation, type Escalation } from "./ApprovalEscalation";
import { getI18n, Trans, useTranslation } from "react-i18next";
import { humanizeApprovalTitle } from "../humanize";
import { PreviewBlock, TitleText } from "./ApprovalCard";
import { Icon } from "./Icon";

export interface WorkerCall {
  item_id?: number;
  item_title?: string;
  worker?: string;
  tool: string;
  arguments?: any;
  reason?: string;
  // "pending" while the worker is still waiting; "resolved" when someone already answered.
  state?: string;
  resolution?: string | null;
}

export interface LeadDecision {
  worker: string;
  callId: string;
  decision: "allow" | "deny";
  note: string;
}

export function leadDecisionFromArgs(args: any): LeadDecision {
  const a = args && typeof args === "object" ? args : {};
  return {
    worker: String(a.worker ?? "").trim() || getI18n().t("workerdec.a_worker"),
    callId: String(a.call_id ?? ""),
    decision: String(a.decision ?? "").trim().toLowerCase() === "allow" ? "allow" : "deny",
    note: String(a.note ?? "").trim(),
  };
}

// "command" / "file change" / "action": the noun the card uses for the worker's call.
// Returned as a KEY SUFFIX: every sentence that names the call has one key per noun
// (`workerdec.title_deny_command`, …) so no language has to splice a noun into a sentence.
type Noun = "command" | "file_change" | "action";
function nounFor(tool?: string): Noun {
  if (tool === "run_shell") return "command";
  if (tool && /^(write_file|replace_in_file|apply_patch|apply_unified_diff)$/.test(tool)) return "file_change";
  return "action";
}

// Older/current servers include an argument preview in the parked prompt body.
// For shell calls the title + command block already show those same two fields.
// Remove only that exact generated line, retaining distinct policy explanations.
function distinctWorkerReason(call?: WorkerCall | null): string {
  const reason = call?.reason || "";
  const args = call?.arguments || {};
  const shorten = (text: string, limit: number) => {
    const chars = Array.from(text);
    return chars.length > limit ? chars.slice(0, limit - 1).join("") + "…" : text;
  };
  const summary = call?.tool === "run_shell" && typeof args.command === "string"
    && Object.keys(args).every(k => k === "command" || k === "description")
    ? shorten(Object.entries(args).map(([key, value]) =>
      `${key}: ${shorten(String(value).trim().replace(/\s+/g, " "), 80)}`,
    ).join(" · "), 240) : "";
  return reason.split("\n").filter(line => line.trim() !== "requires approval"
    && (!summary || line.trim() !== summary)).join("\n").trim();
}

export function WorkerDecisionCard({
  decision,
  workerCall,
  escalation,
  reviewerUnsure,
  onFollow,
  onOverride,
  compact = false,
  bare = false,
  chip,
}: {
  decision: LeadDecision;
  workerCall?: WorkerCall | null;
  escalation?: Escalation;
  reviewerUnsure?: string;
  // Do what the lead decided / do the opposite. Both answer the worker's call.
  onFollow: () => void;
  onOverride: () => void;
  compact?: boolean;
  // Inside an Inbox item the item's own frame is the box.
  bare?: boolean;
  chip?: ReactNode;
}) {
  const { t } = useTranslation();
  const { worker, note } = decision;
  const deny = decision.decision === "deny";
  const noun = nounFor(workerCall?.tool);
  const answered = workerCall?.state === "resolved";
  const args = workerCall?.arguments && typeof workerCall.arguments === "object" ? workerCall.arguments : {};
  const preview =
    typeof args.command === "string" ? args.command : typeof args.content === "string" ? args.content : "";
  const workerReason = distinctWorkerReason(workerCall) === escalation?.reason ? "" : distinctWorkerReason(workerCall);
  return (
    <div
      className={bare ? "workerdec bare" : "approval workerdec" + (compact ? " approval-dock" : "")}
      data-testid="workerdec-card"
    >
      <div className="approval-top">
        <div className="approval-heading">
          <span className="approval-ico" title={t("approval.tool_title", { name: "decide_worker_call" })}>
            <Icon name="diamond" size={15} />
          </span>
          <span className="workerdec-title">
            <Trans
              i18nKey={`workerdec.title_${deny ? "deny" : "allow"}_${noun}`}
              values={{ worker }}
              components={{ b: <b /> }}
            />
          </span>
        </div>
        <span className="approval-scope">{t("workerdec.scope", { worker })}</span>
      </div>
      {workerCall?.item_title && <p className="team-totals">{workerCall.item_title}</p>}
      <ApprovalEscalation escalation={escalation} reviewerUnsure={reviewerUnsure} />

      <div className="workerdec-label">{t("workerdec.wants_to", { worker })}</div>
      {workerCall ? (
        <div className="workerdec-call" data-testid="workerdec-call">
          <TitleText line={humanizeApprovalTitle(workerCall.tool, args)} />
          {preview && <PreviewBlock text={preview} />}
          {workerReason && (
            <div className="approval-reason">{workerReason}</div>
          )}
        </div>
      ) : (
        <div className="workerdec-missing" data-testid="workerdec-missing">
          {t(`workerdec.missing_${noun}`, { worker })}
        </div>
      )}

      <div className="workerdec-label">{t("workerdec.lead_reason")}</div>
      <div className="workerdec-note" data-testid="workerdec-note">
        {note || t("workerdec.no_reason")}
      </div>
      {chip}

      {answered ? (
        <>
          <div className="workerdec-answered" data-testid="workerdec-answered">
            {t(`workerdec.answered_${workerCall?.resolution === "allow" ? "allowed" : "denied"}_${noun}`)}
          </div>
          <div className="approval-btns">
            <span className="spacer" />
            <button className="btn" data-testid="workerdec-follow" onClick={onFollow}>
              {t("workerdec.ok")}
            </button>
          </div>
        </>
      ) : (
        <div className="approval-btns">
          <button className="btn approval-primary" data-testid="workerdec-follow" onClick={onFollow}>
            {deny ? t("workerdec.follow_deny") : t("workerdec.follow_allow")}
          </button>
          <span className="spacer" />
          <button className="btn quiet-deny" data-testid="workerdec-override" onClick={onOverride}>
            {deny ? t(`workerdec.override_allow_${noun}`, { worker }) : t("workerdec.override_deny")}
          </button>
        </div>
      )}
    </div>
  );
}
