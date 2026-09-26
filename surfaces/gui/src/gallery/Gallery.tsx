// Card gallery — a dev-only page (/#/gallery) that renders every tool-call card in every
// named state from the payload files under states/, with no server, socket or model.
// Spec: ocw-context/docs/card-gallery-spec.md. Loaded lazily from main.tsx behind
// import.meta.env.DEV, so none of this ships in the desktop build.
import { useEffect, useMemo, useState } from "react";
import { InboxItemCard } from "../components/InboxItemCard";
import { CARDS, GALLERY_MODEL_LABELS, GROUPS, type CardDef } from "./cards";
import { inboxItemBuilders } from "./inboxItems";
import { SCENARIOS, scenarioUrl } from "./scenarios";
import "./gallery.css";

type Place = "session" | "inbox";
type Theme = "light" | "dark";
type TextSize = "small" | "default" | "large";
type Width = "wide" | "narrow";

interface Route {
  card: string;
  state: string;
  place: Place;
}

function parseHash(hash: string): Route {
  const m = hash.match(/^#\/gallery(?:\/([\w-]+))?(?:\/([\w-]+))?(?:\?(.*))?$/);
  const params = new URLSearchParams(m?.[3] ?? "");
  return { card: m?.[1] ?? "", state: m?.[2] ?? "", place: params.get("place") === "inbox" ? "inbox" : "session" };
}

function hashFor(r: Route): string {
  return `#/gallery/${r.card}/${r.state}${r.place === "inbox" ? "?place=inbox" : ""}`;
}

interface Sent {
  at: string;
  channel: string;
  body: unknown;
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="gal-seg" role="group" aria-label={label}>
      <span className="gal-seg-label">{label}</span>
      {options.map((o) => (
        <button
          key={o.value}
          className={"gal-seg-btn" + (o.value === value ? " on" : "")}
          data-testid={`gallery-${label.toLowerCase()}-${o.value}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Gallery() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  const [textSize, setTextSize] = useState<TextSize>(
    () => (document.documentElement.dataset.textSize as TextSize) || "default",
  );
  const [width, setWidth] = useState<Width>("wide");
  const [sent, setSent] = useState<Sent[]>([]);
  const [showPayload, setShowPayload] = useState(false);
  const [copied, setCopied] = useState(false);
  // Cards keep their own state (ticks, expanders); bumping this remounts a fresh one.
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Applied straight to <html>, never saved: the gallery must not change the app's own
  // appearance preferences.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    if (textSize === "default") delete document.documentElement.dataset.textSize;
    else document.documentElement.dataset.textSize = textSize;
  }, [textSize]);

  const onScenarios = route.card === "scenarios";
  const card: CardDef = CARDS.find((c) => c.id === route.card) ?? CARDS[0];
  const state = card.states.find((s) => s.id === route.state) ?? card.states[0];
  const toInbox = inboxItemBuilders[card.id];
  const place: Place = route.place === "inbox" && toInbox ? "inbox" : "session";

  const go = (next: Partial<Route>) => {
    const r = { card: card.id, state: state.id, place, ...next };
    if (next.card && next.card !== card.id && !next.state)
      r.state = (CARDS.find((c) => c.id === next.card) ?? card).states[0].id;
    window.location.hash = hashFor(r);
  };

  // A new card, state or place starts with an empty response panel and a fresh card.
  useEffect(() => {
    setSent([]);
    setCopied(false);
  }, [card.id, state.id, place]);

  const record = (channel: string, body: unknown) =>
    setSent((p) => [{ at: new Date().toLocaleTimeString(), channel, body }, ...p]);

  const stage = useMemo(() => {
    if (place === "inbox" && toInbox) {
      const item = toInbox(state);
      return (
        <div className="max-w-4xl mx-auto px-7 py-6">
          <InboxItemCard
            item={item}
            modelLabels={GALLERY_MODEL_LABELS}
            onResolve={(id, resolution) => {
              let parsed: unknown = resolution;
              try {
                parsed = JSON.parse(resolution);
              } catch {
                /* plain-string resolutions ("allow", "deny") stay strings */
              }
              record(`POST /v1/inbox/${id}/resolve  ·  resolution${typeof parsed === "string" ? "" : " (a JSON string, parsed here)"}`, parsed);
            }}
          />
        </div>
      );
    }
    const inline = card.renderInline(state, (message, note) => record(note ?? "websocket message", message));
    // Message cards live in the transcript column; everything else docks above the composer.
    return card.stage === "transcript" ? (
      <div className="px-6 py-5">
        <div className="transcript">{inline}</div>
      </div>
    ) : (
      <div className="composer-wrap px-6 pb-5 pt-4">{inline}</div>
    );
    // `nonce` is a dependency on purpose: Reset rebuilds the element so the key below remounts it.
  }, [card, state, place, toInbox, nonce]);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.origin + window.location.pathname + hashFor({ card: card.id, state: state.id, place }));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — the address bar has the same link */
    }
  };

  return (
    <div className="gal" data-testid="gallery">
      <aside className="gal-rail">
        <div className="gal-brand">
          Card gallery <span className="gal-dev">dev only</span>
        </div>
        {GROUPS.map((g) => {
          const cards = CARDS.filter((c) => c.group === g);
          if (!cards.length) return null;
          return (
            <div key={g} className="gal-group">
              <div className="gal-group-title">{g}</div>
              {cards.map((c) => (
                <button
                  key={c.id}
                  className={"gal-nav" + (!onScenarios && c.id === card.id ? " on" : "")}
                  data-testid={`gallery-card-${c.id}`}
                  onClick={() => go({ card: c.id, state: "" })}
                >
                  <span>{c.title}</span>
                  <span className="gal-count">{c.states.length}</span>
                </button>
              ))}
            </div>
          );
        })}
        <div className="gal-group">
          <div className="gal-group-title">Sessions</div>
          <button
            className={"gal-nav" + (onScenarios ? " on" : "")}
            data-testid="gallery-scenarios"
            onClick={() => (window.location.hash = "#/gallery/scenarios")}
          >
            <span>Scenarios</span>
            <span className="gal-count">{SCENARIOS.length}</span>
          </button>
        </div>
      </aside>

      {onScenarios ? (
        <main className="gal-main" data-testid="gallery-scenario-list">
          <h1 className="gal-title">Scenarios</h1>
          <div className="gal-sub">
            A saved moment of a session: transcript, waiting cards and rail. Opens the real app against an
            in-memory API — no server, no model. Files: <code>src/gallery/scenarios/*.json</code>
          </div>
          {SCENARIOS.map((s) => (
            <a key={s.id} className="gal-scn" href={scenarioUrl(s)} data-testid={`gallery-scenario-${s.id}`}>
              <div className="gal-scn-title">{s.title}</div>
              {s.note && <div className="gal-state-note">{s.note}</div>}
              <div className="gal-scn-meta">
                {s.session.agent} · {s.session.mode} · {s.session.unattended ? "approvals to the Inbox" : "attended"} ·{" "}
                {s.messages.length} messages
                {s.team ? ` · ${s.team.workers.length} workers` : ""}
              </div>
            </a>
          ))}
        </main>
      ) : (
      <main className="gal-main">
        <header className="gal-top">
          <div>
            <h1 className="gal-title">{card.title}</h1>
            <div className="gal-sub">
              raised by <code>{card.event}</code>
            </div>
          </div>
          <div className="gal-toggles">
            {toInbox && (
              <Segmented
                label="Place"
                value={place}
                options={[
                  { value: "session", label: "In the session" },
                  { value: "inbox", label: "In the Inbox" },
                ]}
                onChange={(v) => go({ place: v })}
              />
            )}
            <Segmented
              label="Width"
              value={width}
              options={[
                { value: "wide", label: "Wide" },
                { value: "narrow", label: "Narrow" },
              ]}
              onChange={setWidth}
            />
            <Segmented
              label="Text"
              value={textSize}
              options={[
                { value: "small", label: "S" },
                { value: "default", label: "M" },
                { value: "large", label: "L" },
              ]}
              onChange={setTextSize}
            />
            <Segmented
              label="Theme"
              value={theme}
              options={[
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark" },
              ]}
              onChange={setTheme}
            />
          </div>
        </header>

        <div className="gal-states" role="tablist" aria-label="States">
          {card.states.map((s) => (
            <button
              key={s.id}
              role="tab"
              aria-selected={s.id === state.id}
              className={"gal-chip" + (s.id === state.id ? " on" : "")}
              data-testid={`gallery-state-${s.id}`}
              onClick={() => go({ state: s.id })}
            >
              {s.title}
            </button>
          ))}
        </div>

        <div className="gal-state-line">
          <span className="gal-state-note">{state.note || " "}</span>
          <span className="gal-state-actions">
            <button className="gal-link" onClick={() => setNonce((n) => n + 1)} data-testid="gallery-reset">
              Reset card
            </button>
            <button className="gal-link" onClick={copyLink} data-testid="gallery-copy-link">
              {copied ? "Copied" : "Copy link"}
            </button>
          </span>
        </div>

        <div className={"gal-stage-wrap " + width}>
          <div className="gal-stage" data-testid="gallery-stage" key={`${card.id}/${state.id}/${place}/${nonce}`}>
            {stage}
          </div>
        </div>

        <section className="gal-panel">
          <div className="gal-panel-head">
            <span>What the card sends</span>
            {sent.length > 0 && (
              <button className="gal-link" onClick={() => setSent([])}>
                Clear
              </button>
            )}
          </div>
          {sent.length === 0 ? (
            <div className="gal-empty">
              {card.stage === "transcript"
                ? "This card is read-only: it sends nothing."
                : "Click a button on the card. Nothing leaves this page."}
            </div>
          ) : (
            <div data-testid="gallery-response">
              {sent.map((s, i) => (
                <div key={i} className="gal-sent">
                  <div className="gal-sent-head">
                    <span>{s.channel}</span>
                    <span>{s.at}</span>
                  </div>
                  {s.body !== null && <pre className="gal-json">{JSON.stringify(s.body, null, 2)}</pre>}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="gal-panel">
          <div className="gal-panel-head">
            <button className="gal-link" onClick={() => setShowPayload((v) => !v)} data-testid="gallery-payload-toggle">
              {showPayload ? "Hide" : "Show"} the payload the server sends
            </button>
            <code className="gal-file">src/gallery/states/{card.id}.ts</code>
          </div>
          {showPayload && (
            <pre className="gal-json" data-testid="gallery-payload">
              {JSON.stringify(place === "inbox" && toInbox ? toInbox(state) : state.payload, null, 2)}
            </pre>
          )}
        </section>
      </main>
      )}
    </div>
  );
}
