import { useState, useEffect } from "react";
import { chooseFolder } from "../tauri";
import { Icon } from "./Icon";

export function AddFolderForm({
  onAdd,
  busy,
  compact,
  startOpen,
  onDismiss,
}: {
  onAdd: (path: string, writable: boolean) => Promise<boolean> | boolean | void;
  busy?: boolean;
  compact?: boolean;
  startOpen?: boolean;
  onDismiss?: () => void;
}) {
  const [open, setOpen] = useState(!!startOpen);
  const [path, setPath] = useState("");
  const [writable, setWritable] = useState(false);

  // Sync state if startOpen changes from parent
  useEffect(() => {
    if (startOpen !== undefined) {
      setOpen(startOpen);
    }
  }, [startOpen]);

  const reset = () => {
    setOpen(false);
    setPath("");
    setWritable(false);
    onDismiss?.();
  };

  const browse = async () => {
    try {
      const p = await chooseFolder();
      if (p) setPath(p);
    } catch (err) {
      console.error("Failed to choose folder:", err);
    }
  };

  const submit = async () => {
    const trimmedPath = path.trim();
    if (!trimmedPath || busy) return;

    try {
      const ok = await onAdd(trimmedPath, writable);
      if (ok !== false) reset();
    } catch (err) {
      console.error("Failed to add folder:", err);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submit();
  };

  if (!open) {
    return (
      <button
        type="button"
        className={`addfolder-trigger${compact ? " compact" : ""}`}
        onClick={() => setOpen(true)}
      >
        <Icon name="folderPlus" size={15} /> Give access to a folder
      </button>
    );
  }

  return (
    <form className="addfolder-form" onSubmit={handleSubmit}>
      <div className="addfolder-row">
        <input
          className="addfolder-path"
          autoFocus
          placeholder="Choose or paste a folder path…"
          value={path}
          spellCheck={false}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              reset();
            }
          }}
        />
        <button
          type="button"
          className="btn icon-only"
          onClick={browse}
          title="Choose location"
          aria-label="Choose location"
        >
          <Icon name="folder" size={15} />
        </button>
      </div>
      <div className="addfolder-actions">
        <label className="addfolder-write" title="Off = read-only. Tick to let the agent write here.">
          <input
            type="checkbox"
            checked={writable}
            onChange={(e) => setWritable(e.target.checked)}
          />
          Allow writes
        </label>
        <span className="spacer" />
        <button type="button" className="btn" onClick={reset}>
          Cancel
        </button>
        <button
          type="submit"
          className="btn primary"
          disabled={busy || !path.trim()}
        >
          Add
        </button>
      </div>
    </form>
  );
}
