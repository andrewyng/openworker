import { useState } from "react";
import { getI18n, useTranslation } from "react-i18next";
import type { ApprovalDecision, Item } from "../types";
import { shortArgs } from "./ApprovalCard";
import { humanizeAsk, humanizeTool, type HumanLine } from "../humanize";
import { Markdown } from "./Markdown";
import { BoardWakeCard } from "./BoardWakeCard";
import { ConnectorMessageCard } from "./ConnectorMessageCard";
import { Icon } from "./Icon";

// Long user pastes swallow the transcript (owner ask 2026-07-30): clamp past a generous
// threshold with a more…/less… toggle. Normal typed messages never see the control; the
// full text still drives copy (BubbleMeta) and is what the model received.
const USER_CLAMP_CHARS = 1200;

function ClampedUserText({ text }: { text: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (text.length <= USER_CLAMP_CHARS) return <>{text}</>;
  return (
    <>
      {open ? text : text.slice(0, USER_CLAMP_CHARS).trimEnd() + "…"}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="block ml-auto mt-1.5 text-[13px] font-medium opacity-75 hover:opacity-100"
      >
        {open ? t("transcript.user_less") : t("transcript.user_more")}
      </button>
    </>
  );
}

// Hover affordances for a message bubble (FB-005): copy the raw text + the message's time.
// Lives in a ZERO-HEIGHT strip under the bubble (absolute, inside the transcript's 20px gap)
// so revealing it on group-hover never shifts the layout. `ts` is unix seconds — canonical
// messages carry it, pre-stamp history doesn't, so the time simply omits itself when absent.
function BubbleMeta({ text, ts, align }: { text: string; ts?: number; align: "left" | "right" }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const when = typeof ts === "number" ? new Date(ts * 1000) : null;
  const copy = () => {
    // "Copied" only after the write actually lands — WebKit can reject outside a
    // trusted gesture, and claiming success on a silent no-op would gaslight the user.
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {});
  };
  return (
    <div className="relative h-0 select-none">
      <div
        className={
          "absolute top-1 flex items-center gap-1.5 text-[11px] leading-none text-faint whitespace-nowrap opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity " +
          (align === "right" ? "right-0" : "left-0")
        }
      >
        <button
          className="flex items-center cursor-pointer hover:text-muted"
          data-testid="bubble-copy"
          title={t("transcript.copy_message")}
          onClick={copy}
        >
          {copied ? t("transcript.copied") : <Icon name="copy" size={11} />}
        </button>
        {when && (
          <span data-testid="bubble-ts" title={when.toLocaleString()}>
            {when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
          </span>
        )}
      </div>
    </div>
  );
}

// Reasoning-model thinking text (model-layer roadmap item 4): a quiet disclosure —
// collapsed by default, the trace one click away. `live` = still streaming (pulsing label);
// App renders that variant above the transcript, this one rides a finalized assistant item.
export function ThinkingBlock({ text, live }: { text: string; live?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="thinking">
      <button
        className="thinking-head"
        onClick={() => setOpen((v) => !v)}
        data-testid="thinking-toggle"
      >
        <Icon name="chevronDown" size={12} className={"thinking-caret" + (open ? " open" : "")} />
        <span className={live ? "thinking-live" : undefined}>
          {live ? t("transcript.thinking_live") : t("transcript.thinking_process")}
        </span>
      </button>
      {open && (
        <div className="thinking-body" data-testid="thinking-body">
          {text}
        </div>
      )}
    </div>
  );
}

type ToolItem = Extract<Item, { kind: "tool" }>;
type ApprovalItem = Extract<Item, { kind: "approval" }>;
type AssistantItem = Extract<Item, { kind: "assistant" }>;
type TurnItem = ToolItem | ApprovalItem | AssistantItem;

// TurnGroup (§33, absorbs §7's StepGroup): the whole user-message → final-answer span collapses
// as ONE disclosure — "N steps" — with the agent's narration (assistant text followed by more
// activity in the same turn) and humanized one-line steps interleaved inside. The final assistant
// text renders as a normal bubble OUTSIDE the group (see the flush logic in Transcript below).
// Approvals fold into their tool's row as a chip; an approval with no executed call (typically
// declined) keeps its own "Wanted to …" row. Raw args+result stay one click away per row.

type TurnRow =
  | { type: "narr"; text: string }
  | { type: "step"; tool: ToolItem; approval?: ApprovalItem }
  | { type: "ask"; approval: ApprovalItem };

function buildRows(items: TurnItem[]): TurnRow[] {
  // First pass: tool rows in order; then pair each resolved approval with the nearest
  // same-name tool that doesn't have one yet (approvals may stream before or after their call).
  const rows: TurnRow[] = items
    .filter((it): it is ToolItem | AssistantItem => it.kind !== "approval")
    // Thinking-only assistant items (no text) carry nothing narratable — skip the row.
    .filter((it) => it.kind !== "assistant" || it.text)
    .map((it) =>
      it.kind === "assistant" ? { type: "narr" as const, text: it.text } : { type: "step" as const, tool: it },
    );
  const approvals = items.filter((it): it is ApprovalItem => it.kind === "approval");
  for (const ap of approvals) {
    const at = items.indexOf(ap);
    let bestRow: Extract<TurnRow, { type: "step" }> | null = null;
    let bestDist = Infinity;
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind !== "tool" || it.name !== ap.name) continue;
      const row = rows.find((r) => r.type === "step" && r.tool === it) as
        | Extract<TurnRow, { type: "step" }>
        | undefined;
      if (!row || row.approval) continue;
      const dist = Math.abs(i - at);
      if (dist < bestDist) {
        bestRow = row;
        bestDist = dist;
      }
    }
    if (bestRow && ap.resolved !== "deny") bestRow.approval = ap;
    else {
      // No executed call to attach to (or it was declined) — the ask keeps its own row,
      // placed where the approval sat in the stream.
      const after = items.slice(0, at).filter((it) => it.kind !== "approval").length;
      rows.splice(after, 0, { type: "ask", approval: ap });
    }
  }
  return rows;
}

