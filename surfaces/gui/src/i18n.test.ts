import { describe, expect, it } from "vitest";
import { interpolate, translate } from "./i18n";

describe("i18n translate", () => {
  it("passes English keys through when the lang is en", () => {
    expect(translate("New chat", "en")).toBe("New chat");
  });

  it("translates known keys into zh-CN", () => {
    expect(translate("New chat", "zh-CN")).toBe("新建会话");
    expect(translate("Settings", "zh-CN")).toBe("设置");
  });

  it("falls back to the English key for untranslated strings", () => {
    expect(translate("Some string nobody translated", "zh-CN")).toBe(
      "Some string nobody translated",
    );
  });

  it("interpolates {{vars}} in the translated value", () => {
    expect(
      translate("approved · {{mode}}", "zh-CN", { mode: "manual" }),
    ).toBe("已批准 · manual");
  });

  it("interpolates {{vars}} even without a provider (default t)", () => {
    expect(interpolate("Delete {{name}}", { name: "weekly-report" })).toBe(
      "Delete weekly-report",
    );
  });
});
