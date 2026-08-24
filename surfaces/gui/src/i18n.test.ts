import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { changeLanguage, detectLanguage, getLanguage, initI18n, supportedLanguages, translationResources } from "./i18n";

describe("language preferences", () => {
  afterEach(() => localStorage.clear());

  it("detects supported browser languages and falls back to English", () => {
    expect(detectLanguage(["pt-PT", "en-US"])).toBe("pt-BR");
    expect(detectLanguage(["es-MX"])).toBe("es-ES");
    expect(detectLanguage(["fr-FR"])).toBe("en-US");
  });

  it("uses a saved valid preference over browser detection", () => {
    localStorage.setItem("openwork-language", "es-ES");
    expect(getLanguage()).toBe("es-ES");
    localStorage.setItem("openwork-language", "invalid");
    expect(getLanguage()).not.toBe("invalid");
  });

  it("updates the document language and persists an explicit choice", () => {
    initI18n();
    changeLanguage("pt-BR");
    expect(localStorage.getItem("openwork-language")).toBe("pt-BR");
    expect(document.documentElement.lang).toBe("pt-BR");
    expect(i18n.language).toBe("pt-BR");
  });

  it("keeps the same semantic translation keys in every catalog", () => {
    const keys = (value: object, prefix = ""): string[] =>
      Object.entries(value).flatMap(([key, child]) =>
        typeof child === "object" ? keys(child as object, `${prefix}${key}.`) : [`${prefix}${key}`],
      );
    const english = keys(translationResources["en-US"].translation).sort();
    for (const language of supportedLanguages) {
      expect(keys(translationResources[language].translation).sort()).toEqual(english);
    }
  });
});