function originChip(origin: string | undefined, note: string | undefined, grant?: string) {
  const t = getI18n().getFixedT(null, "translation");
  // Replayed user resolutions reuse the card-chip look (live sessions pair the card itself).
  if (origin === "user") {
    if (grant === "deny")
      return <span className="text-[11px] px-1.5 rounded-full bg-dangerSoft text-danger shrink-0">{t("transcript.approval.declined")}</span>;
    return (
      <span
        className="text-[11px] px-1.5 rounded-full bg-okSoft text-ok shrink-0"
        title={
          (grant
            ? t("transcript.approval.approved_scope", { scope: grant.replace(/_/g, " ") })
            : t("transcript.approval.approved_title")) +
          (note ? t("transcript.approval.reviewer_unsure", { note }) : "")
        }
      >
        {t("transcript.approval.approved")}
      </span>
    );
  }
  if (origin !== "reviewer" && origin !== "bypass") return null;
  return (
    <span
      className="text-[11px] text-faint shrink-0"
      data-testid="tool-approval-origin"
      title={
        origin === "reviewer"
          ? note || t("transcript.approval.reviewer_allowed_title")
          : t("transcript.approval.bypass_title")
      }
    >
      {origin === "reviewer" ? t("transcript.approval.auto_approved") : t("transcript.approval.bypassed")}
    </span>
  );
}

function approvalChip(resolved: ApprovalDecision | undefined) {
  const t = getI18n().getFixedT(null, "translation");
  if (resolved === "deny")
    return <span className="text-[11px] px-1.5 rounded-full bg-dangerSoft text-danger shrink-0">{t("transcript.approval.declined")}</span>;
  return (
    <span
      className="text-[11px] px-1.5 rounded-full bg-okSoft text-ok shrink-0"
      title={
        resolved
          ? t("transcript.approval.approved_scope", { scope: resolved.replace(/_/g, " ") })
          : t("transcript.approval.approved_title")
      }
    >
      {t("transcript.approval.approved")}
    </span>
  );
}

function LineText({ line }: { line: HumanLine }) {
  return (
    <span className="min-w-0 text-[13px] leading-relaxed">
      <span className="text-muted">{line.pre}</span>
      {line.obj && <span className="text-ink">{line.obj}</span>}
      {line.post && <span className="text-muted">{line.post}</span>}
    </span>
  );
}

