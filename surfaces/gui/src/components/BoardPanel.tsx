// Agent teams (OPE-96 → detail-view rework, owner-approved mock 2026-08-17):
//  - BoardSection: the right-rail summary (grouped by state, blocked on top)
//  - BoardOverlay: the expanded view — a QUIET LIST grouped by the store's raw
//    states (In progress / Awaiting review / Queued; owner ruling: no computed
//    interpretation layer, no row buttons, no badges) + a Linear-style detail
//    pane with the item's TIMELINE (events + comments merged — the store is an
//    event log; the pane is its honest projection). Actions live in the pane
//    only: Mark done / Request changes… (review), Remove (queued), Reopen.
// Both render the same Board data App owns; mutations go through the /board
// endpoints and act as the USER.
import { useEffect, useState } from "react";
import type { Board, BoardItem, BoardItemDetail, BoardTimelineEvent } from "../api";
import { Icon } from "./Icon";

// Rail display order: needs-attention first (mock UX-030: "blocked on top").
const RAIL_GROUPS: { state: string; label: string }[] = [
  { state: "blocked", label: "Blocked" },
  { state: "review", label: "Awaiting review" },
  { state: "in_progress", label: "In progress" },
  { state: "open", label: "Queued" },
  { state: "done", label: "Done" },
  { state: "canceled", label: "Canceled" },
];

function dotClass(state: string): string {
  if (state === "blocked") return "board-dot blocked";
  if (state === "review") return "board-dot review";
  if (state === "in_progress") return "board-dot work";
  if (state === "done") return "board-dot done";
  return "board-dot idle";
}

