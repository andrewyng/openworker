import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import i18n from "i18next";
import { changeLanguage, detectLanguage, getLanguage, initI18n, LegacyUiLocalizer, supportedLanguages, translationResources } from "./i18n";

describe("language preferences", () => {
  afterEach(() => { cleanup(); localStorage.clear(); });

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

  it("updates a dynamic legacy aria label instead of restoring its prior state", async () => {
    initI18n();
    changeLanguage("pt-BR");
    const ui = (label: string, text: string) => createElement(
      "div",
      null,
      createElement(LegacyUiLocalizer),
      createElement("button", { "aria-label": label }, text),
    );
    const root = document.createElement("div");
    root.id = "root";
    document.body.append(root);
    const view = render(ui("Start dictation", "Start"), { container: root });
    await waitFor(() => expect(screen.getByRole("button").getAttribute("aria-label")).toBe("Iniciar ditado"));
    view.rerender(ui("Stop dictation", "Stop"));
    await waitFor(() => expect(screen.getByRole("button").getAttribute("aria-label")).toBe("Parar ditado"));
    view.unmount();
    root.remove();
  });

  it("preserves a legacy source while switching between non-English languages", async () => {
    initI18n();
    changeLanguage("pt-BR");
    const root = document.createElement("div");
    root.id = "root";
    document.body.append(root);
    const view = render(createElement("div", null, createElement(LegacyUiLocalizer), "Start dictation"), {
      container: root,
    });
    await waitFor(() => expect(root.textContent).toContain("Iniciar ditado"));
    changeLanguage("es-ES");
    await waitFor(() => expect(root.textContent).toContain("Iniciar dictado"));
    changeLanguage("en-US");
    await waitFor(() => expect(root.textContent).toContain("Start dictation"));
    view.unmount();
    root.remove();
  });
});
