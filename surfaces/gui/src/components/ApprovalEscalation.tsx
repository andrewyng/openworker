import { useTranslation } from "react-i18next";

export type Escalation = { kind: "reviewer_unsure" | "human_required" | "reviewer_unavailable"; reason: string };

export function ApprovalEscalation({ escalation, reviewerUnsure }: { escalation?: Escalation | null; reviewerUnsure?: string }) {
  const { t } = useTranslation();
  const detail = escalation || (reviewerUnsure ? { kind: "reviewer_unsure", reason: reviewerUnsure } : null);
  if (!detail) return null;
  return <div className="text-meta text-muted mt-1" data-testid="approval-escalation">
    {t(`approval.escalation_${detail.kind}`)}{detail.reason ? ` ${detail.reason}` : ""}
  </div>;
}
