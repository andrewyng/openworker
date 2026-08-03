import { SkillsTab } from "./SkillsTab";

// The Skills surface — the sidebar's one-click entry to the skills list (owner ask
// 2026-08-03: the entry sits between New session and Search). The same management
// home as Settings ▸ Skills, rendered as a full page on the shared §28 shell
// (full-bleed main, centered max-w-4xl column — same as Connectors/Automations/Inbox).
export function SkillsView({
  onCreateSkill,
}: {
  // The doorway (SKILLS-SPEC §5.2): starts a new conversation with the description
  // prefilled in the composer — the worker builds the skill and proposes it via save_skill.
  onCreateSkill?: (description: string) => void;
}) {
  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-4xl mx-auto px-7 py-6">
          <SkillsTab onCreateSkill={onCreateSkill} />
        </div>
      </div>
    </main>
  );
}
