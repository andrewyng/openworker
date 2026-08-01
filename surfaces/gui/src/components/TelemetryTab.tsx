import { useState } from "react";
import type { TodoItem, SessionUsage, Item, ToolItem } from "../types";
import type { ArtifactInfo } from "../api";
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
  artifacts: ArtifactInfo[];
  onSelectArtifact?: (artifact: ArtifactInfo) => void;
  items?: Item[];
}

function kindIcon(kind: string): "file" | "fileCode" | "image" | "table" {
  if (kind === "image") return "image";
  if (kind === "html" || kind === "code") return "fileCode";
  if (kind === "csv" || kind === "sheet") return "table";
  return "file";
}

// Generate SVG polyline points from a numeric series (0-40 Y range, 0-100 X range).
function linePoints(values: number[], normalizeMax: number): string {
  if (!values.length) return "";
  const max = Math.max(normalizeMax, ...values) || 1;
  const stepX = values.length > 1 ? 100 / (values.length - 1) : 50;
  return values
    .map((v, i) => {
      const x = i === 0 && values.length === 1 ? 50 : i * stepX;
      const y = 40 - (v / max) * 35; // leave 5 units margin bottom
      return `${x},${y}`;
    })
    .join(" ");
}

// Status color tokens matching SwarmGraph agent card coloring.
const STATUS_COLORS = {
  done: "var(--ok)",
  running: "var(--accent)",
  queued: "var(--faint)",
};

// Callsign helper matching SwarmGraph.
function callsignFor(call?: ToolItem, index = 0): string {
  if (!call) return `Agent ${index + 1}`;
  const t = String(call.args?.task || "").toLowerCase();
  if (t.includes("search") || t.includes("find") || t.includes("explore") || t.includes("research")) return "Scout";
  if (t.includes("build") || t.includes("implement") || t.includes("write code") || t.includes("create") || t.includes("generate")) return "Forge";
  if (t.includes("analyze") || t.includes("review") || t.includes("examine") || t.includes("audit")) return "Lens";
  if (t.includes("test") || t.includes("verify") || t.includes("check")) return "Probe";
  if (t.includes("plan") || t.includes("design") || t.includes("architect")) return "Map";
  if (t.includes("deploy") || t.includes("release") || t.includes("ship")) return "Launch";
  return `Agent ${index + 1}`;
}

// Generate sample latency/TPS series per subagent for multi-series charts.
// In production, replace with real backend data keyed by subagent ID.
function generateSeries(count: number, min: number, max: number, length = 6): number[][] {
  const series: number[][] = [];
  for (let i = 0; i < count; i++) {
    series.push(
      Array.from({ length }, () => {
        const base = min + Math.random() * (max - min);
        const jitter = (Math.random() - 0.5) * (max - min) * 0.3;
        return Math.max(0, base + jitter);
      }),
    );
  }
  return series;
}

