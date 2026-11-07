import React from "react";
import type { TestingSummary, TestCheck } from "../shared/contracts";
const labels: Record<TestCheck, string> = {
  start: "Game starts",
  movement: "Movement / controls",
  pause: "Pause / resume",
  compact: "Compact layout",
  level: "Level completion",
};
export function TestingPanel({ value }: { value?: TestingSummary }) {
  if (!value) return null;
  const count=(n:number,label:string)=>`${n} ${label}${n===1?"":"s"}`;
  return (
    <details className="testing-summary">
      <summary>
        Testing{" "}
        <span>
          {value.error
            ? "Unavailable"
            : !value.current
              ? "Earlier draft — retest needed"
              : `${count(value.screenshots,"screenshot")} · ${count(value.keys + value.pointer,"input action")}`}
        </span>
      </summary>
      {value.error ? (
        <p>{value.error}</p>
      ) : (
        <>
          <p>
            Recorded by Arcade: {count(value.screenshots,"screenshot")}, {count(value.keys,"keyboard action")}, {count(value.pointer,"pointer action")}.{" "}
            {value.viewports.join(", ") || "No viewport inspected."}
            {value.hostErrors > 0
              ? ` ${value.hostErrors} preview operation errors.`
              : ""}
          </p>
          <h4>Agent assessment{!value.current ? " — outdated" : ""}</h4>
          <ul>
            {(Object.keys(labels) as TestCheck[]).map((key) => {
              const c = value.checks[key];
              return (
                <li key={key}>
                  <div>
                    <span>{labels[key]}</span>
                    <em
                      className={`check-${value.current ? c.status : "not_tested"}`}
                    >
                      {!value.current
                        ? "Needs retest"
                        : c.status === "not_tested"
                          ? "Not tested"
                          : c.status === "passed"
                            ? "Passed"
                            : "Failed"}
                    </em>
                  </div>
                  <small>{c.note}</small>
                </li>
              );
            })}
          </ul>
          <p>
            Agent-reported results are not a certification. Save recovery and OS
            compatibility are not tested by this temporary preview.
          </p>
        </>
      )}
    </details>
  );
}
