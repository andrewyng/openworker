import { useEffect, useMemo, useState } from "react";
import {
  clearConnectorAgentRoute,
  setConnectorAgentRoute,
  type Connector,
  type Persona,
} from "../../api";
import { FOOT, PILL_ACCENT, ROW } from "./ui";

interface IncomingAgentBlockProps {
  c: Connector;
  personas: Persona[];
  onChanged: () => void;
}

function personaName(personas: Persona[], id?: string): string {
  if (!id) return "Default";
  return personas.find((p) => p.id === id)?.name || id;
}

function needsExplicitWorkspace(persona?: Persona): boolean {
  return Boolean(persona?.requires_folder);
}

export function IncomingAgentBlock({ c, personas, onChanged }: IncomingAgentBlockProps) {
  const route = c.inbound_agent_route;
  const routeAgent = route?.explicit ? route.agent : "";
  const routeWorkspace = route?.explicit ? route.workspace || "" : "";
  const [agent, setAgent] = useState(routeAgent);
  const [workspace, setWorkspace] = useState(routeWorkspace);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setAgent(routeAgent);
    setWorkspace(routeWorkspace);
    setError("");
  }, [c.name, routeAgent, routeWorkspace]);

  const enabledPersonas = useMemo(() => personas.filter((p) => p.enabled), [personas]);
  const selected = enabledPersonas.find((p) => p.id === agent);
  const requiresWorkspace = needsExplicitWorkspace(selected);
  const defaultId = route?.default_agent || personas.find((p) => p.default)?.id || "";
  const defaultLabel = personaName(personas, defaultId);
  const dirty = agent !== routeAgent || workspace !== routeWorkspace;

  if (!c.two_way) return null;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const out = agent
        ? await setConnectorAgentRoute(c.name, agent, workspace.trim())
        : await clearConnectorAgentRoute(c.name);
      if (!out.ok) {
        setError(out.error || "Could not save incoming agent.");
        return;
      }
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className={ROW + " items-start"}>
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] font-medium text-ink">Incoming messages</div>
          <div className="text-[12px] text-muted mt-0.5">
            New {c.title} conversations are handled by:
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              className="min-w-[220px] max-w-full rounded-lg border border-line bg-paper px-2.5 py-1.5 text-[12.5px] text-ink outline-none"
              data-testid={`connector-agent-route-${c.name}`}
              value={agent}
              onChange={(e) => {
                setAgent(e.target.value);
                setError("");
              }}
            >
              <option value="">Default agent ({defaultLabel})</option>
              {enabledPersonas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {needsExplicitWorkspace(p) ? " — workspace required" : ""}
                </option>
              ))}
            </select>
            {requiresWorkspace && (
              <input
                className="min-w-[260px] flex-1 rounded-lg border border-line bg-paper px-2.5 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-faint"
                data-testid={`connector-agent-route-workspace-${c.name}`}
                placeholder="Workspace path for this agent"
                value={workspace}
                onChange={(e) => {
                  setWorkspace(e.target.value);
                  setError("");
                }}
              />
            )}
            <button
              className={PILL_ACCENT}
              data-testid={`connector-agent-route-save-${c.name}`}
              disabled={busy || !dirty || (requiresWorkspace && !workspace.trim())}
              onClick={save}
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
          {error && <div className="text-[12px] text-danger mt-2">{error}</div>}
        </div>
      </div>
      <div className={FOOT}>
        Applies only to new conversations created by this connector. Existing sessions keep their
        agent; web sessions still use the default agent.
      </div>
    </div>
  );
}
