// Observability Phase 3, OB-5: the quiet line under an answer, and its rating (technical-
// design.md §8, UX-059 ob5). Time, this turn's tokens (the header pill is the whole session,
// M3-R6), its warnings, what was auto-approved or asked (only when not 0, M3-R7), a Steps
// link and 👍/👎. Always shown under the last turn, on hover for older ones (Q9). A 👎 opens a
// card with the five reasons and a note; its last line says where the rating went.

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getSessionFooters, putTurnFeedback } from "../api";
import { formatTokens } from "../usage";
import { formatDuration } from "./StepsPanel";
import { footerParts, REASONS, whereKey, type FeedbackWhere, type TurnFeedback, type TurnFooter } from "../obs3";

export interface FootersState {
  turns: TurnFooter[];
  recording: boolean;
  where?: FeedbackWhere;
}

/** A session's footers, read again whenever `refreshKey` moves (a turn ended). */
export function useSessionFooters(sessionId: string | null | undefined, refreshKey: unknown): [FootersState | null, (f: TurnFooter) => void] {
  const [state, setState] = useState<FootersState | null>(null);
  useEffect(() => {
    let live = true;
    if (!sessionId) {
      setState(null);
      return;
    }
    getSessionFooters(sessionId)
      .then((r) => live && setState({ turns: r.turns || [], recording: r.recording !== false, where: r.where }))
      .catch(() => live && setState(null));
    return () => {
      live = false;
    };
  }, [sessionId, refreshKey]);
  const update = (f: TurnFooter) =>
    setState((s) => (s ? { ...s, turns: s.turns.map((x) => (x.turn_id === f.turn_id ? f : x)) } : s));
  return [state, update];
}

export function TurnFooterLine({
  sessionId,
  footer,
  recording = true,
  last,
  onOpenSteps,
  onChange,
  defaultWhere,
}: {
  sessionId: string;
  footer: TurnFooter;
  recording?: boolean;
  last: boolean;
  defaultWhere?: FeedbackWhere;
  onOpenSteps?: () => void;
  onChange?: (f: TurnFooter) => void;
}) {
  const { t } = useTranslation();
  const [card, setCard] = useState(false);
  const [where, setWhere] = useState<FeedbackWhere | null>(null);
  const rating = footer.feedback?.rating ?? null;

  const rate = async (r: "up" | "down" | null, reasons: string[] = [], note?: string) => {
    try {
      const res = await putTurnFeedback(sessionId, footer.turn_id, { rating: r, reasons, note });
      setWhere(res.where);
      onChange?.({ ...footer, feedback: res.feedback });
      return true;
    } catch {
      return false;
    }
  };

  const parts = recording ? footerParts(footer) : [];
  return (
    <div className={"turnfoot-wrap" + (last ? " last" : "")} data-testid="turn-footer">
      <div className="turnfoot">
        {!recording ? (
          <span className="text-faint">{t("p3.foot.recording_off")}</span>
        ) : (
          parts.map((p, i) => (
            <span key={p.kind} className="turnfoot-part">
              {i > 0 && <span className="sep">·</span>}
              {p.kind === "time" && <span>{formatDuration(p.ms)}</span>}
              {p.kind === "tokens" && <span data-testid="turn-footer-tokens">{t("p3.foot.tokens", { tokens: (p.at_least ? "≥ " : "") + formatTokens(p.total) })}</span>}
              {p.kind === "issues" && <span className="steps-badge warn" data-testid="turn-footer-issues">{t("p3.foot.issues", { count: p.n })}</span>}
              {p.kind === "auto_approved" && <span className="c-review">{t("p3.foot.auto_approved", { count: p.n })}</span>}
              {p.kind === "asked" && <span>{t("p3.foot.asked", { count: p.n })}</span>}
            </span>
          ))
        )}
        {onOpenSteps && recording && (
          <>
            <span className="sep">·</span>
            <button className="p3-link" data-testid="turn-footer-steps" onClick={onOpenSteps}>{t("steps.open")}</button>
          </>
        )}
        <span className="sep">·</span>
        {/* T3-8: while the 👎 card is open, 👎 is the one shown chosen; 👎 again closes the card
            and leaves the rating as it was. */}
        <button className={"thumb" + (rating === "up" && !card ? " sel" : "")} aria-pressed={rating === "up" && !card} data-testid="turn-footer-up"
          title={t("p3.fb.up")} onClick={() => { setCard(false); void rate(rating === "up" ? null : "up"); }}>👍</button>
        <button className={"thumb" + (rating === "down" || card ? " sel" : "")} aria-pressed={rating === "down" || card} data-testid="turn-footer-down"
          title={t("p3.fb.down")} onClick={() => {
            if (card) setCard(false);
            else if (rating === "down") void rate(null);
            else setCard(true);
          }}>👎</button>
        {where && !card && <span className="text-faint ml-2" data-testid="turn-footer-where">{whereLine(where, t)}</span>}
      </div>
      {card && (
        <RatingCard
          initial={footer.feedback}
          onSend={async (reasons, note) => {
            const ok = await rate("down", reasons, note);
            if (ok) setCard(false);
            return ok;
          }}
          onSkip={async () => {
            await rate("down");
            setCard(false);
          }}
          where={where ?? defaultWhere}
        />
      )}
    </div>
  );
}

