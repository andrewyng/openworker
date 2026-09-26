import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ExternalActions } from "../proposals";
import { Icon } from "./Icon";

export function ProposalActions({
  approveLabel,
  testId,
  onApprove,
  onReject,
  disabled = false,
}: {
  approveLabel: string;
  testId: string;
  onApprove: () => void;
  onReject: (feedback?: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  return (
    <>
      {editing && (
        <form
          className="proposal-feedback"
          onSubmit={(e) => {
            e.preventDefault();
            if (feedback.trim()) onReject(feedback.trim());
          }}
        >
          <label>
            {t("proposal.what_changes")}
            <textarea
              autoFocus
              required
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
            />
          </label>
          <button className="btn" type="submit" disabled={!feedback.trim()}>
            {t("proposal.send_feedback")}
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => setEditing(false)}
          >
            {t("proposal.cancel")}
          </button>
        </form>
      )}
      <div className="proposal-actions">
        <button
          className="btn primary"
          disabled={disabled}
          data-testid={testId}
          onClick={onApprove}
        >
          {approveLabel}
          <Icon name="arrowRight" size={14} />
        </button>
        <button
          className="btn proposal-change"
          aria-expanded={editing}
          onClick={() => setEditing(!editing)}
        >
          {t("proposal.request_changes")}
        </button>
      </div>
    </>
  );
}

export function ExternalActionsSection({
  declaration,
  targets,
}: {
  declaration?: ExternalActions;
  targets?: string[];
}) {
  const { t } = useTranslation();
  return (
    <div className="proposal-boundaries">
      {!!targets?.length && (
        <div>
          <span className="proposal-label">{t("proposal.targets")}</span>
          <ul>
            {targets.map((target) => (
              <li key={target}>{target}</li>
            ))}
          </ul>
        </div>
      )}
      {declaration && (
        <div data-testid="proposal-external-actions">
          <span className="proposal-label">
            {t("proposal.external_actions")}
          </span>
          <p>{t(`proposal.external_${declaration.status}`)}</p>
          {!!declaration.actions.length && (
            <ul>
              {declaration.actions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ul>
          )}
          <p className="proposal-muted">{declaration.explanation}</p>
          {!!declaration.exclusions.length && (
            <>
              <span className="proposal-label">
                {t("proposal.not_planned")}
              </span>
              <ul>
                {declaration.exclusions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </>
          )}
          <p className="proposal-muted proposal-permission-note">
            {t("proposal.intent_not_permission")}
          </p>
        </div>
      )}
    </div>
  );
}
