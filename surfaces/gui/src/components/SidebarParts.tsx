import { useState } from "react";
import type { Persona } from "../api";
import type { SessionInfo } from "../types";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { showPersonas } from "../flags";
import { shortPersonaName } from "../personaScope";
import { Icon } from "./Icon";
import { PersonaGlyph } from "./personaIcon";

export function AttnBadge({ n }: { n: number }) {
  if (!n) return null;
  return (
    <span
      className="text-[10px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
      title={`${n} awaiting your attention`}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

export function UnseenBadge({ n, failed }: { n: number; failed?: boolean }) {
  if (!n) return null;
  return (
    <span
      className="text-[10px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
      title={failed ? `${n} new run${n > 1 ? "s" : ""} — the latest failed` : `${n} new run${n > 1 ? "s" : ""}`}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

export function LiveDot({ state }: { state?: "working" | "sleeping" | "idle" }) {
  if (state !== "working" && state !== "sleeping") return null;
  return state === "working" ? (
    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" title="Working now" />
  ) : (
    <span className="w-1.5 h-1.5 rounded-full bg-faint/60 shrink-0" title="Sleeping (will wake itself)" />
  );
}

export function OriginIcon({ s }: { s: SessionInfo }) {
  if (s.origin !== "slack") return null;
  return (
    <ConnectorIcon
      connector={{ logo: "slack", brand_color: "#611f69" }}
      size={12}
      title={s.origin_label || "From Slack"}
    />
  );
}

export function ConnectorDot({ subs }: { subs?: string[] }) {
  if (!subs || subs.length === 0) return null;
  return <span className="w-1.5 h-1.5 rounded-full bg-faint shrink-0" data-brand={subs[0]} title={subs.join(", ")} />;
}

export function NewSessionSplit({
  personas,
  current,
  onNew,
  onManage,
}: {
  personas: Persona[] | null;
  current: string;
  onNew: (agent: string) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const enabled = (personas || []).filter((persona) => persona.enabled);
  const solo = personas !== null && enabled.length <= 1;

  return (
    <div className="px-3 pt-2 relative">
      <div className="flex">
        <button
          className={
            "newsplit-primary flex-1 text-left px-3 py-2 bg-accent text-white text-[13px] font-medium hover:opacity-95 flex items-center gap-2 " +
            (solo ? "rounded-lg" : "rounded-l-lg")
          }
          onClick={() => onNew(solo && enabled.length === 1 ? enabled[0].id : current)}
        >
          <Icon name="plus" size={15} className="shrink-0" /> New session
        </button>
        {!solo && (
          <button
            className="px-2.5 rounded-r-lg bg-accent text-white border-l border-white/25 hover:opacity-95 flex items-center"
            title="Start with a specific persona"
            aria-label="Choose a persona"
            onClick={() => setOpen((value) => !value)}
          >
            <Icon name="chevronDown" size={13} />
          </button>
        )}
      </div>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="newsplit-menu absolute left-3 right-3 mt-1 z-30 bg-panel border border-line rounded-xl2 shadow-xl p-1">
            <div className="px-2 py-1 text-[10.5px] uppercase tracking-[0.06em] text-faint font-semibold">
              Start a session as
            </div>
            {enabled.map((persona) => (
              <button
                key={persona.id}
                className="w-full flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-paper text-left"
                onClick={() => {
                  setOpen(false);
                  onNew(persona.id);
                }}
              >
                <span className="w-6 h-6 rounded-md bg-paper border border-line grid place-items-center text-muted shrink-0">
                  <PersonaGlyph icon={persona.icon} family={persona.family} size={12} />
                </span>
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium truncate">
                    {shortPersonaName(persona.name, persona.id)}
                  </span>
                  {persona.tagline && <span className="block text-[11px] text-muted truncate">{persona.tagline}</span>}
                </span>
              </button>
            ))}
            {showPersonas() && (
              <div className="border-t border-line mt-1 pt-1">
                <button
                  className="w-full px-2 py-1.5 rounded-lg hover:bg-paper text-left text-[12.5px] text-muted"
                  onClick={() => {
                    setOpen(false);
                    onManage();
                  }}
                >
                  Manage personas…
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