export function TelemetryTab({ running, todo, usage, model, contextWindow = 128000, artifacts, onSelectArtifact, items = [] }: Props) {
  // Aggregate usage across all models in the session.
  const activeUsage = usage?.byModel[model || ""] || { input: 0, output: 0, cache_read: 0, cache_write: 0 };
  const inTokens = activeUsage.input || 0;
  const outTokens = activeUsage.output || 0;

  const maxIn = contextWindow;
  const maxOut = 8192;

  const inPercent = Math.min(100, Math.max(0, (inTokens / maxIn) * 100));
  const outPercent = Math.min(100, Math.max(0, (outTokens / maxOut) * 100));
  const activeContext = usage?.context || 0;
  const activeContextPercent = Math.min(100, Math.max(0, (activeContext / maxIn) * 100));

  // Extract actual subagents from session items
  const subagents = items.filter(
    (i) => i.kind === "tool" && (i.name === "delegate" || i.name === "explore")
  ) as ToolItem[];
  const subagentCount = subagents.length;

  const latencySeries = generateSeries(subagentCount, 200, 900);
  const tpsSeries = generateSeries(subagentCount, 12, 45);

  // Sample p95 latency from the series.
  const allLatencies = latencySeries.flat();
  const p95Latency =
    allLatencies.length > 0
      ? Math.round(allLatencies.slice().sort((a, b) => b - a)[Math.floor(allLatencies.length * 0.05)] || 0)
      : 0;

  // Current TPS readout.
  const currentTps = tpsSeries.length > 0 ? (tpsSeries[0][tpsSeries[0].length - 1] || 32).toFixed(1) : "0";

  return (
    <div className="telemetry-tab p-5 space-y-6">
      <header className="flex justify-between items-center border-b border-line pb-4">
        <h2 className="text-[14px] font-semibold">Telemetry</h2>
        {running && (
          <span className="flex items-center gap-1.5 text-[11px] text-accent">
            <span className="inline-block w-[6px] h-[6px] rounded-full bg-accent animate-pulse"></span>
            Live
          </span>
        )}
      </header>

      {/* CLOUD SECTION */}
      <section className="bg-paper border border-line rounded-xl p-4 flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <span className="inline-block w-[6px] h-[6px] rounded-full bg-accent shrink-0"></span>
          <span className="text-[12.5px] font-semibold">Cloud — {model || "—"}</span>
        </div>

        {/* Token usage bars */}
        <div>
          <div className="flex justify-between text-[11px] mb-1">
            <span className="text-muted">Prompt (In)</span>
            <span className="font-mono">{formatNum(inTokens)} / {formatNum(maxIn)}</span>
          </div>
          <div className="w-full h-[6px] bg-line rounded-full overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-500"
              style={{ width: `${inPercent}%` }}
            ></div>
          </div>
        </div>

        <div>
          <div className="flex justify-between text-[11px] mb-1">
            <span className="text-muted">Completion (Out)</span>
            <span className="font-mono">{formatNum(outTokens)} / {formatNum(maxOut)}</span>
          </div>
          <div className="w-full h-[6px] bg-line rounded-full overflow-hidden">
            <div
              className="h-full bg-teal-500 transition-all duration-500"
              style={{ width: `${outPercent}%` }}
            ></div>
          </div>
        </div>

        {/* Multi-series latency line chart */}
        <div>
          <div className="flex justify-between text-[11px] mb-1.5">
            <span className="text-muted">API latency by agent</span>
            {subagentCount > 0 && <span className="font-mono">{p95Latency}ms p95</span>}
          </div>
          {subagentCount > 0 ? (
            <>
              <svg
                width="100%"
                height="56"
                viewBox="0 0 100 40"
                preserveAspectRatio="none"
                style={{ display: "block" }}
              >
                {latencySeries.map((series, i) => {
                  const subagent = subagents[i];
                  const status = subagent?.status === "…" ? "running" : "done";
                  return (
                    <polyline
                      key={i}
                      points={linePoints(series, 1000)}
                      fill="none"
                      stroke={STATUS_COLORS[status] || "var(--faint)"}
                      strokeWidth="1.6"
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
              </svg>
              <div className="flex gap-3 mt-2 flex-wrap">
                {subagents.map((call, i) => {
                  const status = call.status === "…" ? "running" : "done";
                  return (
                    <span key={i} className="flex items-center gap-1 text-[10.5px] text-muted">
                      <span
                        className="inline-block w-[8px] h-[2px]"
                        style={{ backgroundColor: STATUS_COLORS[status] || "var(--faint)" }}
                      ></span>
                      {callsignFor(call, i)}
                    </span>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="text-[11px] text-faint py-2 text-center bg-panel rounded border border-line">
              No active subagents in this session
            </div>
          )}
        </div>
      </section>

      {/* LOCAL SECTION */}
      <section className="bg-paper border border-line rounded-xl p-4 flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <span className="inline-block w-[6px] h-[6px] rounded-full bg-purple-500 shrink-0"></span>
          <span className="text-[12.5px] font-semibold">Local — llama-3.1-70b (Ollama)</span>
        </div>

        {/* Context saturation */}
        <div>
          <div className="flex justify-between text-[11px] mb-1">
            <span className="text-muted">Active context</span>
            <span className="font-mono text-warnInk">{formatNum(activeContext)} / {formatNum(maxIn)}</span>
          </div>
          <div className="w-full h-[6px] bg-line rounded-full overflow-hidden">
            <div
              className="h-full bg-warnInk transition-all duration-500"
              style={{ width: `${activeContextPercent}%` }}
            ></div>
          </div>
        </div>

        {/* VRAM */}
        <div>
          <div className="flex justify-between text-[11px] mb-1">
            <span className="text-muted">System VRAM</span>
            <span className="font-mono">14.2GB / 16.0GB</span>
          </div>
          <div className="w-full h-[6px] bg-line rounded-full overflow-hidden">
            <div className="h-full bg-purple-500" style={{ width: "88%" }}></div>
          </div>
        </div>

        {/* Multi-series TPS line chart */}
        <div>
          <div className="flex justify-between text-[11px] mb-1.5">
            <span className="text-muted">Inference speed by agent</span>
            {subagentCount > 0 && <span className="font-mono text-ok">{currentTps} TPS</span>}
          </div>
          {subagentCount > 0 ? (
            <>
              <svg
                width="100%"
                height="56"
                viewBox="0 0 100 40"
                preserveAspectRatio="none"
                style={{ display: "block" }}
              >
                {tpsSeries.map((series, i) => {
                  const subagent = subagents[i];
                  const status = subagent?.status === "…" ? "running" : "done";
                  return (
                    <polyline
                      key={i}
                      points={linePoints(series, 50)}
                      fill="none"
                      stroke={STATUS_COLORS[status] || "var(--faint)"}
                      strokeWidth="1.6"
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
              </svg>
              <div className="flex gap-3 mt-2 flex-wrap">
                {subagents.map((call, i) => {
                  const status = call.status === "…" ? "running" : "done";
                  return (
                    <span key={i} className="flex items-center gap-1 text-[10.5px] text-muted">
                      <span
                        className="inline-block w-[8px] h-[2px]"
                        style={{ backgroundColor: STATUS_COLORS[status] || "var(--faint)" }}
                      ></span>
                      {callsignFor(call, i)}
                    </span>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="text-[11px] text-faint py-2 text-center bg-panel rounded border border-line">
              No active subagents in this session
            </div>
          )}
        </div>
      </section>

      {/* Active Context */}
      <section>
        <div className="flex justify-between items-center mb-2">
          <h3 className="text-[13px] font-semibold">Active Context ({artifacts.length})</h3>
        </div>
        <div className="space-y-1 border border-line rounded-xl p-2 bg-paper">
          {artifacts.length === 0 ? (
            <div className="text-[12px] text-faint p-2 text-center">No active context files.</div>
          ) : (
            artifacts.map((a, i) => (
              <div
                key={i}
                className="flex items-center gap-3 p-2 rounded-lg hover:bg-panel cursor-pointer group"
                title={a.path}
                onClick={() => onSelectArtifact?.(a)}
              >
                <div className="w-6 h-6 rounded bg-line grid place-items-center text-muted flex-shrink-0">
                  <Icon name={kindIcon(a.kind)} size={13} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] truncate">{a.path.split("/").pop()}</div>
                  <div className="text-[11px] text-faint">
                    {a.size !== undefined ? (a.size / 1024).toFixed(1) + " KB" : "Unknown size"}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      {/* Task Progress */}
      <section className="pt-3 border-t border-line">
        <h3 className="text-[13px] font-semibold mb-2">Current Task</h3>
        {todo.length > 0 ? (
          <div className="rail-todo-list text-[13px]">
            {todo.map((item, index) => (
              <div className={"rail-todo " + item.status} key={index}>
                <span className="rail-todo-mark"></span>
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

      {/* Task History Section */}
      <TaskHistorySection items={items} />
    </div>
  );
}

function TaskHistorySection({ items }: { items: Item[] }) {
  const [isOpen, setIsOpen] = useState(false);
  const historyItems = items.filter((i) => i.kind === "user" || i.kind === "tool");

  if (historyItems.length === 0) return null;

  return (
    <section className="pt-3 border-t border-line">
      <button
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          background: 'none',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          color: 'var(--text-main)',
        }}
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className="text-[13px] font-semibold">Task History ({historyItems.length})</span>
        <span
          style={{
            transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.15s ease',
            marginLeft: 'auto',
            display: 'inline-flex',
          }}
        >
          <Icon name="chevronDown" size={12} />
        </span>
      </button>
      
      {isOpen && (
        <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {historyItems.map((item, index) => {
            if (item.kind === "user") {
              return (
                <div key={index} style={{ display: 'flex', gap: '8px', fontSize: '12px' }}>
                  <span style={{ color: 'var(--accent)', marginTop: '2px' }}>
                    <Icon name="chat" size={11} />
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ fontWeight: 500, color: 'var(--text-muted)' }}>Prompt:</span>{" "}
                    <span style={{ color: 'var(--text-main)', wordBreak: 'break-all' }} className="truncate block">
                      {item.text}
                    </span>
                  </div>
                </div>
              );
            } else {
              const isDone = item.status === "completed" || item.status === "done" || item.status === "success";
              const isSubagent = item.name === "delegate" || item.name === "explore";
              const iconName: "file" | "wrench" | "sparkle" = isSubagent ? "sparkle" : (item.name.includes("file") || item.name.includes("grep") || item.name.includes("search") ? "file" : "wrench");
              const iconColor = isDone ? "var(--ok)" : "var(--accent)";
              
              return (
                <div key={index} style={{ display: 'flex', gap: '8px', fontSize: '12px' }}>
                  <span style={{ color: iconColor, marginTop: '2px' }}>
                    <Icon name={iconName} size={11} />
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ fontWeight: 500, color: 'var(--text-muted)' }}>Tool:</span>{" "}
                    <code style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-main)' }}>
                      {item.name}
                    </code>
                    {item.args && Object.keys(item.args).length > 0 && (
                      <span style={{ fontSize: '10px', color: 'var(--text-faint)', marginLeft: '4px', fontStyle: 'italic' }}>
                        ({Object.keys(item.args).join(", ")})
                      </span>
                    )}
                  </div>
                </div>
              );
            }
          })}
        </div>
      )}
    </section>
  );
}
