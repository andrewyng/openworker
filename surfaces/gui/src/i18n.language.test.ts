import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCurrentLanguage, getStoredLanguage, initI18n, setLanguage } from "./i18n";

beforeEach(() => { localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); });

function systemLanguage(language: string) {
  vi.spyOn(navigator, "language", "get").mockReturnValue(language);
}

describe("language selection", () => {
  it.each(["zh-TW", "zh-Hant", "zh-Hant-TW", "zh-HK", "zh-MO", "zh-hant-hk"])("resolves %s to Traditional Chinese", async (language) => {
    systemLanguage(language);
    const instance = await initI18n();
    expect(getCurrentLanguage()).toBe("zh-TW");
    expect(instance.t("settings.files_save")).toBe("儲存");
    expect(document.documentElement.lang).toBe("zh-TW");
  });

  it.each(["zh", "zh-CN", "zh-SG", "zh-Hans", "zh-Hans-TW"])("preserves Simplified Chinese for %s", async (language) => {
    systemLanguage(language);
    const instance = await initI18n();
    expect(getCurrentLanguage()).toBe("zh");
    expect(instance.t("settings.files_save")).toBe("保存");
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("gives explicit script precedence over region", async () => {
    systemLanguage("zh-Hant-CN");
    await initI18n();
    expect(getCurrentLanguage()).toBe("zh-TW");
  });

  it("keeps English as the unsupported-language fallback", async () => {
    systemLanguage("de-DE");
    await initI18n();
    expect(getCurrentLanguage()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("persists Traditional Chinese and restores it over the system locale", async () => {
    systemLanguage("en-US");
    await initI18n();
    await setLanguage("zh-TW");
    expect(getStoredLanguage()).toBe("zh-TW");
    expect(document.documentElement.lang).toBe("zh-TW");
    const instance = await initI18n();
    expect(getCurrentLanguage()).toBe("zh-TW");
    expect(instance.t("settings.files_save")).toBe("儲存");
  });

  it("keeps a previously saved zh choice Simplified on Traditional systems", async () => {
    systemLanguage("zh-TW");
    localStorage.setItem("openworker.lang", "zh");
    await initI18n();
    expect(getCurrentLanguage()).toBe("zh");
  });

  it("can switch immediately and return to the system locale", async () => {
    systemLanguage("zh-TW");
    const instance = await initI18n();
    await setLanguage("en");
    expect(instance.t("settings.files_save")).toBe("Save");
    expect(document.documentElement.lang).toBe("en");
    await setLanguage("zh");
    expect(instance.t("settings.files_save")).toBe("保存");
    await setLanguage(null);
    expect(getStoredLanguage()).toBeNull();
    expect(instance.t("settings.files_save")).toBe("儲存");
    expect(document.documentElement.lang).toBe("zh-TW");
  });

  it("ignores invalid saved preferences", async () => {
    systemLanguage("zh-TW");
    localStorage.setItem("openworker.lang", "unsupported");
    await initI18n();
    expect(getStoredLanguage()).toBeNull();
    expect(getCurrentLanguage()).toBe("zh-TW");
  });

  it("switches even when persistence is unavailable", async () => {
    systemLanguage("zh-TW");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    await initI18n();
    expect(getCurrentLanguage()).toBe("zh-TW");
    await setLanguage("en");
    expect(getCurrentLanguage()).toBe("en");
  });
});
