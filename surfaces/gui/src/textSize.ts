// Text size (UX-048 follow-up, owner 2026-09-03): three steps for the whole type scale —
// Small (13px body), Default (14px body — owner's pick), Large (15px body). Per-device like the
// theme: localStorage only, applied as data-text-size on <html> so the token blocks in
// styles.css switch. index.html applies the same key pre-paint so the first frame is right.
import { useEffect, useState } from "react";

export type TextSize = "small" | "default" | "large";

const KEY = "openwork-text-size";
const PREF_EVENT = "openwork:text-size";

export function getTextSize(): TextSize {
  try {
    const v = localStorage.getItem(KEY);
    return v === "small" || v === "large" ? v : "default";
  } catch {
    return "default";
  }
}

function apply(size: TextSize) {
  if (size === "default") delete document.documentElement.dataset.textSize;
  else document.documentElement.dataset.textSize = size;
}

export function setTextSize(size: TextSize) {
  try {
    if (size === "default") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, size);
  } catch {
    /* private mode etc. — still applies for this session */
  }
  apply(size);
  window.dispatchEvent(new CustomEvent(PREF_EVENT));
}

/** Call once at startup. */
export function initTextSize() {
  apply(getTextSize());
}

/** The settings control's hook — stays in sync if the pref changes elsewhere. */
export function useTextSize(): [TextSize, (s: TextSize) => void] {
  const [size, setSize] = useState<TextSize>(getTextSize);
  useEffect(() => {
    const sync = () => setSize(getTextSize());
    window.addEventListener(PREF_EVENT, sync);
    return () => window.removeEventListener(PREF_EVENT, sync);
  }, []);
  return [size, setTextSize];
}
