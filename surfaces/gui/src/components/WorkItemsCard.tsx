// The decomposition gate (agent teams): a lead proposes work items; approval
// creates them on the board. The board-flavored sibling of PlanCard — items with
// acceptance criteria as the primary text, 3 visible + expander with the true
// count in the header, no in-card reply surface (editing happens by replying).
import { useState } from "react";
import type { Item } from "../types";
import { Icon } from "./Icon";

// Past this length, "Done when" clamps to two lines with a per-item expander —
// a model that writes essay criteria must not occupy two screens of gate card
// (owner-hit 2026-08-16). The full text is one click away; the lead's prompt
// separately pushes criteria back toward 1–3 crisp checks.
const CRITERIA_CLAMP_CHARS = 160;

export function WorkItemsCard({
  item,
  onRespond,
}: {
  item: Extract<Item, { kind: "itemsreq" }>;
  onRespond: (approved: boolean, feedback?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [openCriteria, setOpenCriteria] = useState<Record<number, boolean>>({});
  const visible = expanded ? item.items : item.items.slice(0, 3);
  const hidden = item.items.length - visible.length;
  return (
    <div className="dirreq-card itemsreq-card" data-testid="itemsreq-card">
      <div className="itemsreq-head">
        <Icon name="table" size={15} />
        <span className="itemsreq-title">
          Proposed work items — {item.items.length}
        </span>
      </div>
      {item.note && <div className="itemsreq-note">{item.note}</div>}
      <div className="itemsreq-list">
        {visible.map((entry, i) => {
          const long = (entry.criteria || "").length > CRITERIA_CLAMP_CHARS;
          const open = !!openCriteria[i];
          return (
            <div className="itemsreq-item" key={i}>
              <span className="itemsreq-num">{i + 1}.</span>
              <span className="itemsreq-body">
                <span className="itemsreq-item-title">{entry.title}</span>
                <span className={"itemsreq-ac" + (long && !open ? " clamped" : "")}>
                  <b>Done when:</b> {entry.criteria}
                </span>
                {long && (
                  <button
                    className="itemsreq-ac-toggle"
                    data-testid={`itemsreq-ac-toggle-${i}`}
                    onClick={() => setOpenCriteria((s) => ({ ...s, [i]: !s[i] }))}
                  >
                    {open ? "Show less" : "Show full criteria"}
                  </button>
                )}
              </span>
            </div>
          );
        })}
        {hidden > 0 && (
          <button className="itemsreq-more" onClick={() => setExpanded(true)}>
            ＋ {hidden} more item{hidden === 1 ? "" : "s"}
            <Icon name="chevronDown" size={12} />
          </button>
        )}
      </div>
      <div className="dirreq-actions">
        <span className="itemsreq-grant">
          Reply to edit the split; approval creates these on the board.
        </span>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          Not now
        </button>
        <button
          className="btn primary"
          data-testid="itemsreq-approve"
          onClick={() => onRespond(true)}
        >
          Approve items
        </button>
      </div>
    </div>
  );
}
