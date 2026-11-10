// T3-14: moving between turns in the Trace panel. "Turn N of M" opens a list of the session's
// turns with a search box (A), and a thin bar per turn sits under the picker (C). The keys (B)
// live on the panel. Everything shown or searched is what the GUI already holds: the recorded
// turns and the conversation. Nothing is fetched or sent for it.

import { useEffect, useMemo, useRef, useState } from "react";
import type { RecordedTurn } from "../api";
import { firstWords, turnMatches, type TurnText } from "../obs3";
import { formatTokens } from "../usage";

type T = (k: string, o?: any) => string;

export const turnTokens = (tn: RecordedTurn): number => Number(tn.usage?.pill || 0);
const warnOf = (tn: RecordedTurn): number => tn.issues?.warn || 0;
const issueTitles = (tn: RecordedTurn, t: T): string[] =>
  (tn.issue_checks || []).map((c) => t(`steps.issues.title.${c}`, { defaultValue: c }));

function startTime(ms: number): string {
  const d = new Date(ms);
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.toDateString() === today.toDateString()
    ? time
    : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

/** The list under "Turn N of M": a search box over the session's turns, newest last. */
export function TurnList({
  turns,
  current,
  texts,
  wakeOf,
  onPick,
  onClose,
  t,
}: {
  turns: RecordedTurn[];
  current: number;
  texts: Map<string, TurnText>;
  wakeOf: (tn: RecordedTurn) => string | null;
  onPick: (idx: number) => void;
  onClose: () => void;
  t: T;
}) {
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement | null>(null);
  const currentRow = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    currentRow.current?.scrollIntoView?.({ block: "nearest" });
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const shown = useMemo(
    () => turns.map((tn, i) => ({ tn, i })).filter(({ tn, i }) => turnMatches(i + 1, texts.get(tn.turn_id), issueTitles(tn, t), query)),
    [turns, texts, query, t],
  );
  const number = /^\d+$/.test(query.trim()) ? Number(query.trim()) : null;
  const pick = (i: number) => {
    onPick(i);
    onClose();
  };
  return (
    <div className="steps-turnlist" ref={box} data-testid="turn-list" role="dialog" aria-label={t("steps.turns.list_title")}>
      <input
        autoFocus
        className="steps-turnlist-search"
        placeholder={t("steps.turns.search")}
        value={query}
        data-testid="turn-list-search"
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          else if (e.key === "Enter" && shown.length > 0) pick(shown[0].i);
        }}
      />
      {number != null && (
        <div className="steps-turnlist-hint" data-testid="turn-list-hint">
          {number >= 1 && number <= turns.length ? t("steps.turns.jump_hint", { n: number }) : t("steps.turns.no_such", { total: turns.length })}
        </div>
      )}
      <div className="steps-turnlist-rows">
        {shown.length === 0 ? (
          number == null && <div className="steps-turnlist-empty" data-testid="turn-list-empty">{t("steps.turns.no_match")}</div>
        ) : (
          shown.map(({ tn, i }) => {
            const text = texts.get(tn.turn_id);
            const words = text?.user ? firstWords(text.user) : "";
            const wake = wakeOf(tn);
            const warn = warnOf(tn);
            return (
              <button
                key={tn.turn_id}
                ref={i === current ? currentRow : undefined}
                className={"steps-turnlist-row" + (i === current ? " current" : "")}
                aria-current={i === current}
                data-testid={`turn-list-row-${i + 1}`}
                onClick={() => pick(i)}
              >
                <span className="steps-turnlist-head">
                  <b>{t("steps.turns.turn_n", { n: i + 1 })}</b>
                  <span className="text-faint">{startTime(tn.started_at)}</span>
                  <span className="steps-sp" />
                  {warn > 0 && <span className="steps-badge warn">{t("steps.turns.issues", { count: warn })}</span>}
                  <span className="text-faint">{(tn.usage?.at_least ? "≥ " : "") + t("steps.turns.tokens", { tokens: formatTokens(turnTokens(tn)) })}</span>
                </span>
                <span className="steps-turnlist-words">{words || <span className="text-faint">{t("steps.turns.no_message")}</span>}</span>
                {wake && <span className="steps-turnlist-wake text-faint">{wake}</span>}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

/** One thin bar per turn: its height is the turn's tokens, amber when it has issues; the
 * current one is marked. Bars get thinner as turns add up and the strip scrolls past about
 * 120, so 200+ turns stay readable. */
export function TurnStrip({ turns, current, onPick, t }: { turns: RecordedTurn[]; current: number; onPick: (idx: number) => void; t: T }) {
  const max = Math.max(1, ...turns.map(turnTokens));
  const strip = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = strip.current?.querySelector<HTMLElement>(".current");
    el?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [current, turns.length]);
  return (
    <div className={"steps-strip" + (turns.length > 120 ? " dense" : "")} ref={strip} data-testid="turn-strip" aria-label={t("steps.turns.strip")}>
      {turns.map((tn, i) => {
        const tokens = turnTokens(tn);
        const warn = warnOf(tn);
        const label = t("steps.turns.bar", { n: i + 1, tokens: formatTokens(tokens) }) +
          (warn > 0 ? " · " + t("steps.turns.issues", { count: warn }) : "");
        return (
          <button
            key={tn.turn_id}
            className={"steps-strip-bar" + (warn > 0 ? " warn" : "") + (i === current ? " current" : "")}
            title={label}
            aria-label={label}
            aria-current={i === current}
            data-testid={`turn-strip-bar-${i + 1}`}
            onClick={() => onPick(i)}
          >
            <i style={{ height: `${Math.max(12, (tokens / max) * 100)}%` }} />
          </button>
        );
      })}
    </div>
  );
}
