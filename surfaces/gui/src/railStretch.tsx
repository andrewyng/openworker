// UX-060: the right rail's full-width mode, for what it shows in its wide form (the trace,
// an artifact). The rail owns the state and remembers it per device; the panel inside
// renders the toggle — a stretch icon left of its ✕ (owner placement 2026-11-08).
import { createContext, useContext, useState } from "react";
import { Icon } from "./components/Icon";

export interface RailStretch {
  stretched: boolean;
  toggle: () => void;
}

export const RailStretchContext = createContext<RailStretch | null>(null);

/** The toggle, or null outside a rail (tests, a panel rendered on its own). */
export function useRailStretch(): RailStretch | null {
  return useContext(RailStretchContext);
}

const KEY = "ocw:rail-stretched";

export function useRailStretchState(): RailStretch {
  const [stretched, setStretched] = useState<boolean>(() => {
    try {
      return localStorage.getItem(KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggle = () =>
    setStretched((s) => {
      try {
        localStorage.setItem(KEY, s ? "0" : "1");
      } catch {
        /* no storage: the choice lasts the window */
      }
      return !s;
    });
  return { stretched, toggle };
}

export function StretchButton({ className, labels }: { className: string; labels: { stretch: string; unstretch: string } }) {
  const ctx = useRailStretch();
  if (!ctx) return null;
  return (
    <button
      className={className}
      data-testid="rail-stretch"
      aria-pressed={ctx.stretched}
      aria-label={ctx.stretched ? labels.unstretch : labels.stretch}
      title={ctx.stretched ? labels.unstretch : labels.stretch}
      onClick={ctx.toggle}
    >
      <Icon name={ctx.stretched ? "unstretch" : "stretch"} size={14} />
    </button>
  );
}
