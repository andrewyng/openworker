// Persona icon resolution (one source of truth for the sidebar, persona page, and sources drawer).
//
// A persona's `icon` resolves to a named glyph, legacy logo-id, or family fallback.
// Emoji manifests deliberately fall back to the line-icon set to keep the UI plain-text.
// So personas are visually distinct (Ops→wrench, Code→code, Cowork→diamond) instead of all ◆.

import { Icon, type IconName } from "./Icon";

const LEGACY: Record<string, IconName> = {
  cowork: "diamond",
  chat: "chat",
  code: "code",
  ops: "wrench",
};

// The glyphs a persona manifest may name directly (a curated, persona-appropriate subset).
const NAMED: ReadonlySet<string> = new Set<IconName>([
  "sparkle",
  "diamond",
  "chat",
  "code",
  "wrench",
  "sliders",
  "search",
  "clock",
  "folder",
  "shield",
  "table",
  "plug",
  "audit",
  "branch",
  "pencil",
]);

export function isEmojiIcon(icon?: string): boolean {
  return !!icon && /[^\x00-\x7F]/.test(icon);
}

export function personaGlyph(icon?: string, folderScoped?: boolean): IconName {
  if (icon && NAMED.has(icon)) return icon as IconName;
  if (icon && LEGACY[icon]) return LEGACY[icon];
  return folderScoped ? "code" : "sparkle";
}

/** Renders a persona's icon with the product's line-icon set. */
export function PersonaGlyph({
  icon,
  folderScoped,
  size = 14,
}: {
  icon?: string;
  folderScoped?: boolean;
  size?: number;
}) {
  return <Icon name={personaGlyph(icon, folderScoped)} size={size} />;
}
