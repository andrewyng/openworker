import { useState } from "react";
import type { TodoItem, SessionUsage } from "../types";
import { Icon } from "./Icon";

function formatNum(num: number): string {
  if (num >= 1000) return (num / 1000).toFixed(1) + "k";
  return num.toString();
}

interface Props {
  running: boolean;
  todo: TodoItem[];
  usage?: SessionUsage;
  model?: string;
  contextWindow?: number;
}

export function TelemetryTab({ running, todo, usage, model, contextWindow = 128000 }: Props) {
  const [localMode, setLocalMode] = useState(false);

  // Aggregate usage across all models in the session, or just pick the active one.
  const activeUsage = usage?.byModel[model || ""] || { input: 0, output: 0, cache_read: 0, cache_write: 0 };
  
  const inTokens = activeUsage.input || 0;
  const outTokens = activeUsage.output || 0;
  
  const maxIn = contextWindow;
  const maxOut = 8192; // standard typical max completion limit

  const inPercent = Math.min(100, Math.max(0, (inTokens / maxIn) * 100));
  const outPercent = Math.min(100, Math.max(0, (outTokens / maxOut) * 100));
  const activeContext = usage?.context || 0;
  const activeContextPercent = Math.min(100, Math.max(0, (activeContext / maxIn) * 100));

  return (
    <div className={`telemetry-tab p-5 space-y-6 ${localMode ? "local-mode" : "cloud-mode"}`}>
      
      <header className="flex justify-between items-center border-b border-line pb-4 relative">
        <h2 className="text-[14px] font-semibold">Telemetry</h2>
        
        {/* Toggle */}
        <div className="flex items-center gap-2 cursor-pointer" onClick={() => setLocalMode(!localMode)}>
          <span className="text-[10px] text-muted font-medium uppercase tracking-wide">Local LLM</span>
          <div className={`w-8 h-4 rounded-full relative transition-colors ${localMode ? 'bg-accent' : 'bg-line'}`}>
            <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-transform ${localMode ? 'translate-x-4' : 'translate-x-1'}`} />
          </div>
        </div>
      </header>

      {/* CLOUD MODE */}
      {!localMode && (
        <div className="space-y-6">
          <section>
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-[13px] font-semibold">Token Usage</h3>
              <span className="text-[11px] text-muted">Session</span>
            </div>
            <div className="bg-paper border border-line rounded-xl p-4">
              <div className="mb-4">
                <div className="flex justify-between text-[11px] mb-1">
                  <span className="text-muted">Prompt (In)</span>
                  <span className="font-mono">{formatNum(inTokens)} / {formatNum(maxIn)}</span>
                </div>
                <div className="w-full h-1.5 bg-line rounded-full overflow-hidden">
                  <div className="h-full bg-accent transition-all duration-500" style={{ width: `${inPercent}%` }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[11px] mb-1">
                  <span className="text-muted">Completion (Out)</span>
                  <span className="font-mono">{formatNum(outTokens)} / {formatNum(maxOut)}</span>
                </div>
                <div className="w-full h-1.5 bg-line rounded-full overflow-hidden">
                  <div className="h-full bg-teal-500 transition-all duration-500" style={{ width: `${outPercent}%` }}></div>
                </div>
              </div>
            </div>
          </section>

          <section>
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-[13px] font-semibold">API Latency</h3>
            </div>
            <div className="bg-paper border border-line rounded-xl p-4 h-32 flex items-end gap-1 justify-between">
              <div className="w-full bg-blue-500/20 hover:bg-blue-500/40 rounded-t-sm" style={{ height: "40%" }}></div>
              <div className="w-full bg-blue-500/20 hover:bg-blue-500/40 rounded-t-sm" style={{ height: "60%" }}></div>
              <div className="w-full bg-blue-500/20 hover:bg-blue-500/40 rounded-t-sm" style={{ height: "30%" }}></div>
              <div className="w-full bg-accent hover:bg-blue-400 rounded-t-sm relative group" style={{ height: "85%" }}>
                <div className="hidden group-hover:block absolute -top-8 left-1/2 -translate-x-1/2 bg-ink text-paper text-[10px] px-2 py-1 rounded">850ms</div>
              </div>
              <div className="w-full bg-blue-500/20 hover:bg-blue-500/40 rounded-t-sm" style={{ height: "45%" }}></div>
              <div className="w-full bg-blue-500/20 hover:bg-blue-500/40 rounded-t-sm" style={{ height: "50%" }}></div>
            </div>
          </section>
        </div>
      )}

      {/* LOCAL MODE */}
      {localMode && (
        <div className="space-y-6">
          <section>
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-[13px] font-semibold">Context Saturation</h3>
              <span className="text-[11px] text-muted">VRAM Limit</span>
            </div>
            <div className="bg-paper border border-line rounded-xl p-4">
              <div className="mb-4">
                <div className="flex justify-between text-[11px] mb-1">
                  <span className="text-muted">Active Context</span>
                  <span className="font-mono text-warnInk">{formatNum(activeContext)} / {formatNum(maxIn)}</span>
                </div>
                <div className="w-full h-1.5 bg-line rounded-full overflow-hidden">
                  <div className="h-full bg-warnInk transition-all duration-500" style={{ width: `${activeContextPercent}%` }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[11px] mb-1">
                  <span className="text-muted">System VRAM</span>
                  <span className="font-mono">14.2GB / 16.0GB</span>
                </div>
                <div className="w-full h-1.5 bg-line rounded-full overflow-hidden">
                  <div className="h-full bg-purple-500 w-[88%]"></div>
                </div>
              </div>
            </div>
          </section>

          <section>
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-[13px] font-semibold">Inference Speed</h3>
              <span className="text-[11px] text-ok">32.4 TPS</span>
            </div>
            <div className="bg-paper border border-line rounded-xl p-4 h-32 flex items-end gap-1 justify-between">
              <div className="w-full bg-green-500/20 rounded-t-sm" style={{ height: "30%" }}></div>
              <div className="w-full bg-green-500/20 rounded-t-sm" style={{ height: "35%" }}></div>
              <div className="w-full bg-green-500/20 rounded-t-sm" style={{ height: "32%" }}></div>
              <div className="w-full bg-ok hover:bg-green-400 rounded-t-sm relative group" style={{ height: "60%" }}>
                <div className="hidden group-hover:block absolute -top-8 left-1/2 -translate-x-1/2 bg-ink text-paper text-[10px] px-2 py-1 rounded">32 TPS</div>
              </div>
              <div className="w-full bg-green-500/20 rounded-t-sm" style={{ height: "62%" }}></div>
              <div className="w-full bg-green-500/20 rounded-t-sm" style={{ height: "58%" }}></div>
            </div>
          </section>
        </div>
      )}

      {/* Active Context Explorer */}
      <section>
        <div className="flex justify-between items-center mb-3">
          <h3 className="text-[13px] font-semibold">Active Context</h3>
        </div>
        <div className="space-y-2 border border-line rounded-xl p-2 bg-paper">
          <div className="flex items-center gap-3 p-2 rounded-lg hover:bg-panel cursor-pointer group">
            <div className="w-6 h-6 rounded bg-line grid place-items-center text-[10px]">📄</div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] truncate">dataset_v4.csv</div>
              <div className="text-[11px] text-faint">12 KB</div>
            </div>
            <button className="hidden group-hover:block text-faint hover:text-danger"><Icon name="x" size={12} /></button>
          </div>
        </div>
      </section>
      
      {/* Task Progress (Moved from Progress Accordion) */}
      <section className="pt-4 border-t border-line">
         <h3 className="text-[13px] font-semibold mb-3">Current Task</h3>
         {todo.length > 0 ? (
           <div className="rail-todo-list text-[13px]">
             {todo.map((item, index) => (
               <div className={"rail-todo " + item.status} key={index}>
                 <span className="rail-todo-mark" />
                 <span>{item.content}</span>
               </div>
             ))}
           </div>
         ) : (
           <div className="rail-muted text-[13px]">
             {running ? "Working..." : "No active task plan."}
           </div>
         )}
      </section>

    </div>
  );
}
