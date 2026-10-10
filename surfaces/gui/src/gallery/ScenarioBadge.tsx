// The small floating panel shown over the app while a scenario is open: what this moment
// is, the way back to the gallery, and what the app tried to send (nothing is delivered).
import { useState } from "react";
import type { SentRecord } from "./scenarioApi";
import type { Scenario } from "./scenarios/types";
import "./gallery.css";

export function ScenarioBadge({ scenario, sent }: { scenario: Scenario; sent: SentRecord[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={"gal-badge" + (open ? " open" : "")} data-testid="scenario-badge">
      <button className="gal-badge-pill" onClick={() => setOpen((o) => !o)} data-testid="scenario-sent-toggle">
        <span className="gal-dev">scenario</span>
        <span>{sent.length ? `sent ${sent.length}` : "nothing sent"}</span>
      </button>
      {open && (
        <div className="gal-badge-body" data-testid="scenario-sent">
          <div className="gal-badge-line">
            <span className="gal-badge-title">{scenario.title}</span>
            <a className="gal-link" href={`${window.location.pathname}#/gallery/scenarios`}>
              Back to gallery
            </a>
          </div>
          {scenario.note && <div className="gal-state-note">{scenario.note}</div>}
          {sent.length === 0 && <div className="gal-empty">Answer a card; what the app sends is printed here.</div>}
          {sent.map((s, i) => (
            <div key={i} className="gal-sent">
              <div className="gal-sent-head">
                <span>{s.channel}</span>
                <span>{s.at}</span>
              </div>
              <pre className="gal-json">{JSON.stringify(s.body, null, 2)}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