function StepRow({
  tool,
  approval,
  onAllowAnyway,
}: {
  tool: ToolItem;
  approval?: ApprovalItem;
  onAllowAnyway?: (name: string, args: any) => void;
}) {
  const { t } = useTranslation();
  // A reviewer deny (spec §8.4) renders as a card under the step: the FULL reason (the
  // agent only got a terse refusal) plus the one-shot "Allow anyway" override.
  const [overrideSent, setOverrideSent] = useState(false);
  const [raw, setRaw] = useState(false);
  const running = tool.status === "…";
  const failed = tool.status !== "ok" && !running;
  return (
    <div>
      <div className="group flex items-baseline gap-2 px-2 py-0.5 rounded-lg hover:bg-paper" data-testid="turn-step">
        <span className={"w-3.5 text-center text-[11px] shrink-0 " + (failed ? "text-danger" : running ? "text-accent" : "text-ok")}>
          {running ? <span className="spinner" data-testid="step-running" /> : "●"}
        </span>
        <LineText
          line={
            // A refused load must not read as a success — "Used skill:" is the trust line
            // (SKILLS-SPEC §4.1 #4), so a blocked attempt gets honest wording instead.
            tool.name === "load_skill" && tool.preview?.includes('"error"')
              ? {
                  pre: t("transcript.step.tried_skill_pre"),
                  obj: String(tool.args?.name ?? ""),
                  post: t("transcript.step.tried_skill_post"),
                }
              : humanizeTool(tool.name, tool.args)
          }
        />
        {approval && approvalChip(approval.resolved)}
        {!approval && originChip(tool.approvalOrigin, tool.approvalNote, tool.approvalGrant)}
        {!!tool.standingRule && (
          <span
            className="text-[11px] px-1.5 rounded-full bg-tealSoft text-tealInk shrink-0"
            data-testid="tool-standing-rule"
            title={t("transcript.step.auto_allowed_tip", { name: tool.standingRule })}
          >
            {t("transcript.step.auto_allowed")}
          </span>
        )}
        {!!tool.hidden && (
          <span
            className="text-[11px] text-warnInk shrink-0"
            data-testid="tool-hidden-count"
            title={t("transcript.step.hidden_tip")}
          >
            {t("transcript.step.hidden_count_label", { n: tool.hidden })}
          </span>
        )}
        {failed && <span className="text-[11px] text-danger shrink-0">{tool.status}</span>}
        {!running && (
          <button
            className="ml-auto shrink-0 text-[11px] text-faint opacity-0 group-hover:opacity-100 cursor-pointer"
            onClick={() => setRaw((v) => !v)}
          >
            {t("transcript.step.raw")}
          </button>
        )}
      </div>
      {raw && (
        <pre className="ml-8 mr-2 my-1 px-2.5 py-1.5 rounded-lg border border-line bg-paper font-mono text-[12px] leading-relaxed text-muted whitespace-pre-wrap break-words max-h-56 overflow-auto">
          {`${tool.name}  ${shortArgs(tool.args)}`}
          {tool.preview ? `\n→ ${tool.preview.length > 1500 ? tool.preview.slice(0, 1500) + "\n…" : tool.preview}` : ""}
        </pre>
      )}
      {tool.status === "denied" && tool.reviewerReason && (
        <div
          className="ml-8 mr-2 my-1 px-3 py-2 rounded-lg border border-line bg-dangerSoft/40"
          data-testid="reviewer-deny-card"
        >
          <div className="text-[11px] font-medium text-danger">{t("transcript.reviewer.blocked")}</div>
          <div className="text-[12px] text-ink mt-0.5">{tool.reviewerReason}</div>
          <div className="text-[11px] text-faint mt-1">{t("transcript.reviewer.explain")}</div>
          {tool.allowAnyway && onAllowAnyway && !overrideSent && (
            <button
              className="mt-1.5 px-2.5 py-1 rounded-lg border border-line bg-panel text-[12px] text-ink hover:bg-paper"
              data-testid="reviewer-allow-anyway"
              onClick={() => {
                setOverrideSent(true);
                onAllowAnyway(tool.name, tool.args);
              }}
            >
              {t("transcript.reviewer.allow_anyway")}
            </button>
          )}
          {overrideSent && (
            <div className="mt-1.5 text-[11px] text-ok" data-testid="reviewer-override-sent">
              {t("transcript.reviewer.override_sent")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TurnGroup({
  items,
  live,
  streamingText,
  onAllowAnyway,
}: {
  items: TurnItem[];
  live?: boolean;
  // Sub-threshold streamed text belongs to THIS group (§33 ref #3): collapsed → it rides
  // the header as the live line; expanded → the small quiet line under the steps.
  streamingText?: string;
  onAllowAnyway?: (name: string, args: any) => void;
}) {
  const { t } = useTranslation();
  // Turns start COLLAPSED, running or not (owner call 2026-07-14) — the header's live
  // line is the pulse; expanding is opt-in.
  const rows = buildRows(items);
  const tools = items.filter((it): it is ToolItem => it.kind === "tool");
  const running = live || tools.some((t) => t.status === "…");
  const [userToggle, setUserToggle] = useState<boolean | null>(null);
  const open = userToggle ?? false;
  const lastNarr = [...items].reverse().find((it): it is AssistantItem => it.kind === "assistant");
  const liveLine = streamingText || lastNarr?.text || "";

  const nSteps = rows.filter((r) => r.type !== "narr").length;
  const declined = items.filter((it) => it.kind === "approval" && it.resolved === "deny").length;
  const hiddenTotal = tools.reduce((n, t) => n + (t.hidden || 0), 0);
  const stepsLabel = t("transcript.turn.steps_label", { count: nSteps });

  return (
    <details className="stepgroup" open={open}>
      <summary
        className="stepgroup-head flex items-center gap-2 py-0.5 cursor-pointer select-none text-[13px] text-faint hover:text-muted"
        onClick={(e) => {
          e.preventDefault(); // drive open/closed from state, not the native toggle
          setUserToggle(!open);
        }}
      >
        <span className={"chev inline-block transition-transform" + (open ? " rotate-90" : "")}>›</span>
        <span>
          <span>{running ? t("transcript.turn.running", { label: stepsLabel }) : stepsLabel}</span>
          {declined > 0 && (
            <>
              {" · "}
              <span className="text-danger" data-testid="stepgroup-declined">
                {t("transcript.turn.declined", { count: declined })}
              </span>
            </>
          )}
          {hiddenTotal > 0 && (
            <>
              {" · "}
              <span className="text-warnInk" data-testid="stepgroup-hidden">
                {t("transcript.turn.hidden", { count: hiddenTotal })}
              </span>
            </>
          )}
        </span>
        {running && !open && liveLine && (
          <span className="min-w-0 flex-1 truncate" data-testid="turn-live-line">
            · {liveLine}
          </span>
        )}
      </summary>
      {open && (
        <div className="ml-1.5 mt-1 pl-2 border-l-2 border-line flex flex-col gap-0.5">
          {rows.map((row, i) =>
            row.type === "narr" ? (
              <div className="turn-narr px-2 py-1 text-[13px] text-muted max-w-[60ch]" key={i} data-testid="turn-narration">
                <Markdown text={row.text} />
              </div>
            ) : row.type === "ask" ? (
              <div className="flex items-baseline gap-2 px-2 py-0.5" key={i} data-testid="turn-ask">
                <span className={"w-3.5 text-center text-[11px] shrink-0 " + (row.approval.resolved === "deny" ? "text-danger" : "text-ok")}>●</span>
                <LineText line={humanizeAsk(row.approval.name, row.approval.args)} />
                {approvalChip(row.approval.resolved)}
              </div>
            ) : (
              <StepRow tool={row.tool} approval={row.approval} onAllowAnyway={onAllowAnyway} key={i} />
            ),
          )}
          {streamingText && (
            <div
              className="turn-narr px-2 py-1 text-[13px] text-muted max-w-[60ch]"
              data-testid="turn-live-stream"
            >
              <Markdown text={streamingText} />
              <span className="stream-cursor">▍</span>
            </div>
          )}
        </div>
      )}
    </details>
  );
}

interface Props {
  items: Item[];
  onApprove: (decision: ApprovalDecision) => void;
  // The session's live flag. While true, the FINAL run's trailing assistant text is still
  // narration (status), not the answer — promoting it early made each line flash as a full
  // ASSISTANT bubble and then vanish into the group when the next tool call arrived
  // (owner report 2026-07-13). The answer bubble appears once, when the turn ends.
  running?: boolean;
  // Sub-threshold streamed text (streamGate mode "quiet") — handed to the live turn group.
  streamingText?: string;
  // Re-run the failed turn (no new user message). Offered only on a retriable notice that
  // is the transcript tail of an idle session — anywhere else the error is history.
  onRetry?: () => void;
  // mcp_error notices: "Open Connectors" jumps to the Connectors page (Integrations surface).
  onOpenConnectors?: () => void;
  // MEMORY-SPEC §5.1: undo a just-announced write. `previous` (set when the write was
  // an edit) is the text to restore; without it the memory is deleted.
  onUndoMemory?: (id: number, previous?: string) => void;
  // §8.4 "Allow anyway" on a reviewer-denied tool: one-shot exact-action override.
  onAllowAnyway?: (name: string, args: any) => void;
}

// The transcript index whose notice gets the Retry button: the tail error notice, looking
// through info notices after it (model switches must not consume the retry — switching
// models and THEN retrying is the intended recovery path). -1 when the tail is anything else.
export function retryAnchor(items: Item[]): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind !== "notice") return -1;
    if (it.retriable) return i;
    if (it.tone !== "info") return -1;
  }
  return -1;
}

// One quiet line for a dead MCP server (owner ruling 2026-08-21): the summary names the
// server; the raw error (stderr excerpts, tracebacks) hides behind a Details disclosure;
// "Open Connectors" is the fix/remove path. Never a wall of blue text in the transcript.
function McpNotice({
  item,
  onOpenConnectors,
}: {
  item: Extract<Item, { kind: "notice" }>;
  onOpenConnectors?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="mcp-notice" data-testid="mcp-notice">
      <div className="mcp-notice-line">
        <span aria-hidden>⚠</span>
        <span className="min-w-0 truncate">{item.text}</span>
        <button
          className="mcp-notice-act"
          data-testid="mcp-notice-details"
          onClick={() => setOpen((v) => !v)}
        >
          {t("transcript.mcp_details")} {open ? "⌃" : "⌄"}
        </button>
        {onOpenConnectors && (
          <button
            className="mcp-notice-act"
            data-testid="mcp-notice-connectors"
            onClick={onOpenConnectors}
          >
            {t("transcript.mcp_open_connectors")}
          </button>
        )}
      </div>
      {open && (
        <pre className="mcp-notice-detail" data-testid="mcp-notice-detail">
          {item.detail}
        </pre>
      )}
    </div>
  );
}


export function Transcript({ items, running, streamingText, onRetry, onOpenConnectors, onUndoMemory, onAllowAnyway }: Props) {
  const { t } = useTranslation();
  // §33 grouping: a turn = the maximal run of assistant/tool/resolved-approval items between
  // breakers (user, connector, notices, plan/dir requests…). Trailing assistant texts are the
  // ANSWER and render as bubbles after the group; interior assistant texts are narration and
  // stay inside. A run with no activity at all is just bubbles (unchanged chat behavior).
  const blocks: Array<{ turn: TurnItem[]; live?: boolean } | { item: Item; i: number }> = [];
  let run: TurnItem[] = [];
  const flush = (live = false) => {
    if (!run.length) return;
    const turn = [...run];
    run = [];
    const answers: AssistantItem[] = [];
    // A live run with tool activity keeps its trailing text inside as the status line;
    // a live run with NO activity is a plain streaming reply — bubbles, as ever.
    const keepTrailing = live && turn.some((it) => it.kind !== "assistant");
    if (!keepTrailing)
      while (turn.length && turn[turn.length - 1].kind === "assistant")
        answers.unshift(turn.pop() as AssistantItem);
    if (turn.some((it) => it.kind !== "assistant")) blocks.push({ turn, live });
    else turn.forEach((t) => blocks.push({ item: t, i: -1 }));
    answers.forEach((a) => blocks.push({ item: a, i: -1 }));
  };
  items.forEach((item, i) => {
    if (item.kind === "tool" || item.kind === "assistant" || (item.kind === "approval" && item.resolved))
      run.push(item);
    else if (
      // PENDING interactive items render elsewhere (approval/question → composer head) and
      // nothing here — if they broke the run, the trailing narration would flash into an
      // answer bubble exactly while the user is being asked to decide.
      (item.kind === "approval" || item.kind === "dirreq" || item.kind === "planreq" || item.kind === "question") &&
      !item.resolved
    ) {
      return;
    } else {
      flush();
      blocks.push({ item, i });
    }
  });
  flush(!!running);

  const lastTurnIndex = blocks.reduce((acc, b, i) => ("turn" in b ? i : acc), -1);
  return (
    <div className="transcript">
      {blocks.map((block, bi) => {
        if ("turn" in block)
          return (
            <TurnGroup
              items={block.turn}
              live={block.live}
              streamingText={block.live && bi === lastTurnIndex ? streamingText : undefined}
              onAllowAnyway={onAllowAnyway}
              key={bi}
            />
          );
        const { item } = block;
        switch (item.kind) {
          case "connector":
            // Board wakes get their own collapsed-by-default card — a report,
            // not a foreign message (owner ask 2026-08-16).
            return item.source.connector === "board" ? (
              <BoardWakeCard source={item.source} key={bi} />
            ) : (
              <ConnectorMessageCard source={item.source} key={bi} />
            );
          case "user":
            return (
              <div className="group self-end max-w-[78%] flex flex-col items-end" key={bi}>
                <div className="bubble-user px-3.5 py-2.5 rounded-[14px_14px_4px_14px] bg-solid text-onSolid text-[14px] leading-relaxed whitespace-pre-wrap">
                  {item.attachments && item.attachments.length > 0 && (
                    <div className="bubble-attachments">
                      {item.attachments.map((a, i) =>
                        a.kind === "image" ? (
                          <img key={i} className="msg-img" src={a.data_url} alt={a.name} />
                        ) : (
                          <span key={i} className="msg-file">📄 {a.name}</span>
                        ),
                      )}
                    </div>
                  )}
                  <ClampedUserText text={item.text} />
                </div>
                <BubbleMeta text={item.text} ts={item.ts} align="right" />
              </div>
            );
          case "assistant":
            // Thinking-only item (stopped mid-reasoning): just the disclosure, no bubble.
            if (!item.text && item.reasoning)
              return (
                <div key={bi}>
                  <ThinkingBlock text={item.reasoning} />
                </div>
              );
            return (
              <div className="group bubble-assistant" key={bi}>
                <div className="who">{t("transcript.who_assistant")}</div>
                {item.reasoning && <ThinkingBlock text={item.reasoning} />}
                <Markdown text={item.text} />
                <BubbleMeta text={item.text} ts={item.ts} align="left" />
              </div>
            );
          case "dirreq":
            if (!item.resolved) return null;
            return (
              <div className="approval-inline" key={bi}>
                <span className={"status " + (item.resolved === "granted" ? "ok" : "denied")}>
                  {item.resolved === "granted" ? "✓" : "✕"}
                </span>
                <span>{item.resolved === "granted" ? t("transcript.dir_granted") : t("transcript.dir_declined")}</span>
                {item.path && <span className="dim">{item.path}</span>}
              </div>
            );
          case "planreq":
            if (!item.resolved) return null; // pending plan renders in the composer head
            return (
              <div className="bubble-assistant" key={bi}>
                <div className="who">{t("transcript.plan_proposed")}</div>
                <Markdown text={item.plan} />
                <div className="approval-inline">
                  <span className={"status " + (item.resolved === "approved" ? "ok" : "denied")}>
                    {item.resolved === "approved" ? "✓" : "✕"}
                  </span>
                  <span>{item.resolved === "approved" ? t("transcript.plan_approved") : t("transcript.plan_rejected")}</span>
                </div>
              </div>
            );
          case "notice":
            if (item.server && item.detail)
              return (
                <McpNotice key={bi} item={item} onOpenConnectors={onOpenConnectors} />
              );
            // A titled notice is prose (the Auto-Approve banner), not a status line.
            if (item.title) {
              return (
                <div
                  className={"notice notice-block " + (item.tone === "warn" ? "warn" : "")}
                  key={bi}
                  data-testid="mode-notice"
                >
                  <div className="notice-title">{item.title}</div>
                  {item.text.split("\n\n").map((para, i) => (
                    <p key={i}>{para}</p>
                  ))}
                </div>
              );
            }
            return (
              <div className={"notice " + (item.tone === "warn" ? "warn" : "")} key={bi}>
                {item.text}
                {item.retriable && !running && onRetry && block.i === retryAnchor(items) && (
                  <button className="btn ml-2" data-testid="notice-retry" onClick={onRetry}>
                    {t("transcript.retry")}
                  </button>
                )}
              </div>
            );
          // §5.1 save notice: quiet, inline, and it STAYS — the user reads it in place
          // and can undo whenever they get to it.
          case "memory":
            return (
              <div
                className="notice flex items-center gap-2 text-left"
                data-testid="memory-notice"
                key={bi}
              >
                {item.undone ? (
                  <span data-testid="memory-notice-undone">
                    {item.previous
                      ? t("transcript.memory.undone_restored")
                      : t("transcript.memory.undone_forgotten")}
                  </span>
                ) : (
                  <>
                    <span className="min-w-0">
                      <span className="font-medium">
                        {item.previous ? t("transcript.memory.updated") : t("transcript.memory.saved")}
                      </span>
                      {item.text ? <span className="text-muted"> — {item.text}</span> : null}
                    </span>
                    {onUndoMemory && (
                      <button
                        className="btn ml-auto shrink-0"
                        data-testid="memory-notice-undo"
                        onClick={() => onUndoMemory(item.id, item.previous)}
                      >
                        {t("transcript.memory.undo")}
                      </button>
                    )}
                  </>
                )}
              </div>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
