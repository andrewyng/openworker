export function SwarmGraph() {
  return (
    <div className="flex gap-3 max-w-4xl my-4">
      <div className="w-7 h-7 rounded-full bg-transparent shrink-0"></div>
      <div className="flex-1 bg-panel border border-line rounded-xl shadow-sm overflow-hidden">
        <div className="px-4 py-2 border-b border-line bg-paper/50 flex justify-between items-center">
          <span className="text-[12px] font-medium text-faint uppercase tracking-wide">Live Swarm Execution</span>
          <div className="flex gap-2 text-[11px]">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500"></span> Fast</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500"></span> Balanced</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-purple-500"></span> Heavy</span>
          </div>
        </div>
        
        {/* Swarm Graph Canvas */}
        <div className="p-8 flex justify-center items-center relative overflow-hidden" style={{ minHeight: "250px" }}>
          {/* Connector Lines (Mocked via SVG) */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ zIndex: 0 }}>
            <path d="M 180 125 C 250 125, 250 50, 320 50" fill="none" stroke="var(--line-strong)" strokeWidth="2"/>
            <path d="M 180 125 C 250 125, 250 125, 320 125" fill="none" stroke="var(--line-strong)" strokeWidth="2"/>
            <path d="M 180 125 C 250 125, 250 200, 320 200" fill="none" stroke="var(--line-strong)" strokeWidth="2"/>
          </svg>

          {/* Nodes */}
          <div className="flex items-center gap-16 relative z-10 w-full max-w-2xl">
            {/* Orchestrator */}
            <div className="w-32 h-10 rounded-full border-2 border-line bg-paper flex items-center justify-center font-medium shadow-md">
              Orchestrator
            </div>
            
            {/* Subagents */}
            <div className="flex flex-col gap-6">
              <div className="w-40 h-10 rounded-full border-2 bg-panel flex items-center px-3 shadow-md" style={{ borderColor: "#22c55e", boxShadow: "0 0 10px rgba(34, 197, 94, 0.2)" }}>
                <span className="text-[12px] truncate w-full text-center font-medium">data-parse-1</span>
              </div>
              
              {/* Pulsing Processing Node (Balanced) */}
              <div 
                className="animate-pulse-glow w-40 h-10 rounded-full border-2 bg-panel flex items-center justify-between px-3 shadow-md cursor-pointer hover:scale-105 transition-transform" 
                style={{ borderColor: "#3b82f6", boxShadow: "0 0 10px rgba(59, 130, 246, 0.2)" }}
                onClick={() => {
                  const event = new CustomEvent("open-trace-panel", { detail: { traceId: "trc_8490321" } });
                  window.dispatchEvent(event);
                }}
              >
                <span className="text-[12px] truncate font-medium">schema-gen</span>
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
              </div>

              <div className="w-40 h-10 rounded-full border-2 bg-panel flex items-center px-3 shadow-md" style={{ borderColor: "#a855f7", boxShadow: "0 0 10px rgba(168, 85, 247, 0.2)" }}>
                <span className="text-[12px] truncate w-full text-center font-medium">logic-refactor</span>
              </div>
            </div>
          </div>
        </div>
        
        <div className="px-4 py-3 border-t border-line bg-paper text-[12px] text-muted">
          Swarm running across 3 parallel subagents. Click on a pulsing node to trace its execution.
        </div>
      </div>
    </div>
  );
}
