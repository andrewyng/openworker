import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

export interface Option {
  value: string;
  label: string;
  description?: string;
  // UX-055: the menu groups options under these headings, in first-seen order.
  group?: string;
  // A short right-aligned note on the row (context size, provider).
  meta?: string;
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
  // UX-055 (the model picker): a search box above the rows once there are this many
  // options or more, and a footer row under them (e.g. "Pick or configure a model…").
  searchFrom?: number;
  searchPlaceholder?: string;
  footer?: ReactNode;
  testId?: string;
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
  searchFrom,
  searchPlaceholder,
  footer,
  testId,
}: Props) {
  const [open, setOpen] = useState(false);
  const [tip, setTip] = useState(false);
  const [query, setQuery] = useState("");
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
  const q = query.trim().toLowerCase();
  const shown = q ? options.filter((o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)) : options;
  const groups = Array.from(new Set(shown.map((o) => o.group || "")));
  const showSearch = searchFrom !== undefined && options.length >= searchFrom;
  const row = (o: Option) => (
    <div
      key={o.value}
      className={"dd-item" + (o.value === value ? " sel" : "")}
      onClick={() => {
        onChange(o.value);
        setOpen(false);
        setQuery("");
      }}
      data-testid={testId ? `${testId}-option-${o.value}` : undefined}
    >
      <div className="dd-label">
        <span className="truncate">{o.label}</span>
        <span className="flex items-center gap-2 shrink-0 ml-3">
          {o.meta && <span className="dd-meta">{o.meta}</span>}
          {o.value === value && <span className="chk">✓</span>}
        </span>
      </div>
      {o.description && <div className="dd-desc">{o.description}</div>}
    </div>
  );
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
          <div className={"dd-menu " + align} data-testid={testId ? `${testId}-menu` : undefined}>
            {showSearch && (
              <input
                className="dd-search"
                autoFocus
                placeholder={searchPlaceholder}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                data-testid={testId ? `${testId}-search` : undefined}
              />
            )}
            <div className="dd-rows">
              {groups.map((g) => (
                <div key={g || "_"}>
                  {g && <div className="dd-group">{g}</div>}
                  {shown.filter((o) => (o.group || "") === g).map(row)}
                </div>
              ))}
            </div>
            {footer && <div className="dd-footer">{footer}</div>}
          </div>
        </>
      )}
    </div>
  );
}
