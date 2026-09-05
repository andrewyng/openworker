import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { InboxItem } from "../api";
import type { QuestionOption } from "../types";
import { humanizeApprovalTitle } from "../humanize";
import {
  approvalActionLabels,
  PreviewBlock,
  SaveSkillPreview,
  scopeNote,
  TitleText,
} from "./ApprovalCard";

// One Inbox item, rendered identically in the Inbox list and inline in its own session view
// (answer-in-context). Resolving either place hits the same item id — first responder wins.
// Questions (ask_user) mirror Claude Code's AskUserQuestion: optional quick-reply options + an
// always-available free-text escape, with optional multi-select. OPE-51 adds rich options
// ({label, description, recommended, preview}) and grouped questions (a stepper) — plain-string
// options and single questions render exactly as before.

// Shared styles (mock parity — same language as SourcesDrawer/PersonaView).
const SEC = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const BTN_PRIMARY =
  "px-3 py-1.5 rounded-lg bg-accent text-white text-[13px] font-medium hover:brightness-105 disabled:opacity-40 disabled:hover:brightness-100";
const BTN_BORDERED =
  "px-3 py-1.5 rounded-lg border border-line bg-paper text-[13px] hover:border-lineStrong";
// §35 approval buttons: blue border for the primary, quiet Deny (matches ApprovalCard).
const BTN_ACCENT =
  "px-3 py-1.5 rounded-lg border border-accent text-accent text-[13px] font-semibold hover:bg-accentSoft";
const BTN_QUIET = "px-3 py-1.5 text-[13px] text-faint hover:text-danger";
const OPT_BASE =
  "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-[13px] transition-colors";
const OPT_OFF = "border-line bg-paper text-ink hover:border-accent hover:bg-accentSoft/50";
const OPT_ON = "border-accent bg-accentSoft text-accent font-medium";
const INPUT =
  "flex-1 min-w-0 rounded-lg bg-paper border border-line px-3 py-2 text-[13px] text-ink placeholder:text-faint outline-none focus:border-lineStrong";
// Rich options stack as full-width rows (pills can't hold a description line).
const ROW_BASE = "w-full text-left rounded-lg border px-3 py-2 transition-colors";
const ROW_OFF = "border-line bg-paper hover:border-accent hover:bg-accentSoft/50";
const ROW_ON = "border-accent bg-accentSoft";

// -- question normalization ---------------------------------------------------

interface NormOption {
  label: string;
  description: string;
  recommended: boolean;
  preview: string;
}

const normOption = (o: QuestionOption): NormOption =>
  typeof o === "string"
    ? { label: o, description: "", recommended: false, preview: "" }
    : {
        label: o.label || "",
        description: o.description || "",
        recommended: !!o.recommended,
        preview: o.preview || "",
      };

interface QSpec {
  question: string;
  header: string;
  options: NormOption[];
  allowText: boolean;
  multi: boolean;
}

// The item's question steps: the grouped `questions` list, or the singular fields as one step.
function specsFor(item: InboxItem): QSpec[] {
  const grouped = item.questions || [];
  if (grouped.length)
    return grouped.map((q) => ({
      question: q.question,
      header: q.header || "",
      options: (q.options || []).map(normOption),
      allowText: q.allow_text !== false,
      multi: !!q.multi,
    }));
  return [
    {
      question: item.title,
      header: item.header || "",
      options: (item.options || []).map(normOption),
      allowText: item.allow_text !== false,
      multi: !!item.multi,
    },
  ];
}

// -- one question (options + free-text escape) --------------------------------

