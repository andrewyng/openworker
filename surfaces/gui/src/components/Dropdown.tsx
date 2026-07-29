import { useState } from "react";
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
  // Optional per-item "make default" affordance (currently only the composer's model picker
  // uses this): `defaultValue` badges its row "default", every other row reveals a "Make
  // default" button on hover. Independent of `value`/`onChange` — the active model for THIS
  // chat and the app-wide default for NEW chats are separate concepts.
  defaultValue?: string;
  onMakeDefault?: (value: string) => void;
}

export function Dropdown({
  prefix,
  value,
  options,
  onChange,
  align = "left",
  className,
  defaultValue,
  onMakeDefault,
}: Props) {
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.value === value);
  const label = (prefix ? `${prefix}: ` : "") + (current?.label || value);
  return (
    <div className="dd">
      <button
        className={"pill" + (className ? " " + className : "")}
        onClick={() => setOpen((v) => !v)}
        title={label}
      >
        <span className="pill-label">{label}</span>
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
                  <span className="dd-label-text">{o.label}</span>
                  <span className="dd-label-right">
                    {o.value === value && <span className="chk">✓</span>}
                    {onMakeDefault &&
                      (o.value === defaultValue ? (
                        <span className="mlist-default">default</span>
                      ) : (
                        <button
                          type="button"
                          className="mlist-make dd-make"
                          onClick={(e) => {
                            e.stopPropagation();
                            onMakeDefault(o.value);
                          }}
                        >
                          Make default
                        </button>
                      ))}
                  </span>
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
