// Boots the real app on a scenario (dev only): install the in-memory API first, then
// render <App/> with the scenario badge floating over it.
import React, { useEffect, useState, type ComponentType } from "react";
import type { Root } from "react-dom/client";
import { ScenarioBadge } from "./ScenarioBadge";
import { installScenario, type SentRecord } from "./scenarioApi";
import { SCENARIOS } from "./scenarios";

export function bootScenario(id: string, root: Root, App: ComponentType): boolean {
  const scenario = SCENARIOS.find((s) => s.id === id);
  if (!scenario) return false;
  const sent: SentRecord[] = [];
  const listeners = new Set<(list: SentRecord[]) => void>();
  installScenario(scenario, (r) => {
    sent.unshift(r);
    listeners.forEach((l) => l([...sent]));
  });
  function Badge() {
    const [list, setList] = useState<SentRecord[]>(sent);
    useEffect(() => {
      listeners.add(setList);
      return () => void listeners.delete(setList);
    }, []);
    return <ScenarioBadge scenario={scenario!} sent={list} />;
  }
  root.render(
    <React.StrictMode>
      <App />
      <Badge />
    </React.StrictMode>,
  );
  return true;
}
