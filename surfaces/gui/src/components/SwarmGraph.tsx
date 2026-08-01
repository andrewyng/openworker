import { useState } from "react";
import type { ToolItem } from "../types";

// Short callsigns assigned by task heuristic, or fallback to "Agent {n}".
function callsignFor(call: ToolItem, index: number): string {
  const task = String(call.args?.task || "").toLowerCase();
  if (task.includes("search") || task.includes("find") || task.includes("explore") || task.includes("research")) return "Scout";
  if (task.includes("build") || task.includes("implement") || task.includes("write code") || task.includes("create") || task.includes("generate")) return "Forge";
  if (task.includes("analyze") || task.includes("review") || task.includes("examine") || task.includes("audit")) return "Lens";
  if (task.includes("test") || task.includes("verify") || task.includes("check")) return "Probe";
  if (task.includes("plan") || task.includes("design") || task.includes("architect")) return "Map";
  if (task.includes("deploy") || task.includes("release") || task.includes("ship")) return "Launch";
  return `Agent ${index + 1}`;
}

export function SwarmGraph({ calls }: { calls: ToolItem[] }) {
  if (!calls || calls.length === 0) return null;

  const [collapsed, setCollapsed] = useState(false);

  const hasRunning = calls.some((c) => c.status === "…");

  return (
    <div className="swarm-container">
      {/* Header: always visible */}
      <div className="swarm-header">
        <span className="swarm-title">Swarm Orchestration ({calls.length} Agent{calls.length !== 1 ? "s" : ""})</span>
        <div className="swarm-header-right">
          {hasRunning && (
            <span className="swarm-inprogress">
              <span className="swarm-dot pulse-glow"></span>
              In progress
            </span>
          )}
          <button
            className="swarm-collapse-btn"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? "Expand graph" : "Collapse graph"}
            aria-label={collapsed ? "Expand swarm graph" : "Collapse swarm graph"}
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              style={{
                transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
                transition: "transform 0.15s ease",
              }}
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </div>
      </div>

      {/* Graph: toggled by collapse */}
      {!collapsed && (
        <div className="swarm-graph">
          {/* Orchestrator pill */}
          <div className="swarm-orchestrator">Orchestrator</div>

          {/* Connector SVG */}
          <svg
            className="swarm-connectors"
            width="100%"
            height="28"
            viewBox="0 0 100 28"
            preserveAspectRatio="none"
          >
            {calls.map((_, i) => {
              const x = (100 / (calls.length + 1)) * (i + 1);
              return (
                <path
                  key={i}
                  d={`M50,0 L${x},28`}
                  stroke="var(--line-strong)"
                  strokeWidth="1.5"
                  fill="none"
                />
              );
            })}
          </svg>

          {/* Agent cards grid */}
          <div className="swarm-agent-grid" style={{ gridTemplateColumns: `repeat(${calls.length}, 1fr)` }}>
            {calls.map((call, i) => {
              const isRunning = call.status === "…";
              const isSuccess = call.status === "completed" || call.status === "done";

              const iconColor = isRunning ? "var(--accent)" : isSuccess ? "var(--ok)" : "var(--muted)";
              const iconBg = isRunning
                ? "var(--accent-soft)"
                : isSuccess
                ? "var(--ok-soft)"
                : "var(--paper)";
              const iconGlyph = isSuccess ? "✓" : isRunning ? "⟳" : "•";
              const iconClass = isRunning ? "pulse-glow" : "";

              const borderColor = isSuccess
                ? "var(--ok-line)"
                : isRunning
                ? "color-mix(in srgb, var(--accent) 35%, var(--line))"
                : "var(--line)";

              const statusText = isRunning ? "Running" : isSuccess ? "Completed" : call.status || "Queued";
              const statusColor = isRunning
                ? "var(--accent)"
                : isSuccess
                ? "var(--ok)"
                : "var(--muted)";

              const taskStr = String(call.args?.task || "Subagent task");
              const displayName = callsignFor(call, i);
              const targetModel = String(call.args?.target_model || "balanced").toLowerCase();

              return (
                <div
                  key={i}
                  className="swarm-agent-card"
                  style={{ borderColor }}
                  onClick={() => {
                    const event = new CustomEvent("open-trace-panel", {
                      detail: { traceId: call.id, tool: call },
                    });
                    window.dispatchEvent(event);
                  }}
                >
                  <div className="swarm-agent-top">
                    <span
                      className="swarm-agent-icon"
                      style={{ color: iconColor, background: iconBg }}
                    >
                      <span className={iconClass}>{iconGlyph}</span>
                    </span>
                    <span className="swarm-agent-name">{displayName}</span>
                  </div>
                  <span className="swarm-agent-task">{taskStr}</span>
                  <div className="swarm-agent-footer">
                    <span className="swarm-agent-model">{targetModel}</span>
                    <span className="swarm-agent-status" style={{ color: statusColor }}>
                      {statusText}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
