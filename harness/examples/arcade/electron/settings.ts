import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ProviderSettings, Settings, Brief } from "../shared/contracts.js";
import {
  atomicWrite,
  createDataDirectory,
  readBounded,
  realDirectory,
} from "./files.js";
export function validateProvider(value: unknown): ProviderSettings {
  const p = value as ProviderSettings;
  if (
    !p ||
    typeof p.name !== "string" ||
    !/^[a-zA-Z0-9_-]{1,64}$/.test(p.name) ||
    !["responses", "chat-completions", "anthropic"].includes(p.api) ||
    typeof p.model !== "string" ||
    !/^[a-zA-Z0-9_.:/-]{1,256}$/.test(p.model) ||
    typeof p.preset !== "string" ||
    p.preset.length > 40
  )
    throw new Error("Enter a provider, API and exact model ID.");
  let url: URL;
  try {
    url = new URL(p.baseUrl);
  } catch {
    throw new Error("Enter a valid provider URL.");
  }
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["https:", "http:"].includes(url.protocol) ||
    (url.protocol === "http:" &&
      !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
  )
    throw new Error(
      "Use HTTPS, or a local model on localhost, without credentials in the URL.",
    );
  return {
    name: p.name,
    preset: p.preset,
    api: p.api,
    baseUrl: url.href.replace(/\/$/, ""),
    model: p.model,
  };
}
export function validateBrief(value: unknown): Brief {
  const b = value as Brief;
  if (
    !b ||
    typeof b.idea !== "string" ||
    !b.idea.trim() ||
    b.idea.length > 8000
  )
    throw new Error("Describe the game you want to make.");
  for (const key of ["kind", "vibe", "difficulty"] as const)
    if (typeof b[key] !== "string" || b[key].length > 120)
      throw new Error("Invalid game preferences.");
  return {
    idea: b.idea.trim(),
    ...(b.engine
      ? {
          engine:
            b.engine === "three" ? ("three" as const) : ("canvas" as const),
        }
      : {}),
    kind: b.kind,
    vibe: b.vibe,
    difficulty: b.difficulty,
  };
}
export class SettingsStore {
  private readonly path: string;
  private value: Settings;
  constructor(readonly dataDir: string) {
    createDataDirectory(dataDir);
    this.path = join(dataDir, "settings.json");
    if (existsSync(this.path)) {
      try {
        const saved = JSON.parse(readBounded(this.path));
        if (saved.version !== 1 || typeof saved.gamesRoot !== "string")
          throw new Error();
        this.value = {
          version: 1,
          gamesRoot: saved.gamesRoot,
          provider: saved.provider ? validateProvider(saved.provider) : null,
        };
      } catch {
        throw new Error(
          "Arcade settings could not be read. Your data has been preserved.",
        );
      }
    } else {
      this.value = {
        version: 1,
        gamesRoot: createDataDirectory(join(dataDir, "games")),
        provider: null,
      };
      this.persist();
    }
  }
  get(): Settings {
    return structuredClone(this.value);
  }
  provider(provider: ProviderSettings): void {
    const next = { ...this.value, provider: validateProvider(provider) };
    atomicWrite(this.path, JSON.stringify(next, null, 2));
    this.value = next;
  }
  gamesRoot(path: string): void {
    const next = { ...this.value, gamesRoot: realDirectory(path) };
    atomicWrite(this.path, JSON.stringify(next, null, 2));
    this.value = next;
  }
  private persist(): void {
    atomicWrite(this.path, JSON.stringify(this.value, null, 2));
  }
}
