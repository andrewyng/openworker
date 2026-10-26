import { useState } from "react";
import { TeamQuickLook, TeamView } from "../components/TeamView";
import type { TeamSummary } from "../teamView";
import { TaskBoardContext } from "../components/TaskChip";
export function TeamViewGallery({ summary }: { summary: TeamSummary }) {
  const [open, setOpen] = useState(true),
    [item, setItem] = useState<number | null>(null);
  return (
    <TaskBoardContext.Provider
      value={{
        board: { space: summary.space, name: "Acme", items: summary.items },
        sessionId: summary.lead_session,
      }}
    >
      <div
        style={{
          height: 640,
          display: "flex",
          flexDirection: "column",
          position: "relative",
        }}
      >
        {open && (
          <TeamView
            summary={summary}
            initialItem={item}
            sessionId={summary.lead_session}
            sessions={[]}
            machineName="build-box"
            onClose={() => setOpen(false)}
            onRefresh={() => {}}
          />
        )}
        <TeamQuickLook
          key={summary.team_id}
          summary={summary}
          machine="build-box"
          onOpen={(id) => {
            setItem(id ?? null);
            setOpen(true);
          }}
        />
      </div>
    </TaskBoardContext.Provider>
  );
}
