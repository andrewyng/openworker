// UX-060: the trace index — one row per message of yours, the way into the trace. The same
// list sits in the rail's Trace section (collapsed rail) and as the open panel's left column,
// so the index never moves or disappears; the panel only adds a detail pane beside it.
import { useTranslation } from "react-i18next";
import { formatDuration, type TraceRow } from "../obs3";

export function TraceIndex({
  rows,
  current,
  onPick,
  testId = "trace-index",
}: {
  rows: TraceRow[];
  // The turn the panel shows, when it is open.
  current?: string | null;
  onPick: (row: TraceRow) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="trace-index" data-testid={testId}>
      {rows.map((r) => {
        const isCurrent = !!current && r.turnId === current;
        const glance = r.working
          ? t("rail.sum_working")
          : r.durationMs != null
            ? formatDuration(r.durationMs)
            : r.turnId
              ? ""
              : t("rail.trace_no_trace");
        return (
          <button
            key={r.mid || r.n}
            className={"trace-row" + (isCurrent ? " current" : "") + (!r.turnId && !r.working ? " none" : "")}
            data-testid={`trace-row-${r.n}`}
            aria-current={isCurrent || undefined}
            title={t("rail.trace_row_title")}
            onClick={() => onPick(r)}
          >
            <span className="trace-n">{r.n}</span>
            <span className="trace-words">{r.words}</span>
            {r.issues > 0 && <span className="trace-dot" aria-label={t("rail.trace_issues", { count: r.issues })} />}
            <span className={"trace-glance" + (r.working ? " live" : "")}>{glance}</span>
          </button>
        );
      })}
    </div>
  );
}
