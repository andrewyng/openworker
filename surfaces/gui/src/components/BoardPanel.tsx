// Standalone board entry point and shared task evidence/verdicts. Team View replaces
// the old full-window overlay; mutations still use the user-authorized board API.
import { useEffect, useState } from "react";
import type { TFunction } from "i18next";
import { Trans, getI18n, useTranslation } from "react-i18next";
import type { Board, BoardItemDetail, BoardTimelineEvent } from "../api";

// Rail display order: needs-attention first (mock UX-030: "blocked on top").
const RAIL_GROUPS: { state: string; labelKey: string }[] = [
  { state: "blocked", labelKey: "board.state_blocked" },
  { state: "review", labelKey: "board.state_awaiting_review" },
  { state: "in_progress", labelKey: "board.state_in_progress" },
  { state: "open", labelKey: "board.state_queued" },
  { state: "done", labelKey: "board.state_done" },
  { state: "canceled", labelKey: "board.state_canceled" },
];

function dotClass(state: string): string {
  if (state === "blocked") return "board-dot blocked";
  if (state === "review") return "board-dot review";
  if (state === "in_progress") return "board-dot work";
  if (state === "done") return "board-dot done";
  return "board-dot idle";
}

export function boardSummary(board: Board): string {
  const t = getI18n().t;
  const counts: Record<string, number> = {};
  for (const item of board.items) counts[item.state] = (counts[item.state] || 0) + 1;
  const parts: string[] = [];
  if (counts.blocked) parts.push(t("board.summary_blocked", { count: counts.blocked }));
  if (counts.review) parts.push(t("board.summary_review", { count: counts.review }));
  if (counts.in_progress)
    parts.push(t("board.summary_in_progress", { count: counts.in_progress }));
  if (counts.open) parts.push(t("board.summary_open", { count: counts.open }));
  return parts.join(" · ");
}

export function BoardSection({
  board,
  onExpand,
  onOpenItem,
}: {
  board: Board;
  onExpand: () => void;
  // Row click deep-opens the overlay on that item's detail (falls back to expand).
  onOpenItem?: (id: number) => void;
}) {
  // The rail shows ACTIVE work only (owner ruling 2026-08-16): a project board
  // outlives its sessions, so finished history from a past effort would greet
  // every fresh session as a long stale list. Done/canceled sit behind a quiet
  // count; the expanded overlay keeps the full picture.
  const { t } = useTranslation();
  const [showFinished, setShowFinished] = useState(false);
  const finished = board.items.filter(
    (i) => i.state === "done" || i.state === "canceled"
  ).length;
  const shown = showFinished
    ? RAIL_GROUPS
    : RAIL_GROUPS.filter((g) => g.state !== "done" && g.state !== "canceled");
  const groups = shown
    .map((g) => ({
      ...g,
      items: board.items.filter((i) => i.state === g.state),
    }))
    .filter((g) => g.items.length > 0);
  return (
    <div className="board-rail" data-testid="board-rail">
      {groups.length === 0 && (
        <div className="board-rail-quiet" data-testid="board-rail-quiet">
          {t("board.no_active_work")}
        </div>
      )}
      {groups.map((group) => (
        <div key={group.state}>
          <div className="board-group">{t(group.labelKey)}</div>
          {group.items.map((item) => (
            <button
              className="board-row"
              key={item.id}
              onClick={() => (onOpenItem ? onOpenItem(item.id) : onExpand())}
              title={t("board.open_item")}
            >
              <span className={dotClass(item.state)} />
              <span className="board-row-main">
                <span className="board-row-title">
                  <span className="board-row-id">#{item.id}</span> {item.title}
                </span>
                {item.assignee && <span className="board-row-who">{item.assignee}</span>}
              </span>
            </button>
          ))}
        </div>
      ))}
      {finished > 0 && (
        <button
          className="board-finished-toggle"
          data-testid="board-finished-toggle"
          onClick={() => setShowFinished((v) => !v)}
        >
          {showFinished
            ? t("board.hide_finished")
            : t("board.finished_show", { count: finished })}
        </button>
      )}
    </div>
  );
}

const STATE_LABEL_KEYS: Record<string, string> = {
  open: "board.state_queued",
  in_progress: "board.state_in_progress",
  blocked: "board.state_blocked",
  review: "board.state_in_review",
  done: "board.state_done",
  canceled: "board.state_canceled",
};