function QuestionBlock({ spec, onAnswer }: { spec: QSpec; onAnswer: (a: string) => void }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const { options, multi } = spec;
  // Any description/preview upgrades pills to stacked rows; any preview adds the side pane.
  const rich = options.some((o) => o.description || o.preview);
  const hasPreview = options.some((o) => o.preview);

  const pick = (o: NormOption) => {
    if (multi)
      setSelected((s) => (s.includes(o.label) ? s.filter((x) => x !== o.label) : [...s, o.label]));
    else onAnswer(o.label); // single-select answers immediately (pill behavior, unchanged)
  };

  // The pane follows hover/focus, falls back to the selected option, then the first preview.
  const selIdx = options.findIndex((o) => selected.includes(o.label));
  const previewIdx =
    hoverIdx ?? (selIdx >= 0 && options[selIdx].preview ? selIdx : options.findIndex((o) => o.preview));
  const preview = previewIdx >= 0 ? options[previewIdx].preview : "";

  const recommendedTag = (
    <span className="text-[11px] uppercase tracking-[0.04em] font-semibold text-ok bg-okSoft border border-okLine rounded-full px-1.5 py-px shrink-0">
      {t("inbox.recommended")}
    </span>
  );

  const optionRows = (
    <div className={hasPreview ? "flex flex-col gap-2 min-w-0 sm:w-[46%] shrink-0" : "flex flex-col gap-2 mt-2.5"}>
      {options.map((o, i) => {
        const on = selected.includes(o.label);
        return (
          <button
            key={o.label + i}
            className={ROW_BASE + " " + (on ? ROW_ON : ROW_OFF)}
            onMouseEnter={() => setHoverIdx(i)}
            onMouseLeave={() => setHoverIdx(null)}
            onFocus={() => setHoverIdx(i)}
            onBlur={() => setHoverIdx(null)}
            onClick={() => pick(o)}
          >
            <span
              className={
                "flex items-center gap-2 text-[13px] " + (on ? "text-accent font-medium" : "text-ink font-medium")
              }
            >
              {multi && on && <span className="text-accent text-[11px] leading-none">✓</span>}
              <span className="min-w-0 truncate">{o.label}</span>
              {o.recommended && recommendedTag}
            </span>
            {o.description && (
              <span className="block text-[12px] text-muted mt-0.5 leading-snug">{o.description}</span>
            )}
          </button>
        );
      })}
    </div>
  );

  return (
    <>
      {options.length > 0 &&
        (hasPreview ? (
          // Two-pane: options left, preview right; stacks vertically on narrow widths.
          <div className="flex flex-col sm:flex-row gap-3 mt-2.5">
            {optionRows}
            <pre
              data-testid="question-preview"
              className="flex-1 min-w-0 rounded-lg border border-line bg-paper p-3 text-[12px] leading-relaxed font-mono whitespace-pre overflow-auto max-h-72 text-ink"
            >
              {preview}
            </pre>
          </div>
        ) : rich ? (
          optionRows
        ) : (
          // Plain-string options: today's pills, untouched.
          <div className="flex flex-wrap gap-2 mt-2.5">
            {options.map((o) => {
              const on = selected.includes(o.label);
              return (
                <button
                  key={o.label}
                  className={OPT_BASE + " " + (on ? OPT_ON : OPT_OFF)}
                  onClick={() => pick(o)}
                >
                  {multi && on && <span className="text-accent text-[11px] leading-none">✓</span>}
                  {o.label}
                </button>
              );
            })}
          </div>
        ))}
      {multi && options.length > 0 && (
        <div className="mt-2.5">
          <button
            className={BTN_PRIMARY}
            disabled={!selected.length}
            onClick={() => onAnswer(selected.join(", "))}
          >
            {selected.length ? t("inbox.send_count", { count: selected.length }) : t("common.send")}
          </button>
        </div>
      )}
      {(spec.allowText || options.length === 0) && (
        <div className="flex items-center gap-2 mt-2.5">
          <input
            className={INPUT}
            placeholder={options.length ? t("inbox.or_type_answer") : t("inbox.your_answer")}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && text.trim()) onAnswer(text);
            }}
          />
          <button className={BTN_PRIMARY} disabled={!text.trim()} onClick={() => onAnswer(text)}>
            {t("common.send")}
          </button>
        </div>
      )}
    </>
  );
}

