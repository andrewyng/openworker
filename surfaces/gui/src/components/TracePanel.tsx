import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

export function TracePanel() {
  const [open, setOpen] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);

  useEffect(() => {
    const handleOpen = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail?.traceId) {
        setTraceId(customEvent.detail.traceId);
        setOpen(true);
      }
    };
    window.addEventListener("open-trace-panel", handleOpen);
    return () => window.removeEventListener("open-trace-panel", handleOpen);
  }, []);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-ink/20 z-40 transition-opacity"
        onClick={() => setOpen(false)}
      />
      
      {/* Slide-over Panel */}
      <div className="fixed top-0 right-0 h-full w-[500px] bg-panel border-l border-line shadow-2xl z-50 flex flex-col transform transition-transform translate-x-0">
        
        <header className="h-14 flex items-center justify-between px-4 border-b border-line shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-[14px] font-semibold">Subagent Trace</span>
            <span className="text-[10px] bg-line px-1.5 py-0.5 rounded text-muted font-mono">{traceId}</span>
          </div>
          <button className="text-faint hover:text-ink cursor-pointer p-1" onClick={() => setOpen(false)}>
            <Icon name="x" size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-2">System Prompt</h4>
            <div className="bg-paper border border-line rounded-lg p-3 text-[12px] font-mono overflow-x-auto text-faint">
              You are a subagent designed to generate a JSON schema from a CSV sample...
            </div>
          </section>

          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-2">Execution Log</h4>
            <div className="space-y-3">
              <div className="flex gap-2">
                <span className="text-accent text-[11px] mt-0.5"><Icon name="play" size={12}/></span>
                <div>
                  <div className="text-[13px] font-medium">Invoked by Orchestrator</div>
                  <div className="text-[11px] text-muted">0.0s</div>
                </div>
              </div>
              <div className="flex gap-2 opacity-50">
                <span className="text-faint text-[11px] mt-0.5"><Icon name="file" size={12}/></span>
                <div>
                  <div className="text-[13px]">Read lines 1-50 of dataset_v4.csv</div>
                  <div className="text-[11px] text-muted">0.2s</div>
                </div>
              </div>
              <div className="flex gap-2 opacity-50">
                <span className="text-faint text-[11px] mt-0.5"><Icon name="pencil" size={12}/></span>
                <div>
                  <div className="text-[13px]">Wrote schema.json (890 bytes)</div>
                  <div className="text-[11px] text-muted">1.4s</div>
                </div>
              </div>
              <div className="flex gap-2 opacity-50">
                <span className="text-ok text-[11px] mt-0.5"><Icon name="sparkle" size={12}/></span>
                <div>
                  <div className="text-[13px]">Returned to Orchestrator</div>
                  <div className="text-[11px] text-muted">1.5s</div>
                </div>
              </div>
            </div>
          </section>

          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-2">Final Output</h4>
            <div className="bg-paper border border-line rounded-lg p-3 text-[13px]">
              <Markdown text={'```json\n{\n  "type": "object",\n  "properties": {\n    "id": { "type": "string" }\n  }\n}\n```'} />
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
