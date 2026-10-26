import { useTranslation } from "react-i18next";
import type { Item } from "../types";
import type { ProposalTask } from "../proposals";
import { Icon } from "./Icon";
import { ExternalActionsSection, ProposalActions } from "./ProposalParts";

export function stripDoneWhen(criteria: string | undefined): string {
  return (criteria || "").replace(
    /^(\s*(done\s+when|acceptance\s+criteria)\s*:\s*)+/i,
    "",
  );
}

export function WorkItemsCard({
  item,
  onRespond,
}: {
  item: Extract<Item, { kind: "itemsreq" }>;
  onRespond: (approved: boolean, feedback?: string) => void;
}) {
  const { t } = useTranslation();
  const titles = new Map(item.items.map((entry) => [entry.key, entry.title]));
  const final = item.items.find(
    (entry) => entry.key && entry.key === item.final_acceptance?.item_key,
  );
  const task = (entry: ProposalTask, i: number) => (
    <div className="proposal-task" key={entry.key || i}>
      <div className="proposal-task-title">{entry.title}</div>
      <span className="proposal-label">
        {t("proposal.acceptance_criteria")}
      </span>
      <p className="proposal-criteria">{stripDoneWhen(entry.criteria)}</p>
      {entry.description && (
        <p className="proposal-muted">{entry.description}</p>
      )}
      {!!entry.depends_on?.length && (
        <p className="proposal-muted">
          {t("proposal.depends_on")}:{" "}
          {entry.depends_on.map((key) => titles.get(key)).join(" · ")}
        </p>
      )}
      {!!entry.verifies?.length && (
        <p className="proposal-muted">
          {t("proposal.verifies")}:{" "}
          {entry.verifies.map((key) => titles.get(key)).join(" · ")}
        </p>
      )}
    </div>
  );
  return (
    <div
      className="dirreq-card itemsreq-card proposal-card"
      data-testid="itemsreq-card"
    >
      <div className="proposal-content">
        <header className="proposal-heading">
          <span className="proposal-mark">
            <Icon name="workList" size={20} />
          </span>
          <div>
            <span className="proposal-label">
              {t("proposal.proposed_work")}
            </span>
            <h3>
              {item.title ||
                t("team.proposed_items", { count: item.items.length })}
            </h3>
          </div>
        </header>
        {item.summary && <p className="proposal-summary">{item.summary}</p>}
        <div className="proposal-meta">
          <span>{t("proposal.tasks", { count: item.items.length })}</span>
          {!!item.activities?.length && <span>({item.activities.map((activity) =>
            `${item.items.filter((task) => task.activity === activity.id).length} ${activity.title}`,
          ).join(" · ")})</span>}
        </div>
        <div className="proposal-workstreams">
          {item.workstreams?.map((group) => {
            const tasks = item.items.filter(
              (entry) => entry.workstream === group.id,
            );
            return (
              <details className="proposal-group" key={group.id}>
                <summary>
                  <Icon name="chevronRight" size={14} />
                  <span className="proposal-group-title">
                    {group.title}
                    <small>{group.summary}</small>
                  </span>
                  <span className="proposal-muted">
                    {t("proposal.tasks", { count: tasks.length })}
                  </span>
                </summary>
                <div className="proposal-group-content">{tasks.map(task)}</div>
              </details>
            );
          })}
        </div>
        {!item.workstreams?.length && item.items.map(task)}
        {final && (
          <div className="proposal-final">
            <span className="proposal-label">
              {t("proposal.final_acceptance")}
            </span>
            <p>{final.title}</p>
            <p className="proposal-muted">
              {t(
                item.final_acceptance?.owner === "lead"
                  ? "proposal.owner_lead"
                  : "proposal.owner_assigned_worker",
              )}
            </p>
          </div>
        )}
        <ExternalActionsSection
          declaration={item.external_actions}
          targets={item.targets}
        />
      </div>
      <ProposalActions
        approveLabel={t("proposal.approve_plan")}
        testId="itemsreq-approve"
        onApprove={() => onRespond(true)}
        onReject={(feedback) => onRespond(false, feedback)}
      />
    </div>
  );
}
