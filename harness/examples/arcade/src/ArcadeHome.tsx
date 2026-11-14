import React, { useState } from "react";
import type { StarterGame } from "../shared/contracts";
import { Icon } from "./icons";
import { GameFrame } from "./GameFrame";

export function ArcadeHome({
  starters,
  onPlay,
  onRemix,
}: {
  starters: StarterGame[];
  onPlay: (game: StarterGame) => void;
  onRemix: (game: StarterGame) => void;
}) {
  const [featuredId, setFeaturedId] = useState<string>();
  const featured = starters.find((g) => g.id === featuredId) ?? starters[0];
  if (!featured)
    return (
      <div className="home-loading" role="status">
        Opening your arcade…
      </div>
    );
  return (
    <>
      <section
        className={`featured-game featured-${featured.id}`}
        aria-label="Featured game"
      >
        <img
          className="featured-art"
          src={featured.heroUrl}
          alt={`${featured.title} game illustration`}
        />
        <div className="featured-shade" />
        <div className="featured-copy">
          <span className="feature-label">
            <span /> READY TO PLAY. YOURS TO REMIX.
          </span>
          <h1>{featured.title}</h1>
          <p>{featured.description}</p>
          <div className="feature-actions">
            <button className="hero-play" onClick={() => onPlay(featured)}>
              <Icon name="play" size={19} /> Play now
            </button>
            <button className="hero-remix" onClick={() => onRemix(featured)}>
              <Icon name="sparkle" size={18} /> Make it yours
            </button>
          </div>
          <div className="feature-meta">
            {featured.tags.join(" · ")}
            <span />
            No account. No download.
          </div>
        </div>
        <div className="featured-index" aria-label="Choose featured game">
          {starters.map((game, i) => (
            <button
              key={game.id}
              className={game.id === featured.id ? "active" : ""}
              aria-label={`Feature ${game.title}`}
              aria-pressed={game.id === featured.id}
              onClick={() => setFeaturedId(game.id)}
            >
              <span>0{i + 1}</span>
              {game.title}
            </button>
          ))}
        </div>
      </section>
      <section className="starter-shelf" aria-label="Starter games">
        <div className="shelf-heading">
          <div>
            <h2>A good place to play.</h2>
            <p>A great place to start something of your own.</p>
          </div>
          <span className="shelf-note">Included with Arcade</span>
        </div>
        <div className="starter-grid">
          {starters.map((game) => (
            <article className="starter-card" key={game.id}>
              <button
                className="starter-art"
                aria-label={`Play starter ${game.title}`}
                onClick={() => onPlay(game)}
              >
                <img src={game.coverUrl} alt={`${game.title} illustration`} />
                <span className="starter-play">
                  <Icon name="play" size={24} />
                </span>
              </button>
              <div className="starter-caption">
                <div>
                  <h3>{game.title}</h3>
                  <p>{game.tags.join(" / ")}</p>
                </div>
                <button
                  aria-label={`Remix ${game.title}`}
                  onClick={() => onRemix(game)}
                  title="Copy this game and make it your own"
                >
                  <Icon name="sparkle" size={16} /> Remix
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}

export function StarterPlayer({
  game,
  onBack,
  onRemix,
  onFullscreen,
}: {
  game: StarterGame;
  onBack: () => void;
  onRemix: () => void;
  onFullscreen: () => void;
}) {
  const [restart, setRestart] = useState(0);
  return (
    <section className="starter-player">
      <header>
        <button aria-label="Back to games" onClick={onBack}>
          <Icon name="back" />
          Back
        </button>
        <h1>{game.title}</h1>
        <div className="player-actions">
          <button
            onClick={() => setRestart((n) => n + 1)}
            aria-label="Restart game"
          >
            <Icon name="rotate" size={17} />
            Restart
          </button>
          <button onClick={onFullscreen} aria-label="Toggle fullscreen">
            <Icon name="fullscreen" size={17} />
          </button>
          <button className="hero-play" onClick={onRemix}>
            <Icon name="sparkle" size={17} />
            Make it yours
          </button>
        </div>
      </header>
      <GameFrame
        key={`${game.id}-${restart}`}
        id="starter-preview"
        title={game.title}
        src={game.previewUrl}
        sandbox="allow-scripts"
      />
      <footer>
        <span>Like it? Change the rules, the world, or the whole idea.</span>
        <span>Remixing creates your own copy · {game.license} licensed</span>
      </footer>
    </section>
  );
}
