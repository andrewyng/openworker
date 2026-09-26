import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

export interface Option {
  value: string;
  label: string;
  description?: string;
}

interface Props {
  prefix?: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  align?: "left" | "right";
  // Extra classes appended to the trigger pill (e.g. "chip" for a bordered composer-head chip).
  className?: string;
  // UX-048: the trigger may show a shorter label than the menu rows (model name without its
  // provider), carry its own tooltip, and lead with a small node (the context ring).
  displayLabel?: string;
  title?: string;
  leading?: ReactNode;
  // Rich hover card rendered ABOVE the trigger after a short delay (replaces the native
  // title, which is slow, bottom-anchored and unstyled). Hidden while the menu is open.
  tooltip?: ReactNode;
}

const TIP_DELAY_MS = 450;

export function Dropdown({
  prefix,
  value,
  options,
  onChange,
  align = "left",
  className,
  displayLabel,
  title,
  leading,
  tooltip,
}: Props) {
  const [open, setOpen] = useState(false);
  const [tip, setTip] = useState(false);
  const timer = useRef<number | null>(null);
  const armTip = () => {
    if (!tooltip) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setTip(true), TIP_DELAY_MS);
  };
  const disarmTip = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setTip(false);
  };
  useEffect(() => () => disarmTip(), []);
  const current = options.find((o) => o.value === value);
  const label = (prefix ? `${prefix}: ` : "") + (current?.label || value);
  return (
    <div className="dd" onMouseEnter={armTip} onMouseLeave={disarmTip}>
      {tip && !open && tooltip && (
        <div className={"dd-tip " + align} role="tooltip" data-testid="dd-tip">
          {tooltip}
        </div>
      )}
      <button
        className={"pill" + (className ? " " + className : "")}
        onClick={() => {
          disarmTip();
          setOpen((v) => !v);
        }}
        onFocus={armTip}
        onBlur={disarmTip}
        title={tooltip ? undefined : title ?? label}
      >
        {leading}
        <span className="pill-label">{displayLabel ?? label}</span>
        <Icon name="chevronDown" size={13} className="caret" />
      </button>
      {open && (
        <>
          <div className="dd-backdrop" onClick={() => setOpen(false)} />
          <div className={"dd-menu " + align}>
            {options.map((o) => (
              <div
                key={o.value}
                className={"dd-item" + (o.value === value ? " sel" : "")}
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
              >
                <div className="dd-label">
                  {o.label}
                  {o.value === value && <span className="chk">✓</span>}
                </div>
                {o.description && <div className="dd-desc">{o.description}</div>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