// -- the question card (single, or grouped as a stepper) ----------------------

function QuestionCard({
  item,
  onResolve,
  chip,
}: {
  item: InboxItem;
  onResolve: (id: string, resolution: string) => void;
  chip?: ReactNode;
}) {
  const { t } = useTranslation();
  const specs = specsFor(item);
  const grouped = (item.questions?.length ?? 0) > 0;
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const spec = specs[Math.min(step, specs.length - 1)];
  const next = step + 1 < specs.length ? specs[step + 1] : null;
  // The answer map is keyed by header (falling back to the question text) — the same key the
  // server's answer_result() hands the agent.
  const keyFor = (s: QSpec) => s.header || s.question;

  const submit = (a: string) => {
    if (!grouped) {
      onResolve(item.id, a);
      return;
    }
    const all = { ...answers, [keyFor(spec)]: a };
    setAnswers(all);
    if (step + 1 < specs.length) setStep(step + 1);
    else onResolve(item.id, JSON.stringify(all));
  };

  return (
    <>
      {/* Stepper chips (grouped): "Chart style · 1 of 2 · Distribution ›" — ‹ steps back. */}
      <div className={SEC + " flex items-center gap-1.5"} data-testid={grouped ? "question-stepper" : undefined}>
        {grouped && step > 0 && (
          <button
            className="text-faint hover:text-ink leading-none text-[13px]"
            title={t("inbox.previous_question")}
            aria-label={t("inbox.previous_question")}
            onClick={() => setStep(step - 1)}
          >
            ‹
          </button>
        )}
        <span className={grouped ? "text-accent" : undefined}>
          {spec.header ||
            (grouped ? t("inbox.question_n", { n: step + 1 }) : t("inbox.question_label"))}
        </span>
        {grouped && (
          <>
            <span>·</span>
            <span>{t("inbox.step_of", { step: step + 1, total: specs.length })}</span>
            {next && (
              <>
                <span>·</span>
                <span>{(next.header || t("inbox.question_n", { n: step + 2 })) + " ›"}</span>
              </>
            )}
          </>
        )}
      </div>
      <div className="text-[14px] font-semibold mt-0.5 leading-snug">{spec.question}</div>
      {item.body ? (
        <div className="text-[13px] text-muted mt-1 whitespace-pre-wrap">{item.body}</div>
      ) : null}
      {chip}
      {/* key={step} resets selection/text/hover state when the stepper advances */}
      <QuestionBlock key={step} spec={spec} onAnswer={submit} />
    </>
  );
}

