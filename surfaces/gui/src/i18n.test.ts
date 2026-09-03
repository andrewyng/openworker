import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import zh from "./locales/zh.json";

interface LocaleTree {
  [key: string]: string | LocaleTree;
}

const IMPORTANT_KEYS = [
  "settings.voice_dl_progress",
  "settings.voice_installed",
  "settings.voice_not_installed",
  "personas.installed_other",
  "personas.disable_warning_other",
  "personas.tools_label",
  "personas.risk_label",
  "transcript.step.auto_allowed_tip",
  "manage.tool_asks_approval",
  "manage.id_title",
  "automations.empty_state",
  "composer.pdf_too_big",
  "composer.pdf_too_many_pages",
  "composer.pdf_unreadable",
  "composer.listening_sr",
] as const;

const values: Record<string, Record<string, string | number>> = {
  "settings.voice_dl_progress": { done: "1 MiB", total: "2 MiB" },
  "settings.voice_installed": { size: "141 MiB" },
  "settings.voice_not_installed": { size: "141 MiB" },
  "personas.installed_other": { count: 2 },
  "personas.disable_warning_other": { count: 2 },
  "personas.tools_label": { tools: "read_file" },
  "personas.risk_label": { risk: "read" },
  "transcript.step.auto_allowed_tip": { name: "read_file" },
  "manage.tool_asks_approval": { name: "send_message", kind: "write" },
  "manage.id_title": { id: "U123" },
  "composer.pdf_too_big": { name: "report.pdf", mb: "12.5", limit: 10 },
  "composer.pdf_too_many_pages": { name: "report.pdf", pages: 24, limit: 20 },
  "composer.pdf_unreadable": { name: "report.pdf", error: "invalid PDF" },
  "composer.listening_sr": { time: "0:12" },
};

function flatten(tree: LocaleTree, prefix = "", result: Record<string, string> = {}) {
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") result[path] = value;
    else flatten(value, path, result);
  }
  return result;
}

function placeholders(value: string) {
  return [...value.matchAll(/{{\s*([\w.]+)(?:\s*,[^}]*)?\s*}}/g)]
    .map((match) => match[1])
    .sort();
}

const flatEn = flatten(en);
const flatZh = flatten(zh);

describe("locale contracts", () => {
  it("keeps English and Chinese key sets in parity", () => {
    // Chinese has a single plural category, so `*_one` variants exist only in English.
    const missingInZh = Object.keys(flatEn).filter((key) => !key.endsWith("_one") && !(key in flatZh));
    const missingInEn = Object.keys(flatZh).filter((key) => !(key in flatEn));
    expect(missingInZh, "keys missing from zh.json").toEqual([]);
    expect(missingInEn, "keys missing from en.json").toEqual([]);
  });

  it("keeps interpolation placeholders aligned between English and Chinese", () => {
    for (const key of Object.keys(flatEn)) {
      if (!(key in flatZh)) continue;
      expect(placeholders(flatZh[key]), key).toEqual(placeholders(flatEn[key]));
    }
  });

  it("defines every important runtime key in both locales", () => {
    for (const key of IMPORTANT_KEYS) {
      expect(flatEn[key], `missing English key: ${key}`).toBeTypeOf("string");
      expect(flatZh[key], `missing Chinese key: ${key}`).toBeTypeOf("string");
    }
  });

  it("fully interpolates important Chinese runtime strings", async () => {
    const instance = createInstance();
    await instance.init({
      resources: { zh: { translation: zh } },
      lng: "zh",
      fallbackLng: false,
      interpolation: { escapeValue: false },
    });

    for (const key of IMPORTANT_KEYS) {
      const rendered = instance.t(key, values[key] ?? {});
      expect(rendered, key).not.toMatch(/{{\s*[\w.]+(?:\s*,[^}]*)?\s*}}/);
    }
  });
});
