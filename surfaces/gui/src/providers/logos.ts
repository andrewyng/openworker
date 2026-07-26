// Provider logo registry (UX-DECISIONS §39): official brand marks for the onboarding
// provider gallery, bundled rather than fetched (same posture as the connector
// registry — no CDN at runtime). Keys are /v1/providers names; unknown names get no
// mark (the gallery falls back to a neutral monogram). PROVIDER_ORDER is the gallery
// order — recognition first, long tail behind the scroll fold.
//
// Provenance: the SVGs come from the MIT-licensed lobe-icons set. `lmstudio.webp` does
// NOT — it is LM Studio's own app icon from lmstudio.ai, used nominatively to identify
// their product. It is also the only raster here, and unlike the flat SVG marks it
// carries its own background, which is why ProviderMark renders it edge-to-edge.

import anthropic from "./logos/anthropic.svg";
import openai from "./logos/openai.svg";
import gemini from "./logos/gemini.svg";
import ollama from "./logos/ollama.svg";
import lmstudio from "./logos/lmstudio.webp";
import fireworks from "./logos/fireworks.svg";
import together from "./logos/together.svg";
import zai from "./logos/zai.svg";
import kimi from "./logos/kimi.svg";
import deepseek from "./logos/deepseek.svg";
import mistral from "./logos/mistral.svg";
import qwen from "./logos/qwen.svg";
import minimax from "./logos/minimax.svg";
import xai from "./logos/xai.svg";
import meta from "./logos/meta.svg";

export const PROVIDER_LOGOS: Record<string, string> = {
  anthropic,
  openai,
  gemini,
  meta,
  ollama,
  lmstudio,
  fireworks,
  together,
  zai,
  kimi,
  deepseek,
  mistral,
  qwen,
  minimax,
  xai,
};

// Marks that ARE an app icon — they bring their own background and corner radius — rather
// than a flat glyph meant to sit on the light plate. These fill the plate edge-to-edge;
// floating one at 60% would read as a rounded square inside a rounded square.
export const FULL_BLEED_LOGOS = new Set(["lmstudio"]);

export const PROVIDER_ORDER = [
  "anthropic",
  "openai",
  "gemini",
  "meta",
  // The two local runtimes sit together, right after the hosted names people recognize.
  "ollama",
  "lmstudio",
  "fireworks",
  "together",
  "zai",
  "kimi",
  "deepseek",
  "mistral",
  "qwen",
  "minimax",
  "xai",
];

export function providerRank(name: string): number {
  const i = PROVIDER_ORDER.indexOf(name);
  return i === -1 ? PROVIDER_ORDER.length : i;
}
