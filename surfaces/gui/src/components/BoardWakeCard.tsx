// BoardWakeCard — a board wake in the lead's transcript, collapsed to ONE line by
// default (owner ruling 2026-08-16): most of the time the user just wants the
// feel that something is happening. Click to expand into per-event rows; long
// hand-off comments hide behind a per-row "show hand-off". NOT the connector
// card: a connector message is a foreign message, a board wake is a report —
// different shape, different affordances (they only share the visual family).
import { useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { BoardWakeRow, MessageSource } from "../api";
import { Icon } from "./Icon";

// Summary buckets → plural-aware catalog keys ("1 review", "2 reviews", …).
const WAKE_COUNT_KEYS: Record<string, string> = {
  review: "board.wake_review",
  blocked: "board.wake_blocked",
  canceled: "board.wake_canceled",
  move: "board.wake_move",
  filing: "board.wake_filing",
  claim: "board.wake_claim",
  assignment: "board.wake_assignment",
  comment: "board.wake_comment",
  chat: "board.wake_chat_message",
};

function summarize(t: TFunction, rows: BoardWakeRow[]): { text: string; attention: boolean } {
  const counts: Record<string, number> = {};
  const bump = (key: string) => (counts[key] = (counts[key] || 0) + 1);
  for (const row of rows) {
    if (row.kind === "moved" && row.to === "review") bump("review");
    else if (row.kind === "moved" && row.to === "blocked") bump("blocked");
    else if (row.kind === "moved" && row.to === "canceled") bump("canceled");
    else if (row.kind === "moved") bump("move");
    else if (row.kind === "filed") bump("filing");
    else if (row.kind === "claimed") bump("claim");
    else if (row.kind === "assigned") bump("assignment");
    else if (row.kind === "comment") bump("comment");
    else if (row.kind === "chat") bump("chat");
  }
  const parts = Object.entries(counts).map(([bucket, n]) =>
    t(WAKE_COUNT_KEYS[bucket], { count: n })
  );
  // reviews/blocked demand a decision — those tint the collapsed line amber
  const attention = (counts.review || 0) + (counts.blocked || 0) > 0;
  return { text: parts.join(", ") || t("board.wake_update"), attention };
}

function rowText(t: TFunction, row: BoardWakeRow): string {
  const item = row.item != null ? `#${row.item}` : "";
  const title = row.title ? ` ${row.title}` : "";
  const ref = `${item}${title}`;
  switch (row.kind) {
    case "moved":
      return t("board.wake_moved", { ref, to: row.to, actor: row.actor });
    case "filed":
      return t("board.wake_filed", { ref, actor: row.actor });
    case "claimed":
      return t("board.wake_claimed", { ref, actor: row.actor });
    case "assigned":
      return t("board.wake_assigned", { ref });
    case "comment":
      return t("board.wake_commented", { ref, actor: row.actor });
    case "chat":
      return t("board.wake_chat", { actor: row.actor });
    default:
      return ref;
  }
}

function stateDot(row: BoardWakeRow): string {
  if (row.kind === "moved" && row.to === "review") return "board-dot review";
  if (row.kind === "moved" && row.to === "blocked") return "board-dot blocked";
  if (row.kind === "moved" && row.to === "done") return "board-dot done";
  if (row.kind === "claimed" || row.kind === "assigned") return "board-dot work";
  return "board-dot idle";
}

export function BoardWakeCard({ source }: { source: MessageSource }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [openNotes, setOpenNotes] = useState<Record<number, boolean>>({});
  const rows = source.board?.rows || [];
  const { text, attention } = summarize(t, rows);
  return (
    <div
      className={"boardwake" + (attention ? " attention" : "")}
      data-testid="boardwake-card"
    >
      <button
        className="boardwake-head"
        data-testid="boardwake-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Icon name="table" size={14} />
        <span className="boardwake-title">{t("board.wake_title")}</span>
        <span className="boardwake-summary">{text}</span>
        <span className="spacer" />
        <span className={"boardwake-chevron" + (open ? " open" : "")}>
          <Icon name="chevronDown" size={13} />
        </span>
      </button>
      {open && (
        <div className="boardwake-body" data-testid="boardwake-body">
          {rows.map((row, i) => (
            <div className="boardwake-row" key={i}>
              <span className={stateDot(row)} />
              <span className="boardwake-row-main">
                <span className="boardwake-row-text">{rowText(t, row)}</span>
                {row.note &&
                  (openNotes[i] ? (
                    <span className="boardwake-note">{row.note}</span>
                  ) : (
                    <button
                      className="boardwake-note-toggle"
                      onClick={() => setOpenNotes((s) => ({ ...s, [i]: true }))}
                    >
                      {row.kind === "chat" || row.kind === "comment"
                        ? t("board.wake_show_message")
                        : t("board.wake_show_handoff")}
                    </button>
                  ))}
              </span>
            </div>
          ))}
          {rows.length === 0 && (
            <div className="boardwake-note">{source.text}</div>
          )}
        </div>
      )}
    </div>
  );
}