/** The machine's own answer says whether the note went too (`with_note`: a note was given
 * and "Your messages and the AI's answers" is on, TD3-11). */
export function whereLine(w: FeedbackWhere | null | undefined, t: (k: string, o?: any) => string, future = false): string {
  const { key, withNote } = whereKey(w, future);
  return t(key, { by: w?.by || "" }) + (withNote ? " " + t("p3.fb.with_note") : "");
}

export function RatingCard({
  initial,
  onSend,
  onSkip,
  where,
}: {
  initial?: TurnFeedback | null;
  onSend: (reasons: string[], note?: string) => Promise<boolean>;
  onSkip: () => void;
  where?: FeedbackWhere | null;
}) {
  const { t } = useTranslation();
  const [reasons, setReasons] = useState<string[]>(initial?.rating === "down" ? initial.reasons : []);
  const [note, setNote] = useState(initial?.content?.note || "");
  const [failed, setFailed] = useState(false);
  const toggle = (r: string) => setReasons((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]));
  return (
    <div className="p3-ratecard" data-testid="rating-card">
      <b>{t("p3.fb.what_wrong")} <span className="text-faint font-normal">{t("p3.fb.optional")}</span></b>
      {/* T3-8 (a): reasons are zero or more of the five, so say so */}
      <div className="text-faint text-meta" data-testid="rating-pick-any">{t("p3.fb.pick_any")}</div>
      <div className="flex gap-1.5 flex-wrap my-2">
        {REASONS.map((r) => (
          <button key={r} className={"p3-pill" + (reasons.includes(r) ? " on" : "")} aria-pressed={reasons.includes(r)}
            data-testid={`rating-reason-${r}`} onClick={() => toggle(r)}>
            {t(`p3.fb.reason.${r}`)}
          </button>
        ))}
      </div>
      <textarea className="p3-note" placeholder={t("p3.fb.note_placeholder")} value={note} rows={2}
        data-testid="rating-note" onChange={(e) => setNote(e.target.value)} />
      <div className="flex gap-2 mt-2 items-center">
        <button className="btn primary" data-testid="rating-send" onClick={async () => setFailed(!(await onSend(reasons, note.trim() || undefined)))}>
          {t("p3.fb.send")}
        </button>
        <button className="btn" data-testid="rating-skip" onClick={onSkip}>{t("p3.fb.skip")}</button>
        <span className="text-faint ml-auto text-meta">
          {failed ? t("p3.fb.failed") : whereLine(where, t, true)}
        </span>
      </div>
    </div>
  );
}
