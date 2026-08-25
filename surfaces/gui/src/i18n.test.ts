import { beforeEach, describe, expect, it, vi } from "vitest";

import { getLanguage, setLanguage, translate } from "./i18n";

describe("i18n", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("uses English by default and persists a selected language", () => {
    expect(getLanguage()).toBe("en");

    setLanguage("zh-CN");

    expect(getLanguage()).toBe("zh-CN");
    expect(localStorage.getItem("openwork-language")).toBe("zh-CN");
  });

  it("translates known strings and falls back to English", () => {
    expect(translate("zh-CN", "settings.language")).toBe("界面语言");
    expect(translate("zh-CN", "settings.english")).toBe("English");
    expect(translate("zh-CN", "missing.translation.key")).toBe("missing.translation.key");
    expect(translate("en", "provider.connected")).toBe("Connected");
  });

  it("announces language changes to mounted providers", () => {
    const listener = vi.fn();
    window.addEventListener("openwork:language", listener);

    setLanguage("zh-CN");

    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener("openwork:language", listener);
  });
});
