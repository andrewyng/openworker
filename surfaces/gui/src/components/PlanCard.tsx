import { useState } from "react";
import type { Item } from "../types";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

type PlanItem = Extract<Item, { kind: "planreq" }>;

// The agent (in read-only plan mode) proposed a plan via propose_plan. The user approves it —
// choosing whether execution should keep asking per action or run with full access — or sends
// it back with feedback. Mirrors the directory-request card, shown in the composer head.
export function PlanCard({
  item,
  onRespond,
}: {
  item: PlanItem;
  onRespond: (approved: boolean, mode?: string, feedback?: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");

  return (
    <div className="dirreq-card plan-card">
      <div className="dirreq-head">
        <Icon name="sparkle" size={16} className="ico" />
        <span>agent 提出了一个方案</span>
      </div>
      <div className="plan-body">
        <Markdown text={item.plan} />
      </div>
      {rejecting ? (
        <div className="dirreq-actions">
          <input
            className="dirreq-path"
            placeholder="这个方案哪里需要调整？"
            value={feedback}
            autoFocus
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && feedback.trim()) onRespond(false, undefined, feedback.trim());
            }}
          />
          <button className="btn" onClick={() => setRejecting(false)}>
            返回
          </button>
          <button
            className="btn primary"
            disabled={!feedback.trim()}
            onClick={() => onRespond(false, undefined, feedback.trim())}
          >
            发送反馈
          </button>
        </div>
      ) : (
        <div className="dirreq-actions">
          <button className="btn" onClick={() => setRejecting(true)}>
            要求修改
          </button>
          <span className="spacer" />
          <button className="btn" onClick={() => onRespond(true, "interactive")}>
            批准——每步询问
          </button>
          <button className="btn primary" onClick={() => onRespond(true, "auto")}>
            批准并执行
          </button>
        </div>
      )}
    </div>
  );
}
