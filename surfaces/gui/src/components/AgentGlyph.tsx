// Subagent identity glyphs (OPE-259, owner-approved sheet 2026-11-07): ten animal
// silhouettes × eight cool-color families = 80 stable identities. One color per mark;
// the eyes are punched out (evenodd holes), so the surface shows through and the same
// path works on light and dark. Shape = seed mod 10, family = seed ÷ 10 mod 8 — the
// seed is the task's spawn counter, persisted, so a run keeps its animal forever.
// No warm colors: amber stays the rail's one attention meaning.

const eye = (x: number, y: number, r = 1.1) =>
  `M${x - r} ${y} a${r} ${r} 0 1 0 ${2 * r} 0 a${r} ${r} 0 1 0 ${-2 * r} 0`;

// [name, svg inner markup]
const ANIMALS: [string, string][] = [
  ["cat", `<path fill-rule="evenodd" d="M10 5 C11 4.6 11.8 4.4 12.6 4.5 L15.6 1.6 L16.4 7.2 C17 8.2 17.3 9.6 17.3 11 C17.3 15.2 14 17.8 10 17.8 C6 17.8 2.7 15.2 2.7 11 C2.7 9.6 3 8.2 3.6 7.2 L4.4 1.6 L7.4 4.5 C8.2 4.4 9 4.6 10 5 Z ${eye(7.3, 11)} ${eye(12.7, 11)}"/>`],
  ["puppy", `<g fill-rule="evenodd"><path d="M10 4.2 C13.6 4.2 16 6.9 16 10.7 C16 14.7 13.4 17.4 10 17.4 C6.6 17.4 4 14.7 4 10.7 C4 6.9 6.4 4.2 10 4.2 Z ${eye(7.6, 10.3)} ${eye(12.4, 10.3)}"/><ellipse cx="4.1" cy="8.9" rx="1.95" ry="4.3" transform="rotate(16 4.1 8.9)"/><ellipse cx="15.9" cy="8.9" rx="1.95" ry="4.3" transform="rotate(-16 15.9 8.9)"/></g>`],
  ["bunny", `<path fill-rule="evenodd" d="M6.4 1.2 C8 1.2 8.7 4.2 8.8 7.6 C9.2 7.5 9.6 7.5 10 7.5 C10.4 7.5 10.8 7.5 11.2 7.6 C11.3 4.2 12 1.2 13.6 1.2 C15.3 1.2 15.6 5.2 14.5 8.5 C15.6 9.5 16.2 10.9 16.2 12.4 C16.2 15.6 13.4 17.8 10 17.8 C6.6 17.8 3.8 15.6 3.8 12.4 C3.8 10.9 4.4 9.5 5.5 8.5 C4.4 5.2 4.7 1.2 6.4 1.2 Z ${eye(7.6, 12.4)} ${eye(12.4, 12.4)}"/>`],
  ["fox", `<path fill-rule="evenodd" d="M2.6 2.6 C5 2.8 7 3.6 8.3 4.7 C8.9 4.6 9.4 4.5 10 4.5 C10.6 4.5 11.1 4.6 11.7 4.7 C13 3.6 15 2.8 17.4 2.6 C17.6 5.4 17.2 8 16.4 9.4 C16.4 9.9 16.5 10.3 16.5 10.8 C16.5 15 13.3 17.6 10 17.6 C6.7 17.6 3.5 15 3.5 10.8 C3.5 10.3 3.6 9.9 3.6 9.4 C2.8 8 2.4 5.4 2.6 2.6 Z ${eye(7.2, 10.6)} ${eye(12.8, 10.6)}"/>`],
  ["owl", `<path fill-rule="evenodd" d="M3.4 2.2 L7.2 4 C8.1 3.6 9 3.4 10 3.4 C11 3.4 11.9 3.6 12.8 4 L16.6 2.2 L15.7 6.3 C16.6 7.6 17.1 9.3 17.1 11 C17.1 15.3 14 18 10 18 C6 18 2.9 15.3 2.9 11 C2.9 9.3 3.4 7.6 4.3 6.3 Z ${eye(7.1, 9.8, 1.9)} ${eye(12.9, 9.8, 1.9)}"/><circle cx="7.1" cy="9.8" r="0.85"/><circle cx="12.9" cy="9.8" r="0.85"/>`],
  ["octopus", `<path fill-rule="evenodd" d="M3.6 13 L3.6 10 C3.6 5.8 6.3 3 10 3 C13.7 3 16.4 5.8 16.4 10 L16.4 13 A1.6 1.6 0 0 1 13.2 13 A1.6 1.6 0 0 1 10 13 A1.6 1.6 0 0 1 6.8 13 A1.6 1.6 0 0 1 3.6 13 Z ${eye(7.4, 9.2, 1.15)} ${eye(12.6, 9.2, 1.15)}"/>`],
  ["frog", `<path fill-rule="evenodd" d="M6.3 3.2 A2.9 2.9 0 0 1 9.2 6 C9.5 6 9.7 6 10 6 C10.3 6 10.5 6 10.8 6 A2.9 2.9 0 1 1 15.4 8.3 C16.5 9.4 17.2 10.9 17.2 12.4 C17.2 15.6 14 17.6 10 17.6 C6 17.6 2.8 15.6 2.8 12.4 C2.8 10.9 3.5 9.4 4.6 8.3 A2.9 2.9 0 0 1 6.3 3.2 Z ${eye(6.3, 6.1)} ${eye(13.7, 6.1)}"/>`],
  ["mouse", `<path fill-rule="evenodd" d="M5 2.4 A3.1 3.1 0 0 1 8.1 5.6 C8.7 5.4 9.3 5.3 10 5.3 C10.7 5.3 11.3 5.4 11.9 5.6 A3.1 3.1 0 1 1 15.9 8.2 C16.5 9.2 16.8 10.3 16.8 11.5 C16.8 15.4 13.8 17.8 10 17.8 C6.2 17.8 3.2 15.4 3.2 11.5 C3.2 10.3 3.5 9.2 4.1 8.2 A3.1 3.1 0 0 1 5 2.4 Z ${eye(7.7, 11.2, 1.0)} ${eye(12.3, 11.2, 1.0)}"/>`],
  ["chick", `<path fill-rule="evenodd" d="M10 2.8 C14.2 2.8 17 5.9 17 10.2 C17 14.5 14.2 17.4 10 17.4 C5.8 17.4 3 14.5 3 10.2 C3 5.9 5.8 2.8 10 2.8 Z ${eye(7.5, 8.8, 1.05)} ${eye(12.5, 8.8, 1.05)} M10 11.2 L12 12.8 L10 14.4 L8 12.8 Z"/>`],
  ["whale", `<path fill-rule="evenodd" d="M2.6 12.6 C2.6 7.4 5.8 4.8 9.8 4.8 C13 4.8 15.3 6.6 15.9 9.3 L18.2 7.2 C18.8 9.6 18 11.8 15.8 12.6 C14.8 15.6 12.6 17 9.8 17 C5.8 17 2.6 15.6 2.6 12.6 Z ${eye(6.6, 10.4, 1.0)}"/>`],
];

export const GLYPH_FAMILIES = 8;
export const GLYPH_SHAPES = ANIMALS.length;

export function glyphName(seed: number): string {
  return ANIMALS[Math.abs(seed) % GLYPH_SHAPES][0];
}

export function AgentGlyph({ seed, size = 18 }: { seed: number; size?: number }) {
  const s = Math.abs(seed);
  const [name, body] = ANIMALS[s % GLYPH_SHAPES];
  const family = Math.floor(s / GLYPH_SHAPES) % GLYPH_FAMILIES;
  return (
    <svg
      className={`aglyph aglyph-f${family}`}
      data-glyph={name}
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden
      dangerouslySetInnerHTML={{ __html: body }}
    />
  );
}
