import { useEffect, useState } from "react";
import {
  getComputerUseSettings,
  setComputerUseSettings,
  type ComputerUseProgram,
  type ComputerUseSettings,
} from "../api";
import { pickProgram, platformOS } from "../tauri";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { Toggle } from "./Toggle";

const CARD = "rounded-xl2 border border-line bg-panel";
const BTN_ACCENT =
  "text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40";

const programName = (path: string) => {
  const filename = path.split(/[\\/]/).pop() || "Program";
  return filename.replace(/\.exe$/i, "") || "Program";
};

function ProgramRow({
  program,
  removable,
  onRemove,
}: {
  program: ComputerUseProgram;
  removable?: boolean;
  onRemove?: () => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-line bg-paper p-3.5">
      <div className="mt-0.5 rounded-md border border-line bg-panel p-1.5 text-muted">
        <Icon name="wrench" size={15} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium text-ink">{program.name}</span>
          <span className={`text-[11px] ${program.available ? "text-accent" : "text-danger"}`}>
            {program.available ? "Available" : "Not found"}
          </span>
        </div>
        <code className="mt-1 block break-all text-[11.5px] text-muted">{program.path}</code>
      </div>
      {removable ? (
        <button
          type="button"
          aria-label={`Remove ${program.name}`}
          title={`Remove ${program.name}`}
          className="rounded-md p-1.5 text-muted hover:bg-panel hover:text-danger"
          onClick={onRemove}
        >
          <Icon name="trash" size={15} />
        </button>
      ) : (
        <span className="text-[11px] text-muted">Built in</span>
      )}
    </div>
  );
}

export function ComputerUseSection() {
  const [settings, setSettings] = useState<ComputerUseSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [programs, setPrograms] = useState<ComputerUseProgram[]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const apply = (next: ComputerUseSettings) => {
    setSettings(next);
    setEnabled(next.enabled);
    setPrograms(next.allowed_programs);
  };

  useEffect(() => {
    let active = true;
    void getComputerUseSettings()
      .then((next) => {
        if (active) apply(next);
      })
      .catch((error) => {
        if (active) {
          setMessage(error instanceof Error ? error.message : "Could not load Computer use settings.");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const addProgram = async () => {
    setMessage("");
    const path = await pickProgram();
    if (!path) return;
    if (programs.some((program) => program.path.toLocaleLowerCase() === path.toLocaleLowerCase())) {
      setMessage("That program is already allowed.");
      return;
    }
    setPrograms((current) => [
      ...current,
      { name: programName(path), path, available: true },
    ]);
  };

  const save = async () => {
    setSaving(true);
    setMessage("");
    try {
      const next = await setComputerUseSettings({
        enabled,
        allowed_programs: programs.map(({ name, path }) => ({ name, path })),
      });
      if (!next.ok) throw new Error(next.error || "Could not save Computer use settings.");
      apply(next);
      setMessage(
        next.reload_warning
          ? `Saved. The driver will retry the allowlist on the next action: ${next.reload_warning}`
          : "Saved. New sessions use this program allowlist immediately.",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save Computer use settings.");
    } finally {
      setSaving(false);
    }
  };

  const supported = settings?.supported ?? platformOS() === "windows";

  return (
    <section>
      <PanelHead
        title="Computer use"
        sub="Choose which local Windows programs OpenWorker may see and control. Programs outside this list are filtered before the model receives window data."
      />

      <div className={`${CARD} p-5 space-y-5`}>
        <div className="flex items-start justify-between gap-5">
          <div>
            <div className="text-[13px] font-medium text-ink">Allow local computer use</div>
            <p className="mt-1 text-[12px] leading-relaxed text-muted">
              Window reads are restricted to the executable paths below. Opening programs and every input action require approval.
            </p>
          </div>
          <Toggle checked={enabled} onChange={setEnabled} disabled={!supported} title="Allow local computer use" />
        </div>
        {!supported ? (
          <p className="rounded-lg border border-line bg-paper p-3 text-[12px] text-muted">
            Program control is available in the Windows desktop build.
          </p>
        ) : null}
      </div>

      <div className={`${CARD} mt-4 p-5`}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-[13px] font-medium text-ink">Allowed programs</div>
            <p className="mt-1 text-[12px] leading-relaxed text-muted">
              Select an installed .exe. Command interpreters and other programs that could bypass this boundary are rejected.
            </p>
          </div>
          <button type="button" className={BTN_BORDERED} disabled={!supported} onClick={addProgram}>
            Add program…
          </button>
        </div>
        <div className="mt-4 space-y-2">
          {programs.length ? (
            programs.map((program) => (
              <ProgramRow
                key={program.path.toLocaleLowerCase()}
                program={program}
                removable
                onRemove={() => setPrograms((current) => current.filter((item) => item.path !== program.path))}
              />
            ))
          ) : (
            <p className="rounded-lg border border-dashed border-line p-4 text-[12px] text-muted">
              No optional programs are allowed yet.
            </p>
          )}
        </div>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button type="button" className={BTN_ACCENT} disabled={!settings || saving || !supported} onClick={save}>
          {saving ? "Saving…" : "Save"}
        </button>
        {message ? <span className="text-[12px] text-muted">{message}</span> : null}
      </div>
    </section>
  );
}
