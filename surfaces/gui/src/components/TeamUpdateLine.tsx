import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { BoardWakeRow, MessageSource } from "../api";
import type { Item } from "../types";
import { TaskChip } from "./TaskChip";
import { Icon } from "./Icon";

const name = (actor?: string) => (actor || "").split(":")[0];
const stateLabel = (state: string, t: TFunction) => {
  const group = ({ open: "queued", in_progress: "working", review: "review", done: "done", canceled: "canceled" } as Record<string, string>)[state];
  return group ? t("teamview.state_" + group) : t("board.state_" + state, { defaultValue: state });
};
export function summarizeUpdates(rows: BoardWakeRow[], t: TFunction): string {
  if (!rows.length) return t("teamview.checked");
  const groups = new Map<string, string[]>();
  for (const row of rows) {
    const kind = row.kind === "moved" ? row.to || "changed" : row.kind;
    const labels = groups.get(kind) || [];
    const label =
      name(row.assignee || row.actor) ||
      row.title ||
      (row.item ? String(row.item) : "");
    if (label && !labels.includes(label)) labels.push(label);
    groups.set(kind, labels);
  }
  return (
    [...groups]
      .map(([kind, labels]) => {
        const key = "teamview.update_" + kind;
        const label = t(key, { defaultValue: t("teamview.update_changed") });
        return [
          labels.slice(0, 3).join(", "),
          label,
          labels.length > 3
            ? t("teamview.more", { count: labels.length - 3 })
            : "",
        ]
          .filter(Boolean)
          .join(" · ");
      })
      .slice(0, 3)
      .join(" · ") +
    (groups.size > 3
      ? " · " + t("teamview.more", { count: groups.size - 3 })
      : "")
  );
}

// Shared by replay and the live view. Keep original tools inside the disclosure,
// not discarded or moved across a human message/approval/notice.
export function foldTeamUpdates(
  items: Item[],
): { item: Item; sources?: MessageSource[]; steps?: Item[] }[] {
  const out: { item: Item; sources?: MessageSource[]; steps?: Item[] }[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind !== "connector" || item.source.connector !== "board") {
      out.push({ item });
      continue;
    }
    const sources = [item.source],
      steps: Item[] = [];
    let end = i;
    for (let j = i + 1; j < items.length; j++) {
      const next = items[j];
      if (
        next.kind === "connector" &&
        next.source.connector === "board" &&
        next.source.channel_id === item.source.channel_id
      ) {
        sources.push(next.source);
        end = j;
        continue;
      }
      if (next.kind === "tool") {
        steps.push(next);
        end = j;
        continue;
      }
      if (next.kind === "assistant") {
        // Narration preceding a tool belongs to the background check. A terminal
        // answer, decision, human message, or pending approval remains visible.
        const following = items[j + 1];
        const verdict = following?.kind === "tool" && following.name === "transition"
          && ["review", "done", "canceled"].includes(String(following.args.to));
        if ((!next.text.trim() && !next.reasoning) || (following?.kind === "tool" && !verdict)) {
          steps.push(next);
          end = j;
          continue;
        }
      }
      break;
    }
    out.push({ item, sources, steps });
    i = end;
  }
  return out;
}

export function TeamUpdateLine({
  sources,
  children,
  defaultOpen = false,
}: {
  sources: MessageSource[];
  children?: ReactNode;
  defaultOpen?: boolean;
}) {
  const { t } = useTranslation();
  const rows = sources.flatMap((s) => s.board?.rows || []);
  const groups = new Map<string, BoardWakeRow[]>();
  for (const row of rows) {
    const key = row.item != null ? String(row.item) : row.kind;
    groups.set(key, [...(groups.get(key) || []), row]);
  }
  const ts = sources[sources.length - 1]?.ts;
  return (
    <details
      className="team-update"
      open={defaultOpen || undefined}
      data-testid="team-update"
    >
      <summary className="team-update-head transcript-disclosure">
        <Icon name="chevronDown" size={12} />
        <span>
          {rows.length || sources.length > 1
            ? t(sources.length > 1 ? "teamview.updates" : "teamview.update", {
                count: sources.length,
              }) + " · "
            : ""}
          {summarizeUpdates(rows, t)}
        </span>
        {ts > 0 && (
          <time dateTime={new Date(ts * 1000).toISOString()}>
            {new Date(ts * 1000).toLocaleTimeString([], {
              hour: "numeric",
              minute: "2-digit",
            })}
          </time>
        )}
      </summary>
      <div className="team-update-body">
        {sources.filter(s => s.board?.check_in).map((s, i) => <p key={`check-in-${i}`}>{s.text}</p>)}
        {[...groups].map(([key, group]) => {
          const last = group[group.length - 1],
            moves = group.filter((r) => r.kind === "moved");
          const from = moves[0]?.from,
            to = moves[moves.length - 1]?.to;
          const label =
            last.title ||
            t(last.kind === "chat" ? "teamview.chat" : "teamview.task", {
              id: last.item ?? "",
            });
          return (
            <div
              key={key}
              className={
                group.some((r) => r.kind === "waiting") ? "text-warnInk" : ""
              }
            >
              {last.item != null ? (
                <TaskChip id={String(last.item)} space={sources[0]?.channel_id}>
                  {label}
                </TaskChip>
              ) : (
                label
              )}
              {" · "}
              {from
                ? stateLabel(from, t) + " → "
                : ""}
              {to
                ? stateLabel(to, t)
                : summarizeUpdates(group, t)}
              {to && " · " + name(last.assignee || last.actor)}
              {[...new Set(group.flatMap((r) => r.refs || []))]
                .filter((ref) => /^https?:\/\//.test(ref))
                .map((ref) => (
                  <a key={ref} href={ref} target="_blank" rel="noreferrer">
                    {" "}
                    · {t("teamview.evidence")}
                  </a>
                ))}
            </div>
          );
        })}
        {children}
      </div>
    </details>
  );
}

export function TeamCreatedLine({
  workers,
}: {
  workers: { actor: string; persona: string }[];
}) {
  const { t } = useTranslation();
  return (
    <details className="team-update" data-testid="team-created">
      <summary className="team-update-head transcript-disclosure">
        <Icon name="chevronDown" size={12} />
        <span>{t("teamview.created", {
          count: workers.length,
          roles: new Set(workers.map((w) => w.persona)).size,
        })}</span>
      </summary>
      <div className="team-update-body">
        {workers.map((w) => (
          <div key={w.actor}>
            {w.actor} · {w.persona}
          </div>
        ))}
      </div>
    </details>
  );
}
