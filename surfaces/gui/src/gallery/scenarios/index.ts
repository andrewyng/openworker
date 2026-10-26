// Every scenario the gallery lists: each *.json in this folder. Hand-written, or saved
// from a real run with scripts/export-scenario.py — dropping a file here is enough.
import { teamViewScenarios } from "./team-view";
import type { Scenario } from "./types";

const files = import.meta.glob("./*.json", { eager: true, import: "default" }) as Record<string, Scenario>;

export const SCENARIOS: Scenario[] = Object.keys(files)
  .sort()
  .map((k) => files[k]).concat(teamViewScenarios);

/** Opening a scenario is a page load: the app reads its session from the hash at start-up. */
export function scenarioUrl(s: Scenario): string {
  return `${window.location.pathname}?scenario=${encodeURIComponent(s.id)}#/s/${s.session.session_id}`;
}
