import { useTranslation } from "react-i18next";

export type Escalation = {
  // reviewer_asks: a decision-model reviewer (OPE-224) wants a human decision; it gives no reason.
  kind: "reviewer_unsure" | "human_required" | "reviewer_unavailable" | "reviewer_asks";
  reason: string;
};

export function ApprovalEscalation({ escalation, reviewerUnsure }: { escalation?: Escalation | null; reviewerUnsure?: string }) {
  const { t } = useTranslation();
  const detail = escalation || (reviewerUnsure ? { kind: "reviewer_unsure", reason: reviewerUnsure } : null);
  if (!detail) return null;
  return <div className="text-meta text-muted mt-1" data-testid="approval-escalation">
    {t(`approval.escalation_${detail.kind}`)}{detail.reason ? ` ${detail.reason}` : ""}
  </div>;
}
