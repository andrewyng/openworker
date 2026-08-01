import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import type { ToolItem } from "../types";

export function TracePanel() {
  const [open, setOpen] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [tool, setTool] = useState<ToolItem | null>(null);

  useEffect(() => {
    const handleOpen = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail?.traceId) {
        setTraceId(customEvent.detail.traceId);
        setTool(customEvent.detail.tool || null);
        setOpen(true);
      }
    };
    window.addEventListener("open-trace-panel", handleOpen);
    return () => window.removeEventListener("open-trace-panel", handleOpen);
  }, []);

  if (!open) return null;

  let report = "No output available.";
  let note = "";
  let reasoning = "";
  let toolLog: any[] = [];
  if (tool?.preview) {
    try {
      const parsed = JSON.parse(tool.preview);
      report = parsed.report || report;
      note = parsed.note || note;
      reasoning = parsed.reasoning || "";
      toolLog = parsed.tool_log || [];
    } catch {
      report = tool.preview;
    }
  } else if (tool?.status === "…") {
    report = "Subagent is currently running...";
  }

  return (
    <>
      <div 
        style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 40 }}
        onClick={() => setOpen(false)}
      />
      
      <div className={`trace-panel-slide ${open ? 'open' : ''}`}>
        <header className="trace-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-main)' }}>Subagent Trace</span>
            <span style={{ fontSize: '11px', background: 'var(--bg-panel)', padding: '2px 6px', borderRadius: '4px', fontFamily: 'var(--font-mono)', border: '1px solid var(--border-light)' }}>
              {traceId || tool?.id}
            </span>
          </div>
          <button 
            style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: '4px' }} 
            onClick={() => setOpen(false)}
            title="Close panel"
          >
            <Icon name="x" size={16} />
          </button>
        </header>

        <div className="trace-content">
          <section style={{ marginBottom: '24px' }}>
            <h4 className="trace-label">Task (System Prompt)</h4>
            <div className="trace-block" style={{ color: 'var(--text-muted)' }}>
              {tool?.args?.task || "No task provided."}
            </div>
          </section>

          {reasoning && (
            <section style={{ marginBottom: '24px' }}>
              <h4 className="trace-label">Internal Reasoning</h4>
              <div className="trace-block" style={{ fontSize: '13px', color: 'var(--text-muted)', background: 'var(--bg-panel)', padding: '12px', borderRadius: '6px', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                {reasoning}
              </div>
            </section>
          )}

          <section style={{ marginBottom: '24px' }}>
            <h4 className="trace-label">Execution Log</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', gap: '8px' }}>
                <span style={{ color: 'var(--accent)', marginTop: '2px' }}><Icon name="play" size={12}/></span>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-main)' }}>Invoked by Orchestrator</div>
                </div>
              </div>
              
              {toolLog.length > 0 ? (
                toolLog.map((logItem: any, index: number) => {
                  const isDone = logItem.status === "completed" || logItem.status === "done" || logItem.status === "success";
                  const iconName: "file" | "wrench" = logItem.name.includes("file") || logItem.name.includes("grep") || logItem.name.includes("search") ? "file" : "wrench";
                  const iconColor = isDone ? "var(--ok)" : "var(--accent)";
                  return (
                    <div style={{ display: 'flex', gap: '8px' }} key={index}>
                      <span style={{ color: iconColor, marginTop: '2px' }}>
                        <Icon name={iconName} size={12}/>
                      </span>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-main)' }}>
                          Run {logItem.name}
                        </div>
                        {logItem.args && Object.keys(logItem.args).length > 0 && (
                          <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
                            {JSON.stringify(logItem.args)}
                          </div>
                        )}
                        {logItem.preview && (
                          <div style={{ fontSize: '11px', color: 'var(--text-faint)', marginTop: '4px', background: 'var(--bg-panel)', padding: '4px 8px', borderRadius: '4px', border: '1px solid var(--border-light)', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', overflowX: 'auto' }}>
                            {logItem.preview.length > 300 ? logItem.preview.substring(0, 300) + "..." : logItem.preview}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div style={{ display: 'flex', gap: '8px', opacity: 0.7 }}>
                  <span style={{ color: 'var(--text-faint)', marginTop: '2px' }}><Icon name="file" size={12}/></span>
                  <div>
                    <div style={{ fontSize: '13px' }}>Executing trace details securely stored on backend.</div>
                  </div>
                </div>
              )}

              {tool?.status !== "…" && (
                <div style={{ display: 'flex', gap: '8px', opacity: 0.7 }}>
                  <span style={{ color: 'var(--ok)', marginTop: '2px' }}><Icon name="sparkle" size={12}/></span>
                  <div>
                    <div style={{ fontSize: '13px' }}>Returned to Orchestrator</div>
                  </div>
                </div>
              )}
            </div>
          </section>

          <section>
            <h4 className="trace-label">Final Output</h4>
            {note && <div style={{ fontSize: '12px', color: 'var(--warn)', marginBottom: '8px', background: 'rgba(210, 153, 34, 0.1)', padding: '8px', borderRadius: '4px' }}>{note}</div>}
            <div className="trace-block" style={{ fontSize: '13px', color: 'var(--text-main)', lineHeight: 1.5 }}>
              <Markdown text={report} />
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
