import type { SessionInfo } from "./types";
import type { TeamSummary, TeamWorker } from "./teamView";

export type WorkerFilter = {
  query?: string;
  role?: string;
  attention?: boolean;
};
export type RosterEntry = {
  id: string;
  name: string;
  role: string;
  machine: string;
  task: string;
  attention: boolean;
  working: boolean;
  session?: SessionInfo;
  worker?: TeamWorker;
};

export function teamRoster(
  sessions: SessionInfo[],
  summary?: TeamSummary | null,
  machine = "",
): RosterEntry[] {
  const byId = new Map(sessions.map((s) => [s.session_id, s]));
  const workers = new Map(summary?.workers.map((w) => [w.session_id, w]));
  const ids = [...new Set([...byId.keys(), ...workers.keys()])];
  return ids.map((id) => {
    const session = byId.get(id),
      worker = workers.get(id);
    const name = worker?.actor || session?.team?.actor || session?.agent || id;
    const tasks =
      summary?.items.filter(
        (i) => worker?.items.includes(i.id) || i.assignee === name,
      ) || [];
    const active = tasks.filter((i) => !["done", "canceled"].includes(i.group));
    return {
      id,
      name,
      role: worker?.role || session?.agent || "",
      machine: session?.machine_name || machine,
      task:
        active
          .map((i) => [i.title, i.status].filter(Boolean).join(" · "))
          .join(" · ") ||
        session?.team?.current_item ||
        "",
      attention:
        !!session?.attention ||
        active.some((i) => i.group === "waiting") ||
        ["blocked", "waiting"].includes(session?.team?.status || ""),
      working: worker?.running ?? session?.liveness === "working",
      session,
      worker,
    };
  });
}

export function filterWorkers(entries: RosterEntry[], filter: WorkerFilter) {
  const query = (filter.query || "").trim().toLocaleLowerCase();
  return entries.filter(
    (w) =>
      (!filter.role || w.role === filter.role) &&
      (!filter.attention || w.attention) &&
      [w.name, w.role, w.machine, w.task]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query),
  );
}
