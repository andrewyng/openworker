import { useState } from "react";
import {
  addOllamaEndpoint,
  deleteOllamaEndpoint,
  selectOllamaEndpoint,
  updateOllamaEndpoint,
  type OllamaEndpoint,
  type ProviderInfo,
} from "../api";
import { Toggle } from "../components/Toggle";

/** Manage multiple Ollama / local-inference endpoints under the single ollama provider. */
export function OllamaEndpoints({
  info,
  tp,
  onChanged,
  onDetect,
  detecting,
  detected,
}: {
  info: ProviderInfo;
  tp: string;
  onChanged: () => Promise<void>;
  onDetect: () => void;
  detecting: boolean;
  detected: boolean;
}) {
  const endpoints = info.endpoints || [];
  const selectedId = info.selected_endpoint_id || null;
  const [adding, setAdding] = useState(endpoints.length === 0);
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("http://localhost:11434");
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [busy, setBusy] = useState(false);

  const input =
    "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent";
  const labelCls = "block text-[12px] text-muted mt-3 mb-1";

  const run = async (fn: () => Promise<{ ok: boolean; error?: string }>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (!res.ok) {
        setError(res.error || "Something went wrong.");
        return;
      }
      await onChanged();
    } catch {
      setError("Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (ep: OllamaEndpoint) => {
    setEditingId(ep.id);
    setEditLabel(ep.label);
    setEditUrl(ep.base_url);
    setError(null);
  };

  return (
    <div data-testid={`${tp}-ollama-endpoints`}>
      <p className="text-[12px] text-muted mt-3 mb-2">
        Endpoints — switch between local machines or remote Ollama hosts. Models come from the
        selected endpoint only.
      </p>

      <ul className="space-y-2">
        {endpoints.map((ep) => {
          const selected = ep.id === selectedId;
          const editing = editingId === ep.id;
          return (
            <li
              key={ep.id}
              className={
                "rounded-lg border px-3 py-2 " +
                (selected ? "border-accent bg-accentSoft/40" : "border-line bg-panel")
              }
              data-testid={`${tp}-ollama-ep-${ep.id}`}
              data-selected={selected ? "true" : "false"}
              data-enabled={ep.enabled ? "true" : "false"}
            >
              {editing ? (
                <div className="space-y-2">
                  <div>
                    <label className={labelCls + " !mt-0"}>Nickname</label>
                    <input
                      className={input}
                      value={editLabel}
                      data-testid={`${tp}-ollama-edit-label`}
                      onChange={(e) => setEditLabel(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className={labelCls}>URL</label>
                    <input
                      className={input}
                      value={editUrl}
                      data-testid={`${tp}-ollama-edit-url`}
                      onChange={(e) => setEditUrl(e.target.value)}
                    />
                  </div>
                  <div className="flex gap-2 pt-1">
                    <button
                      className="px-3 py-1.5 rounded-lg border border-line text-[12.5px] font-medium hover:border-lineStrong disabled:opacity-40"
                      disabled={busy}
                      data-testid={`${tp}-ollama-edit-save`}
                      onClick={() =>
                        void run(async () => {
                          const res = await updateOllamaEndpoint(ep.id, {
                            label: editLabel,
                            base_url: editUrl,
                          });
                          if (res.ok) setEditingId(null);
                          return res;
                        })
                      }
                    >
                      Save
                    </button>
                    <button
                      className="px-3 py-1.5 rounded-lg text-[12.5px] text-muted hover:text-ink"
                      disabled={busy}
                      data-testid={`${tp}-ollama-edit-cancel`}
                      onClick={() => setEditingId(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[13.5px] font-medium text-ink truncate">
                        {ep.label}
                      </span>
                      {selected && (
                        <span
                          className="text-[10.5px] font-semibold uppercase tracking-wide text-accent bg-accentSoft rounded-full px-1.5 py-px"
                          data-testid={`${tp}-ollama-selected-badge`}
                        >
                          In use
                        </span>
                      )}
                      {!ep.enabled && (
                        <span className="text-[10.5px] font-semibold uppercase tracking-wide text-faint">
                          Disabled
                        </span>
                      )}
                    </div>
                    <div className="text-[12px] text-muted truncate mt-0.5" title={ep.base_url}>
                      {ep.base_url}
                    </div>
                    <div className="flex flex-wrap gap-2 mt-2">
                      {!selected && ep.enabled && (
                        <button
                          className="text-[12px] font-medium text-accent hover:underline disabled:opacity-40"
                          disabled={busy}
                          data-testid={`${tp}-ollama-select-${ep.id}`}
                          onClick={() => void run(() => selectOllamaEndpoint(ep.id))}
                        >
                          Use this
                        </button>
                      )}
                      <button
                        className="text-[12px] text-muted hover:text-ink disabled:opacity-40"
                        disabled={busy}
                        data-testid={`${tp}-ollama-edit-${ep.id}`}
                        onClick={() => startEdit(ep)}
                      >
                        Edit
                      </button>
                      <button
                        className="text-[12px] text-muted hover:text-warnInk disabled:opacity-40"
                        disabled={busy}
                        data-testid={`${tp}-ollama-delete-${ep.id}`}
                        onClick={() =>
                          void run(async () => {
                            if (editingId === ep.id) setEditingId(null);
                            return deleteOllamaEndpoint(ep.id);
                          })
                        }
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  <div className="shrink-0 pt-0.5" data-testid={`${tp}-ollama-toggle-${ep.id}`}>
                    <Toggle
                      checked={ep.enabled}
                      title={ep.enabled ? "Disable endpoint" : "Enable endpoint"}
                      disabled={busy}
                      onChange={(next) =>
                        void run(() => updateOllamaEndpoint(ep.id, { enabled: next }))
                      }
                    />
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {adding ? (
        <div className="mt-3 rounded-lg border border-line px-3 py-2" data-testid={`${tp}-ollama-add-form`}>
          <label className={labelCls + " !mt-0"}>Nickname</label>
          <input
            className={input}
            placeholder="My MacBook"
            value={label}
            data-testid={`${tp}-ollama-add-label`}
            onChange={(e) => setLabel(e.target.value)}
          />
          <label className={labelCls}>URL</label>
          <input
            className={input}
            placeholder="http://localhost:11434"
            value={url}
            data-testid={`${tp}-ollama-add-url`}
            onChange={(e) => setUrl(e.target.value)}
          />
          {/* Keep a hidden base_url field so legacy e2e / Detect paths that target
              set-field-base_url still resolve to the URL being added or the selected one. */}
          <input
            type="hidden"
            data-testid={`${tp}-field-base_url`}
            value={(selectedId && endpoints.find((e) => e.id === selectedId)?.base_url) || url}
            readOnly
          />
          <div className="flex gap-2 mt-3">
            <button
              className="px-3 py-1.5 rounded-lg border border-line text-[12.5px] font-medium hover:border-lineStrong disabled:opacity-40"
              disabled={busy}
              data-testid={`${tp}-ollama-add-save`}
              onClick={() =>
                void run(async () => {
                  const res = await addOllamaEndpoint({
                    label,
                    base_url: url,
                    enabled: true,
                    select: true,
                  });
                  if (res.ok) {
                    setLabel("");
                    setUrl("http://localhost:11434");
                    setAdding(false);
                  }
                  return res;
                })
              }
            >
              Add endpoint
            </button>
            {endpoints.length > 0 && (
              <button
                className="px-3 py-1.5 rounded-lg text-[12.5px] text-muted hover:text-ink"
                disabled={busy}
                data-testid={`${tp}-ollama-add-cancel`}
                onClick={() => {
                  setAdding(false);
                  setError(null);
                }}
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-3 flex-wrap">
          <button
            className="text-[12.5px] font-medium text-ink hover:underline"
            data-testid={`${tp}-ollama-add`}
            onClick={() => {
              setAdding(true);
              setError(null);
            }}
          >
            + Add endpoint
          </button>
          {/* Visible selected URL field for blur-save / Detect compatibility with older flows. */}
          <input
            type="hidden"
            data-testid={`${tp}-field-base_url`}
            value={
              (selectedId && endpoints.find((e) => e.id === selectedId)?.base_url) ||
              info.values?.base_url ||
              ""
            }
            readOnly
          />
        </div>
      )}

      <div className="flex gap-2 mt-4 items-center">
        <button
          className="px-4 py-2 rounded-lg border border-line text-[13px] font-medium text-ink hover:border-lineStrong shrink-0 disabled:opacity-40"
          onClick={onDetect}
          disabled={detecting || endpoints.length === 0}
          data-testid={`${tp}-test`}
        >
          {detecting ? "…" : "Detect"}
        </button>
        {detected && (
          <span
            className="text-[11px] font-medium text-ok bg-okSoft rounded-full px-2 py-0.5"
            data-testid={`${tp}-saved-pill`}
          >
            ✓ Detected
          </span>
        )}
      </div>

      <div className="mt-2 min-h-[19px] text-[12.5px]">
        {error && (
          <span className="text-warnInk" data-testid={`${tp}-ollama-error`}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
