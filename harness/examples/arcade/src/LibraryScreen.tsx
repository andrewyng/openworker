import React, { useState } from "react";
import type { Game } from "../shared/contracts";
import { Icon } from "./icons";
export function LibraryScreen({
  games,
  activeId,
  onPlay,
  onBuild,
  onClone,
  onCreate,
  compact = false,
}: {
  games: Game[];
  activeId: string | null;
  onPlay: (id: string) => void;
  onBuild: (id: string) => void;
  onClone: (id: string) => void;
  onCreate: () => void;
  compact?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("All games");
  const visible = games.filter(
    (g) =>
      g.title.toLowerCase().includes(query.toLowerCase()) &&
      (filter === "All games" ||
        (filter === "Ready to play" && g.revisionId) ||
        (filter === "In progress" && !g.revisionId)),
  );
  return (
    <>
      <header className={`collection-header ${compact ? "compact" : ""}`}>
        <div>
          {compact ? <h2>Your games</h2> : <h1>My games</h1>}
          <p>
            {compact
              ? "Pick up where you left off."
              : "Everything you’ve made. Ready for one more round."}
          </p>
        </div>
        <button className="primary new-game" onClick={onCreate}>
          <Icon name="plus" size={18} /> New game
        </button>
      </header>
      {!compact && (
        <div className="collection-toolbar">
          <div className="collection-tabs">
            {["All games", "Ready to play", "In progress"].map((t) => (
              <button
                className={filter === t ? "active" : ""}
                key={t}
                onClick={() => setFilter(t)}
              >
                {t}
                {t === "All games" && <span>{games.length}</span>}
              </button>
            ))}
          </div>
          <label className="collection-search">
            <Icon name="search" size={17} />
            <input
              aria-label="Search games"
              placeholder="Search your collection"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
      )}
      {visible.length ? (
        <section className="collection-grid">
          {(compact ? visible.slice(0, 3) : visible).map((g, i) => (
            <article className="collection-card" key={g.id}>
              <button
                className={`collection-art cover-${i % 4}`}
                aria-label={`Play ${g.title}`}
                disabled={!g.revisionId}
                onClick={() => onPlay(g.id)}
              >
                {g.thumbnail || g.coverUrl ? (
                  <img
                    src={
                      g.thumbnail
                        ? `arcade-preview://games/${g.id}/thumbnail.png`
                        : g.coverUrl
                    }
                    alt={`${g.title} ${g.thumbnail ? "preview" : "cover illustration"}`}
                  />
                ) : (
                  <>
                    <div className="cover-grid" />
                    <div className="cover-orb" />
                    <Icon
                      name={g.brief.engine === "three" ? "cube" : "game"}
                      size={82}
                    />
                    <span className="cover-caption">
                      {g.brief.engine === "three"
                        ? "ANOTHER DIMENSION"
                        : "MADE BY YOU"}
                    </span>
                  </>
                )}
                <span className="cover-tag">
                  {activeId === g.id
                    ? "Building"
                    : g.brief.engine === "three"
                      ? "3D · Three.js"
                      : g.brief.kind === "You decide"
                        ? "Arcade"
                        : g.brief.kind}
                </span>
                {g.revisionId && (
                  <span className="cover-play">
                    <Icon name="play" size={22} />
                  </span>
                )}
                {!g.revisionId && (
                  <span className="cover-draft">
                    {g.status === "failed"
                      ? "Needs attention"
                      : "Work in progress"}
                  </span>
                )}
              </button>
              <div className="collection-card-info">
                <h2>{g.title}</h2>
                <p>
                  Updated{" "}
                  {new Date(g.updatedAt).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                  <span>·</span>
                  {g.revisionId ? "Ready when you are" : "Keep building"}
                </p>
              </div>
              <div className="collection-actions">
                <button
                  className="card-play"
                  disabled={!g.revisionId}
                  onClick={() => onPlay(g.id)}
                >
                  <Icon name="play" size={14} />
                  Play
                </button>
                <button
                  onClick={() => onBuild(g.id)}
                  aria-label={`Continue building ${g.title}`}
                >
                  Continue building
                  <Icon name="arrow" size={14} />
                </button>
                <button
                  className="clone-game"
                  disabled={!g.revisionId}
                  aria-label={`Clone ${g.title}`}
                  title="Clone into an independent project"
                  onClick={() => onClone(g.id)}
                >
                  <Icon name="copy" size={15} />
                  Clone
                </button>
              </div>
            </article>
          ))}
        </section>
      ) : (
        <div className="collection-empty">
          <div className="empty-stack">
            <Icon name="game" size={48} />
          </div>
          <h2>
            {games.length
              ? "No games match just yet."
              : "Your first game starts with an idea."}
          </h2>
          <p>
            {games.length
              ? "Try a different search or collection filter."
              : "Make something you wish existed. We’ll help you bring it to life."}
          </p>
          {!games.length && (
            <button className="primary" onClick={onCreate}>
              Create your first game
              <Icon name="arrow" size={16} />
            </button>
          )}
        </div>
      )}
    </>
  );
}
