import { describe, expect, it } from "vitest";

import { detectProvider } from "./api";

describe("detectProvider", () => {
  it("recognizes TrustedRouter keys before the generic OpenAI prefix", () => {
    expect(detectProvider("sk-tr-v1-test")).toBe("trustedrouter");
    expect(detectProvider("sk-proj-test")).toBe("openai");
  });
});
