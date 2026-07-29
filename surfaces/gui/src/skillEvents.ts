import type { SkillRef } from "./types";

export const OPEN_SKILL_EVENT = "ocw-open-skill";

export function openSkill(skill: SkillRef) {
  window.dispatchEvent(new CustomEvent(OPEN_SKILL_EVENT, { detail: skill }));
}
