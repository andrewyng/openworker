// Hover and keyboard-focus help for a label (observability F-6). A dotted underline says help
// is there; the text opens below the label (above it near the bottom of the window), drawn in
// a layer over the page so a scrolling panel never clips it.
import { useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const WIDTH = 280;

export function Help({ text, children }: { text?: string; children: ReactNode }) {
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  const [at, setAt] = useState<{ left: number; top: number; up: boolean } | null>(null);
  if (!text) return <>{children}</>;
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - WIDTH - 8));
    const up = r.bottom + 140 > window.innerHeight;
    setAt({ left, top: up ? r.top - 6 : r.bottom + 6, up });
  };
  const hide = () => setAt(null);
  return (
    <span
      ref={ref}
      className="help"
      tabIndex={0}
      aria-describedby={at ? id : undefined}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onKeyDown={(e) => e.key === "Escape" && hide()}
      data-testid="help"
    >
      {children}
      {at && createPortal(
        <span role="tooltip" id={id} className={"help-tip" + (at.up ? " up" : "")} style={{ left: at.left, top: at.top }} data-testid="help-tip">
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}