export function InboxItemCard({
  item,
  onResolve,
  chip,
  compact,
}: {
  item: InboxItem;
  onResolve: (id: string, resolution: string) => void;
  chip?: ReactNode; // optional "go to session" affordance (shown in the Inbox list, not inline)
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const isQuestion = item.kind === "question";
  return (
    <div
      className={
        compact
          ? "max-w-3xl mx-auto mb-2.5 rounded-xl2 border border-lineStrong bg-panel px-4 py-3.5 shadow-[0_8px_24px_rgba(0,0,0,0.08)]"
          : "mb-2.5 rounded-xl2 border border-line bg-panel px-3.5 py-3"
      }
    >
      {/* §35: parked approvals wear the SAME humanized dress as the live card — one
          decision, one dialect. Items carry tool+arguments in data since 2026-07-14;
          older rows fall back to the raw kind/title/body treatment below. */}
      {item.kind === "approval" && item.data?.tool ? (
        <div className="flex items-center justify-between gap-3">
          <TitleText line={humanizeApprovalTitle(item.data.tool, item.data.arguments)} />
          {(() => {
            const s = scopeNote(item.data.tool, item.data.arguments);
            return (
              <span className={"text-[11px] whitespace-nowrap pt-0.5 " + (s.external ? "text-warnInk" : "text-faint")}>
                {s.text}
              </span>
            );
          })()}
        </div>
      ) : isQuestion ? null : ( // QuestionCard owns its header + title (stepper needs them)
        <>
          <div className={SEC}>{item.kind}</div>
          <div className="text-[14px] font-semibold mt-0.5 leading-snug">{item.title}</div>
        </>
      )}
      {item.kind === "approval" && item.data?.tool === "save_skill" ? (
        // Parked skill proposals wear the same review surface as the live card (§5.2).
        <SaveSkillPreview args={item.data.arguments} />
      ) : item.kind === "approval" && item.data?.tool && typeof item.data.arguments?.content === "string" ? (
        <PreviewBlock text={item.data.arguments.content} />
      ) : item.kind === "approval" && item.data?.tool && typeof item.data.arguments?.command === "string" ? (
        <PreviewBlock text={item.data.arguments.command} />
      ) : !isQuestion && item.body ? (
        <div className="text-[13px] text-muted mt-1 whitespace-pre-wrap">{item.body}</div>
      ) : null}
      {!isQuestion && chip}
      {item.kind === "approval" ? (
        <div className="flex items-center gap-2 mt-2.5 flex-wrap">
          <button
            className={item.data?.tool ? BTN_ACCENT : BTN_PRIMARY}
            onClick={() => onResolve(item.id, "allow")}
          >
            {item.data?.tool ? approvalActionLabels(item.data.tool).allow : t("inbox.approve")}
          </button>
          {/* Task-persistent standing grant (§25) — present only when the approval was
              raised inside an automation run AND the call can carry a tool+target rule.
              In-app only by construction: Slack mirrors render Approve/Deny buttons. */}
          {item.data?.task_id && item.data?.standing_target && (
            <button
              className={BTN_BORDERED}
              title={t("inbox.always_task_title", {
                target: item.data.standing_target,
                task: item.data.task_title || t("approval.btn.this_automation"),
              })}
              onClick={() => onResolve(item.id, "always_task")}
            >
              {t("approval.btn.allow_every_time")}
            </button>
          )}
          <button
            className={item.data?.tool ? BTN_QUIET : BTN_BORDERED}
            onClick={() => onResolve(item.id, "deny")}
          >
            {item.data?.tool ? approvalActionLabels(item.data.tool).deny : t("approval.deny")}
          </button>
        </div>
      ) : isQuestion ? (
        <QuestionCard item={item} onResolve={onResolve} chip={chip} />
      ) : item.kind === "directory" ? (
        <div className="flex items-center gap-2 mt-2.5">
          <button
            className={BTN_PRIMARY}
            disabled={!item.data?.path}
            title={item.data?.path || t("inbox.no_folder_suggested")}
            onClick={() =>
              onResolve(
                item.id,
                JSON.stringify({
                  granted: true,
                  path: item.data?.path || "",
                  writable: item.data?.primary ? true : !!item.data?.writable,
                }),
              )
            }
          >
            {item.data?.path
              ? item.data?.primary
                ? t("inbox.make_workspace")
                : t("inbox.grant")
              : t("inbox.grant_no_folder")}
          </button>
          <button className={BTN_BORDERED} onClick={() => onResolve(item.id, JSON.stringify({ granted: false }))}>
            {t("approval.deny")}
          </button>
        </div>
      ) : item.kind === "plan" ? (
        <div className="flex items-center gap-2 mt-2.5">
          <button
            className={BTN_PRIMARY}
            onClick={() => onResolve(item.id, JSON.stringify({ approved: true, mode: "interactive" }))}
          >
            {t("inbox.approve")}
          </button>
          <button
            className={BTN_BORDERED}
            onClick={() => onResolve(item.id, JSON.stringify({ approved: false, feedback: "" }))}
          >
            {t("inbox.reject")}
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 mt-2.5">
          <button className={BTN_BORDERED} onClick={() => onResolve(item.id, "seen")}>
            {t("inbox.dismiss")}
          </button>
        </div>
      )}
    </div>
  );
}