function stateLabel(t: TFunction, state: string): string {
  return STATE_LABEL_KEYS[state] ? t(STATE_LABEL_KEYS[state]) : state;
}

export function ItemDetail({
  detail,
  onTransition,
  onAddNote,
  loadAttachment,
  onOpenWorker,
  hideTitle = false,
}: {
  detail: BoardItemDetail;
  onTransition?: (item: number, to: string, comment?: string) => void;
  onAddNote?: (item: number, body: string) => Promise<void>;
  loadAttachment?: (stored: string) => Promise<string | null>;
  onOpenWorker?: (actor: string) => void;
  hideTitle?: boolean;
}) {
  const { t } = useTranslation();
  // "Request changes…" discloses a comment box; the verdict rides the transition.
  const [changesOpen, setChangesOpen] = useState(false);
  const [changesText, setChangesText] = useState("");
  useEffect(() => {
    setChangesOpen(false);
    setChangesText("");
  }, [detail.id]);
  return (
    <div className="board-detail" data-testid="board-detail">
      {!hideTitle && <div className="board-detail-title">
        <span className="board-detail-id">#{detail.id}</span> {detail.title}
      </div>}
      <div className="board-detail-meta">
        <span className={"board-detail-st st-" + detail.state}>
          {stateLabel(t, detail.state)}
        </span>
        {detail.assignee && (
          <>
            {" · "}
            {onOpenWorker ? (
              <button
                className="board-detail-worker"
                data-testid="board-open-worker"
                onClick={() => onOpenWorker(detail.assignee)}
                title={t("board.open_worker_session")}
              >
                {detail.assignee} ↗
              </button>
            ) : (
              detail.assignee
            )}
          </>
        )}
        {" · "}
        {t("board.filed_by", { creator: detail.creator })}
      </div>
      {detail.description && (
        <div className="board-detail-desc">{detail.description}</div>
      )}
      {detail.criteria && (
        <div className="board-detail-crit">
          <Trans
            i18nKey="board.done_when_line"
            values={{ criteria: detail.criteria }}
            components={{ label: <span className="board-detail-label" /> }}
          />
        </div>
      )}
      <div className="board-tl">
        {detail.refs.filter(ref => /^https?:\/\//.test(ref)).map(ref => <p key={ref}><a href={ref} target="_blank" rel="noreferrer">{ref}</a></p>)}
        {(detail.timeline || []).map((event) => (
          <TimelineRow key={event.seq} event={event} loadAttachment={loadAttachment} />
        ))}
      </div>
      {onAddNote && <NoteComposer detail={detail} onAddNote={onAddNote} />}
      {onTransition && (
        <DetailActions
          detail={detail}
          onTransition={onTransition}
          changesOpen={changesOpen}
          setChangesOpen={setChangesOpen}
          changesText={changesText}
          setChangesText={setChangesText}
        />
      )}
    </div>
  );
}

// A pure note — an append to the item's story that NEVER changes state (owner
// doctrine 2026-08-17). The assignee hears it through its feed, so this is the
// lightweight way to talk to a worker through the board.
function NoteComposer({
  detail,
  onAddNote,
}: {
  detail: BoardItemDetail;
  onAddNote: (item: number, body: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  useEffect(() => setText(""), [detail.id]);
  const submit = async () => {
    const body = text.trim();
    if (!body) return;
    try { await onAddNote(detail.id, body); setText(""); } catch { /* Keep the draft; the parent displays the failure. */ }
  };
  return (
    <input
      className="board-note-input"
      data-testid="board-note-input"
      placeholder={t("board.add_note_placeholder")}
      title={t("board.add_note_title")}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") void submit();
      }}
    />
  );
}

function DetailActions({
  detail,
  onTransition,
  changesOpen,
  setChangesOpen,
  changesText,
  setChangesText,
}: {
  detail: BoardItemDetail;
  onTransition: (item: number, to: string, comment?: string) => void;
  changesOpen: boolean;
  setChangesOpen: (v: boolean) => void;
  changesText: string;
  setChangesText: (v: string) => void;
}) {
  const { t } = useTranslation();
  if (detail.state === "review") {
    return (
      <div className="board-detail-actions">
        {changesOpen ? (
          <div className="board-changes" data-testid="board-changes">
            <textarea
              autoFocus
              placeholder={t("board.changes_placeholder")}
              value={changesText}
              onChange={(e) => setChangesText(e.target.value)}
            />
            <div className="board-changes-row">
              {/* A board write, not a message: review → in_progress with the
                  comment attached; delivery to the assignee is the queue's job. */}
              <button
                className="board-btn primary"
                disabled={!changesText.trim()}
                onClick={() =>
                  onTransition(detail.id, "in_progress", changesText.trim())
                }
              >
                {t("plan.request_changes")}
              </button>
              <button className="board-btn ghost" onClick={() => setChangesOpen(false)}>
                {t("board.cancel")}
              </button>
            </div>
          </div>
        ) : (
          <>
            <button
              className="board-btn primary"
              onClick={() => onTransition(detail.id, "done")}
            >
              {t("board.mark_done")}
            </button>
            <button className="board-btn ghost" onClick={() => setChangesOpen(true)}>
              {t("board.request_changes_ellipsis")}
            </button>
          </>
        )}
      </div>
    );
  }
  if (detail.state === "canceled") {
    return (
      <div className="board-detail-actions">
        <button className="board-btn ghost" onClick={() => onTransition(detail.id, "open")}>
          {t("board.reopen")}
        </button>
      </div>
    );
  }
  if (detail.state === "done") return null;
  return (
    <div className="board-detail-actions">
      <button className="board-btn ghost" onClick={() => onTransition(detail.id, "canceled")}>
        {t("common.remove")}
      </button>
    </div>
  );
}

function timelineLine(t: TFunction, event: BoardTimelineEvent): string {
  switch (event.kind) {
    case "created":
      return t("board.tl_filed");
    case "assigned":
      return t("board.tl_assigned", { assignee: event.assignee });
    case "claimed":
      return t("board.tl_claimed");
    case "moved":
      return event.to === "in_progress"
        ? t("board.tl_started")
        : t("board.tl_moved", { state: stateLabel(t, event.to || "").toLowerCase() });
    case "comment":
      return t("board.tl_commented");
    default:
      return event.kind;
  }
}

function TimelineRow({
  event,
  loadAttachment,
}: {
  event: BoardTimelineEvent;
  loadAttachment?: (stored: string) => Promise<string | null>;
}) {
  const { t } = useTranslation();
  const shots = (event.refs || []).filter((r) => r.startsWith("attachment://"));
  const when = new Date(event.ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const tone =
    event.kind === "moved" && event.to === "review"
      ? " review"
      : event.kind === "moved" && event.to === "blocked"
        ? " blocked"
        : event.kind === "moved" && event.to === "in_progress"
          ? " work"
          : "";
  return (
    <div className={"board-tl-ev" + tone}>
      <div className="board-tl-line">
        <b>{event.actor}</b> {timelineLine(t, event)} · {when}
      </div>
      {event.body && <p className="board-tl-body">{event.body}</p>}
      {loadAttachment &&
        shots.map((ref) => (
          <AttachmentThumb key={ref} refString={ref} loadAttachment={loadAttachment} />
        ))}
    </div>
  );
}

function AttachmentThumb({
  refString,
  loadAttachment,
}: {
  refString: string;
  loadAttachment: (stored: string) => Promise<string | null>;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const stored = refString.slice("attachment://".length).split("#")[0];
  const name = refString.includes("#") ? refString.split("#").pop()! : stored;
  const isImage = /\.(png|jpe?g|gif|webp)$/i.test(stored);
  useEffect(() => {
    let created: string | null = null;
    let disposed = false;
    void loadAttachment(stored).then((u) => {
      if (disposed) {
        if (u) URL.revokeObjectURL(u);
        return;
      }
      created = u;
      setUrl(u);
    });
    return () => {
      disposed = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [stored, loadAttachment]);
  if (!url) return null;
  if (!isImage) return (
    <a href={url} download={name} className="text-ui text-accent underline underline-offset-2" data-testid="board-file-attachment">
      {name}
    </a>
  );
  return (
    <a className="board-shot" href={url} target="_blank" rel="noreferrer" title={name}>
      <img src={url} alt={name} data-testid="board-attachment" />
      <span className="board-shot-cap">{name}</span>
    </a>
  );
}
