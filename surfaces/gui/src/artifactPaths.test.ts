import { describe, expect, it } from "vitest";
import { artifactBaseName, decodeArtifactPath, findArtifact } from "./artifactPaths";

const external = {
  path: "/Users/example/Documents/新闻摘要_2026-07-27.md",
  abs_path: "/Users/example/Documents/新闻摘要_2026-07-27.md",
  name: "新闻摘要_2026-07-27.md",
};

describe("artifact paths", () => {
  it("decodes URL-encoded Unicode paths but preserves malformed literal percent names", () => {
    expect(decodeArtifactPath("%E6%96%B0%E9%97%BB%E6%91%98%E8%A6%81.md")).toBe("新闻摘要.md");
    expect(decodeArtifactPath("progress-100%.md")).toBe("progress-100%.md");
  });

  it("extracts names with POSIX or Windows separators", () => {
    expect(artifactBaseName("reports/summary.md")).toBe("summary.md");
    expect(artifactBaseName("C:\\Reports\\summary.md")).toBe("summary.md");
  });

  it("matches encoded bare names and relative paths against external artifacts", () => {
    expect(
      findArtifact([external], "%E6%96%B0%E9%97%BB%E6%91%98%E8%A6%81_2026-07-27.md"),
    ).toBe(external);
    expect(findArtifact([external], "Documents/新闻摘要_2026-07-27.md")).toBe(external);
    expect(findArtifact([external], external.path)).toBe(external);
  });
});