export function boardSummary(board: Board): string {
  const counts: Record<string, number> = {};
  for (const item of board.items) counts[item.state] = (counts[item.state] || 0) + 1;
  const parts: string[] = [];
  if (counts.blocked) parts.push(`${counts.blocked} blocked`);
  if (counts.review) parts.push(`${counts.review} review`);
  if (counts.in_progress) parts.push(`${counts.in_progress} in progress`);
  if (counts.open) parts.push(`${counts.open} open`);
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
          No active work
        </div>
      )}
      {groups.map((group) => (
        <div key={group.state}>
          <div className="board-group">{group.label}</div>
          {group.items.map((item) => (
            <button
              className="board-row"
              key={item.id}
              onClick={() => (onOpenItem ? onOpenItem(item.id) : onExpand())}
              title="Open item"
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
          {showFinished ? "Hide finished" : `${finished} finished · show`}
        </button>
      )}
    </div>
  );
}

// Overlay list sections — the store's raw states, nothing computed (owner ruling
// 2026-08-17). Blocked rows live under In progress: still that worker's item,
// just stuck — the red dot + blocker fact carry the difference.
const LIST_SECTIONS: { label: string; states: string[] }[] = [
  { label: "In progress", states: ["in_progress", "blocked"] },
  { label: "Awaiting review", states: ["review"] },
  { label: "Queued", states: ["open"] },
];

export function BoardOverlay({
  board,
  onClose,
  onTransition,
  onComment,
  loadItem,
  loadAttachment,
  onOpenWorker,
  initialItem,
}: {
  board: Board;
  onClose: () => void;
  // (item, to, comment?) → performed as the user; App refetches on completion.
  onTransition?: (item: number, to: string, comment?: string) => void;
  // A pure note — never changes state; the assignee hears it through its feed.
  onComment?: (item: number, body: string) => Promise<unknown> | void;
  loadItem?: (id: number) => Promise<BoardItemDetail | { error: string }>;
  loadAttachment?: (stored: string) => Promise<string | null>;
  // Assignee link → jump into that coworker's session (closes the overlay).
  onOpenWorker?: (actor: string) => void;
  initialItem?: number | null;
}) {
  const [detail, setDetail] = useState<BoardItemDetail | null>(null);
  const [showFinished, setShowFinished] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const openItem = async (id: number) => {
    if (!loadItem) return;
    const loaded = await loadItem(id);
    if (!("error" in loaded)) setDetail(loaded);
  };
  useEffect(() => {
    if (initialItem != null) void openItem(initialItem);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialItem]);

  const move = async (item: number, to: string, comment?: string) => {
    onTransition?.(item, to, comment);
    // the pane refreshes on the next tick so the transition's board refetch lands first
    if (detail?.id === item) setTimeout(() => void openItem(item), 350);
  };
  const addNote = async (item: number, body: string) => {
    await onComment?.(item, body);
    await openItem(item);
  };

  const finished = board.items.filter(
    (i) => i.state === "done" || i.state === "canceled"
  );
  const sections = LIST_SECTIONS.map((s) => ({
    ...s,
    items: board.items.filter((i) => s.states.includes(i.state)),
  })).filter((s) => s.items.length > 0);

  const row = (item: BoardItem) => (
    <button
      className={"board-lrow" + (detail?.id === item.id ? " sel" : "")}
      key={item.id}
      data-testid={`board-item-${item.id}`}
      onClick={() => void openItem(item.id)}
    >
      <span className={dotClass(item.state)} />
      <span className="board-lrow-id">#{item.id}</span>
      <span className="board-lrow-title">{item.title}</span>
      <span className="board-lrow-end">
        {item.assignee}
        {item.state === "blocked" && (
          <> · blocked{item.blocker ? `: ${item.blocker}` : ""}</>
        )}
      </span>
    </button>
  );

  return (
    <div className="board-overlay" data-testid="board-overlay" onClick={onClose}>
      <div className="board-overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="board-overlay-head">
          <div className="board-overlay-title">
            <Icon name="table" size={16} />
            <span>Board</span>
            <span className="board-overlay-space">{board.name}</span>
          </div>
          <button className="artifact-icon-btn" onClick={onClose} aria-label="Close board" title="Close">
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="board-overlay-body">
          <div className="board-list">
            {sections.map((section) => (
              <div key={section.label}>
                <div className="board-lsec">{section.label}</div>
                {section.items.map(row)}
              </div>
            ))}
            {sections.length === 0 && (
              <div className="board-rail-quiet">No active work</div>
            )}
            {finished.length > 0 && (
              <>
                <button
                  className="board-finished-toggle"
                  data-testid="overlay-finished-toggle"
                  onClick={() => setShowFinished((v) => !v)}
                >
                  {showFinished
                    ? "Hide finished"
                    : `${finished.length} finished · show`}
                </button>
                {showFinished && (
                  <div>
                    <div className="board-lsec">Finished</div>
                    {finished.map(row)}
                  </div>
                )}
              </>
            )}
          </div>
          {detail && (
            <ItemDetail
              detail={detail}
              onTransition={move}
              onAddNote={onComment ? addNote : undefined}
              loadAttachment={loadAttachment}
              onOpenWorker={onOpenWorker}
            />
          )}
        </div>
      </div>
    </div>
  );
}

const STATE_LABEL: Record<string, string> = {
  open: "Queued",
  in_progress: "In progress",
  blocked: "Blocked",
  review: "In review",
  done: "Done",
  canceled: "Canceled",
};

function ItemDetail({
  detail,
  onTransition,
  onAddNote,
  loadAttachment,
  onOpenWorker,
}: {
  detail: BoardItemDetail;
  onTransition?: (item: number, to: string, comment?: string) => void;
  onAddNote?: (item: number, body: string) => Promise<void>;
  loadAttachment?: (stored: string) => Promise<string | null>;
  onOpenWorker?: (actor: string) => void;
}) {
  // "Request changes…" discloses a comment box; the verdict rides the transition.
  const [changesOpen, setChangesOpen] = useState(false);
  const [changesText, setChangesText] = useState("");
  useEffect(() => {
    setChangesOpen(false);
    setChangesText("");
  }, [detail.id]);
  return (
    <div className="board-detail" data-testid="board-detail">
      <div className="board-detail-title">
        <span className="board-detail-id">#{detail.id}</span> {detail.title}
      </div>
      <div className="board-detail-meta">
        <span className={"board-detail-st st-" + detail.state}>
          {STATE_LABEL[detail.state] || detail.state}
        </span>
        {detail.assignee && (
          <>
            {" · "}
            {onOpenWorker ? (
              <button
                className="board-detail-worker"
                data-testid="board-open-worker"
                onClick={() => onOpenWorker(detail.assignee)}
                title="Open this coworker's session"
              >
                {detail.assignee} ↗
              </button>
            ) : (
              detail.assignee
            )}
          </>
        )}
        {" · filed by "}
        {detail.creator}
      </div>
      {detail.description && (
        <div className="board-detail-desc">{detail.description}</div>
      )}
      {detail.criteria && (
        <div className="board-detail-crit">
          <span className="board-detail-label">Done when</span> — {detail.criteria}
        </div>
      )}
      <div className="board-tl">
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
  const [text, setText] = useState("");
  useEffect(() => setText(""), [detail.id]);
  const submit = async () => {
    const body = text.trim();
    if (!body) return;
    setText("");
    await onAddNote(detail.id, body);
  };
  return (
    <input
      className="board-note-input"
      data-testid="board-note-input"
      placeholder="Add a note…"
      title="Leaves a note on the item — never changes its state"
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
  if (detail.state === "review") {
    return (
      <div className="board-detail-actions">
        {changesOpen ? (
          <div className="board-changes" data-testid="board-changes">
            <textarea
              autoFocus
              placeholder="What needs to change?"
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
                Request changes
              </button>
              <button className="board-btn ghost" onClick={() => setChangesOpen(false)}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <button
              className="board-btn primary"
              onClick={() => onTransition(detail.id, "done")}
            >
              Mark done
            </button>
            <button className="board-btn ghost" onClick={() => setChangesOpen(true)}>
              Request changes…
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
          Reopen
        </button>
      </div>
    );
  }
  if (detail.state === "done") return null;
  return (
    <div className="board-detail-actions">
      <button className="board-btn ghost" onClick={() => onTransition(detail.id, "canceled")}>
        Remove
      </button>
    </div>
  );
}

function timelineLine(event: BoardTimelineEvent): string {
  switch (event.kind) {
    case "created":
      return "filed this";
    case "assigned":
      return `assigned to ${event.assignee}`;
    case "claimed":
      return "claimed this";
    case "moved":
      return event.to === "in_progress" ? "started" : `moved to ${(STATE_LABEL[event.to || ""] || event.to || "").toLowerCase()}`;
    case "comment":
      return "commented";
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
        <b>{event.actor}</b> {timelineLine(event)} · {when}
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
  useEffect(() => {
    let created: string | null = null;
    void loadAttachment(stored).then((u) => {
      created = u;
      setUrl(u);
    });
    return () => {
      if (created) URL.revokeObjectURL(created);
    };
  }, [stored, loadAttachment]);
  if (!url) return null;
  return (
    <a className="board-shot" href={url} target="_blank" rel="noreferrer" title={name}>
      <img src={url} alt={name} data-testid="board-attachment" />
      <span className="board-shot-cap">{name}</span>
    </a>
  );
}
