import { useEffect, useRef, useState } from "react";
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
  // Opt-in combobox: the menu grows a filter box and any typed value can be committed, for
  // lists the server treats as suggestions rather than an allow-list (the model picker —
  // Settings already says "adding any model string works at your own risk"). Off by default,
  // so every other Dropdown keeps its select-only behavior.
  editable?: boolean;
  // Placeholder for that filter box (e.g. "Filter or type a model id…").
  editablePlaceholder?: string;
}

export function Dropdown({
  prefix,
  value,
  options,
  onChange,
  align = "left",
  className,
  editable = false,
  editablePlaceholder = "Filter or type a value…",
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // -1 = "commit exactly what's typed"; >=0 indexes the filtered list.
  const [active, setActive] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const current = options.find((o) => o.value === value);
  const label = (prefix ? `${prefix}: ` : "") + (current?.label || value);

  const typed = query.trim();
  const shown =
    editable && typed
      ? options.filter(
          (o) =>
            o.label.toLowerCase().includes(typed.toLowerCase()) ||
            o.value.toLowerCase().includes(typed.toLowerCase()),
        )
      : options;
  // Offer the raw text only when it isn't already one of the suggestions.
  const custom = editable && typed && !options.some((o) => o.value === typed) ? typed : "";

  useEffect(() => {
    if (open && editable) inputRef.current?.focus();
  }, [open, editable]);

  const close = () => {
    setOpen(false);
    setQuery("");
    setActive(-1);
  };

  const commit = (v: string) => {
    const next = v.trim();
    if (!next) return; // an empty box commits nothing
    onChange(next);
    close();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!shown.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      // -1 (the typed text) participates in the cycle only when there is typed text.
      const min = custom ? -1 : 0;
      const span = shown.length - min;
      setActive((i) => min + ((i - min + step + span) % span));
    } else if (e.key === "Enter") {
      e.preventDefault();
      commit(active >= 0 && shown[active] ? shown[active].value : query);
    } else if (e.key === "Escape") {
      // The composer binds Escape to Stop — an open picker must claim it first.
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "Tab") {
      close();
    }
  };

  return (
    <div className="dd">
      <button
        className={"pill" + (className ? " " + className : "")}
        onClick={() => (open ? close() : setOpen(true))}
        title={label}
        aria-haspopup={editable ? "listbox" : undefined}
        aria-expanded={editable ? open : undefined}
      >
        <span className="pill-label">{label}</span>
        <Icon name="chevronDown" size={13} className="caret" />
      </button>
      {open && (
        <>
          <div className="dd-backdrop" onClick={close} />
          <div className={"dd-menu " + align}>
            {editable && (
              <input
                ref={inputRef}
                className="dd-filter"
                role="combobox"
                aria-expanded="true"
                aria-autocomplete="list"
                aria-controls="dd-listbox"
                placeholder={editablePlaceholder}
                value={query}
                spellCheck={false}
                autoComplete="off"
                data-testid="dd-filter"
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(-1);
                }}
                onKeyDown={onKeyDown}
              />
            )}
            <div id="dd-listbox" role={editable ? "listbox" : undefined}>
              {shown.map((o, i) => (
                <div
                  key={o.value}
                  className={
                    "dd-item" + (o.value === value ? " sel" : "") + (i === active ? " active" : "")
                  }
                  role={editable ? "option" : undefined}
                  aria-selected={editable ? o.value === value : undefined}
                  onMouseEnter={() => editable && setActive(i)}
                  onClick={() => {
                    onChange(o.value);
                    close();
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
            {custom && (
              <div
                className={"dd-item dd-custom" + (active === -1 ? " active" : "")}
                role="option"
                aria-selected={false}
                onMouseEnter={() => setActive(-1)}
                onClick={() => commit(custom)}
                data-testid="dd-use-custom"
              >
                <div className="dd-label">Use “{custom}”</div>
                <div className="dd-desc">Not in the curated list — it must exist on the provider.</div>
              </div>
            )}
            {editable && !shown.length && !custom && (
              <div className="dd-desc" style={{ padding: "6px 10px" }}>
                No matches.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
