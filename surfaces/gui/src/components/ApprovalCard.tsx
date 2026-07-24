import { useState } from "react";
import type { ApprovalDecision, Item } from "../types";
import { humanizeApprovalTitle, type HumanLine } from "../humanize";
import { Icon } from "./Icon";

export function shortArgs(args: any): string {
  if (!args || typeof args !== "object") return "";
  return Object.entries(args)
    .map(([k, v]) => {
      let s = typeof v === "string" ? v : JSON.stringify(v);
      if (s.length > 96) s = s.slice(0, 95) + "...";
      return `${k}=${s.replace(/\n/g, " ")}`;
    })
    .join("  ");
}

// Human verbs kept for the §25 grant lines (the card title now comes from humanize.ts).
const TOOL_VERBS: Record<string, string> = {
  write_file: "Write a file",
  replace_in_file: "Edit a file",
  apply_patch: "Apply a patch",
  apply_unified_diff: "Apply a patch",
  run_shell: "Run a command",
  send_message: "Send a message",
  send_file: "Send a file",
};

// §35: routine workspace writes render as a compact ROW; everything else is a full card.
const FILE_WRITES = new Set(["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]);
// Actions that leave the Mac get the warm border + explicit destination note.
const EXTERNAL = new Set(["send_message", "send_file"]);

type ApprovalItem = Extract<Item, { kind: "approval" }>;

// A `permissions` proposal on the create_scheduled_task consent card (§25): reads are
// disclosure lines, writes are the standing grants the approval mints.
interface PermissionLine {
  tool: string;
  target: string;
  access: string;
}

function permissionLines(args: any): PermissionLine[] {
  const raw = args?.permissions;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((p) => p && typeof p === "object" && p.tool && p.target)
    .map((p) => ({ tool: String(p.tool), target: String(p.target), access: String(p.access || "read") }));
}

export function TitleText({ line }: { line: HumanLine }) {
  return (
    <span className="approval-title">
      {line.pre}
      {line.obj && <b>{line.obj}</b>}
      {line.post}
    </span>
  );
}

// Plain-words scope note (replaces the "local action" badge): where does this act?
// Shared with the parked-approval card (InboxItemCard) so both dialects match (§35).
export function scopeNote(
  name: string,
  args: any,
  category?: string,
): { text: string; external: boolean } {
  if (category === "connector") return { text: "acts on a connected service", external: true };
  if (EXTERNAL.has(name)) {
    const platform = String(args?.target ?? "").split(":")[0];
    const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
    return { text: `leaves this Mac → ${names[platform] || platform || "a connected chat"}`, external: true };
  }
  const overwrite = name === "write_file" && args?.overwrite;
  return { text: "stays on this Mac" + (overwrite ? " · overwrites the existing file" : ""), external: false };
}

// The proposed content/command, straight from the tool call's ARGS — the file/action
// doesn't exist yet, so no viewer could show it (§35; see UX-018 mock note).
// Clamps by CHARACTERS as well as lines: a one-paragraph Slack digest has no
// newlines at all and once ballooned the card to full-transcript height.
const PREVIEW_LINES = 5;
const PREVIEW_CHARS = 420;

export interface DiffLine {
  type: "add" | "del" | "hdr" | "ctx";
  content: string;
}

export function parseDiffLines(args: any): DiffLine[] | null {
  if (!args || typeof args !== "object") return null;

  // Case 1: Unified diff or patch string
  const diffStr = args.patch || args.diff || args.unified_diff || args.changes;
  if (typeof diffStr === "string" && diffStr.trim().length > 0) {
    const rawLines = diffStr.split("\n");
    return rawLines.map((line) => {
      if (
        line.startsWith("+++") ||
        line.startsWith("---") ||
        line.startsWith("@@") ||
        line.startsWith("diff --git")
      ) {
        return { type: "hdr", content: line };
      }
      if (line.startsWith("+")) {
        return { type: "add", content: line };
      }
      if (line.startsWith("-")) {
        return { type: "del", content: line };
      }
      return { type: "ctx", content: line };
    });
  }

  // Case 2: Replace in file (support all parameter aliases)
  const oldStr =
    args.old_string ??
    args.old_str ??
    args.old_content ??
    args.old_text ??
    args.target_content ??
    args.original ??
    args.search ??
    args.find ??
    args.old;
  const newStr =
    args.new_string ??
    args.new_str ??
    args.new_content ??
    args.new_text ??
    args.replacement_content ??
    args.replacement ??
    args.replace ??
    args.new;

  if (typeof oldStr === "string" || typeof newStr === "string") {
    const lines: DiffLine[] = [];
    if (typeof oldStr === "string" && oldStr.trim().length > 0) {
      oldStr.split("\n").forEach((l) =>
        lines.push({ type: "del", content: "-" + (l.startsWith("-") ? l.slice(1) : l) }),
      );
    }
    if (typeof newStr === "string" && newStr.trim().length > 0) {
      newStr.split("\n").forEach((l) =>
        lines.push({ type: "add", content: "+" + (l.startsWith("+") ? l.slice(1) : l) }),
      );
    }
    if (lines.length > 0) return lines;
  }

  return null;
}

export function DiffPreviewBlock({ lines }: { lines: DiffLine[] }) {
  const [all, setAll] = useState(false);
  const clipped = lines.length > PREVIEW_LINES;
  const shown = all || !clipped ? lines : lines.slice(0, PREVIEW_LINES);

  const addCount = lines.filter((l) => l.type === "add").length;
  const delCount = lines.filter((l) => l.type === "del").length;

  return (
    <div className="approval-diff-box" data-testid="approval-diff-box">
      <div className="approval-diff-stats">
        <span className="diff-tag">Diff Preview</span>
        {addCount > 0 && <span className="diff-add-count">+{addCount}</span>}
        {delCount > 0 && <span className="diff-del-count">-{delCount}</span>}
      </div>
      <div className="approval-diff-body">
        {shown.map((line, idx) => (
          <div key={idx} className={`approval-diff-line line-${line.type}`}>
            <span className="diff-line-prefix">
              {line.type === "add" ? "+" : line.type === "del" ? "-" : line.type === "hdr" ? "@" : " "}
            </span>
            <span className="diff-line-text">{line.content.replace(/^[+-]/, "")}</span>
          </div>
        ))}
      </div>
      {clipped && (
        <button className="approval-prev-more" onClick={() => setAll((v) => !v)}>
          {all ? "show less" : `show all ${lines.length} diff lines`}
        </button>
      )}
    </div>
  );
}

export function PreviewBlock({ text, mono = true }: { text: string; mono?: boolean }) {
  const [all, setAll] = useState(false);
  const lines = text.split("\n");
  const clipped = lines.length > PREVIEW_LINES || text.length > PREVIEW_CHARS;
  let shown = text;
  if (!all && clipped) {
    shown = lines.slice(0, PREVIEW_LINES).join("\n");
    if (shown.length > PREVIEW_CHARS) shown = shown.slice(0, PREVIEW_CHARS).trimEnd() + "…";
  }
  return (
    <div className={"approval-prev" + (mono ? "" : " prose")}>
      {shown}
      {clipped && (
        <button className="approval-prev-more" onClick={() => setAll((v) => !v)}>
          {all
            ? "show less"
            : lines.length > PREVIEW_LINES
              ? `show all ${lines.length} lines`
              : "show the full message"}
        </button>
      )}
    </div>
  );
}

// Outbound message text: short one-liners keep the cozy inline quote; anything
// long (or multi-line) gets the clamped preview so the card stays card-sized.
function MessagePreview({ text, label }: { text: string; label?: string }) {
  if (text.length <= 220 && !text.includes("\n")) {
    return (
      <div className="approval-with">
        {label ? `${label}: ` : ""}“{text}”
      </div>
    );
  }
  return <PreviewBlock text={text} mono={false} />;
}

function Buttons({
  item,
  onApprove,
  runTask,
  primaryLabel,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  runTask?: { id: string; title: string } | null;
  primaryLabel: string;
}) {
  const connector = item.category === "connector";
  const offerStanding = !!(runTask && item.standingTarget);
  return (
    <div className="approval-btns">
      <button className="btn approval-primary" onClick={() => onApprove("once")}>
        {primaryLabel}
      </button>
      {offerStanding && (
        <button
          className="btn"
          title={`Always allow ${item.name} → ${item.standingTarget} for “${runTask?.title || "this automation"}” — revoke any time on its Automations page`}
          onClick={() => onApprove("always_task")}
        >
          Allow every time
        </button>
      )}
      {/* In a run context the task-persistent grant replaces the session-scoped one —
          a run session is ephemeral, and two adjacent "always" buttons would blur
          exactly the scope distinction §25 exists to draw. Same rule for run_shell:
          the command-scoped button below is the specific (safer) grant, so the
          tool-wide one stays out of the card. */}
      {!connector && !offerStanding && item.name !== "run_shell" && (
        <button
          className="btn"
          title={`Always allow ${TOOL_VERBS[item.name]?.toLowerCase() || item.name} for this session`}
          onClick={() => onApprove("always_tool")}
        >
          Always allow
        </button>
      )}
      {item.name === "run_shell" && (
        <button className="btn" onClick={() => onApprove("always_command")}>
          Always allow this command
        </button>
      )}
      <span className="spacer" />
      <button className="btn quiet-deny" onClick={() => onApprove("deny")}>
        Deny
      </button>
    </div>
  );
}

export function ApprovalCard({
  item,
  onApprove,
  runTask,
  compact = false,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  // Present when this approval was raised inside an automation run — unlocks the
  // task-persistent "Allow every time" (in-app only, §25).
  runTask?: { id: string; title: string } | null;
  compact?: boolean;
}) {
  const [peek, setPeek] = useState(false);
  const title = humanizeApprovalTitle(item.name, item.args);
  const scope = scopeNote(item.name, item.args, item.category);
  const grants = item.name === "create_scheduled_task" ? permissionLines(item.args) : [];
  // "requires approval" is the engine's default boilerplate — only surface a real reason.
  const reason = item.reason && item.reason !== "requires approval" ? item.reason : "";
  const offerStanding = !!(runTask && item.standingTarget);
  const dock = compact ? " approval-dock" : "";

  // §35 compact row: routine workspace writes — one line, preview expands inline from the
  // tool args. Standing/grant flows keep the full card (they carry §25 consent weight).
  const content = typeof item.args?.content === "string" ? item.args.content : "";
  const diffLines = parseDiffLines(item.args);
  const hasPreview = !!(diffLines || content || shortArgs(item.args));

  if (FILE_WRITES.has(item.name) && !offerStanding && !grants.length && !item.resolved) {
    return (
      <div className={"approval approval-row" + dock} data-testid="approval-row">
        <div className="approval-row-line">
          <TitleText line={title} />
          {hasPreview && (
            <button className="approval-peek" onClick={() => setPeek((v) => !v)}>
              preview {peek ? "▴" : "▾"}
            </button>
          )}
          <span className="spacer" />
          <Buttons item={item} onApprove={onApprove} runTask={runTask} primaryLabel="Allow" />
        </div>
        {peek &&
          (diffLines ? (
            <DiffPreviewBlock lines={diffLines} />
          ) : content ? (
            <PreviewBlock text={content} />
          ) : (
            <PreviewBlock text={shortArgs(item.args)} />
          ))}
        {reason && <div className="approval-reason">{reason}</div>}
      </div>
    );
  }

  return (
    <div className={"approval" + (scope.external ? " approval-external" : "") + dock}>
      <div className="approval-top">
        <div className="approval-heading">
          <span className="approval-ico" title={`Tool: ${item.name}`}>
            <Icon name="shield" size={15} />
          </span>
          <TitleText line={title} />
        </div>
        <span className={"approval-scope" + (scope.external ? " out" : "")}>{scope.text}</span>
      </div>

      {/* Tool-shaped previews — the proposal, not an args dump. */}
      {item.name === "run_shell" && item.args?.command && (
        <PreviewBlock text={String(item.args.command)} />
      )}
      {FILE_WRITES.has(item.name) && (
        diffLines ? <DiffPreviewBlock lines={diffLines} /> : content ? <PreviewBlock text={content} /> : null
      )}
      {item.name === "send_file" && (
        <>
          <span className="approval-filechip">
            <span className="ico">
              <Icon name="file" size={13} />
            </span>
            {String(item.args?.path ?? "").split("/").pop() || "file"}
            {item.args?.as_screenshot ? " · as a PNG screenshot" : ""}
          </span>
          {item.args?.comment && (
            <MessagePreview text={String(item.args.comment)} label="With the message" />
          )}
        </>
      )}
      {item.name === "send_message" && item.args?.text && (
        <MessagePreview text={String(item.args.text)} />
      )}

      {grants.length > 0 && (
        <div className="approval-grants" data-testid="approval-grants">
          {grants.map((g, i) => (
            <div className="approval-grant" key={i} data-access={g.access}>
              <span className={"grant-mark" + (g.access === "write" ? " write" : "")}>
                {g.access === "write" ? "✓" : "·"}
              </span>
              <span className="grant-line">
                {TOOL_VERBS[g.tool] || g.tool} <code className="approval-tool">{g.target}</code>
                <span className="grant-note">
                  {g.access === "write" ? " — always allowed once you approve" : " — read-only"}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
      {/* Long-tail tools: no bespoke preview — fall back to the compact args line. */}
      {!FILE_WRITES.has(item.name) &&
        !["run_shell", "send_message", "send_file"].includes(item.name) &&
        !grants.length &&
        shortArgs(item.args) && <div className="approval-rest">{shortArgs(item.args)}</div>}
      {reason && <div className="approval-reason">{reason}</div>}

      {item.resolved ? (
        <div className="resolved">Approved: {item.resolved.replace("_", " ")}</div>
      ) : (
        <Buttons item={item} onApprove={onApprove} runTask={runTask} primaryLabel="Allow once" />
      )}
    </div>
  );
}
