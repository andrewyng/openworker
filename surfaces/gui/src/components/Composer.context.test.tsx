import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const model = "acme:code-model";
const savedConfig = { [model]: { context_size: 655_360 } };

afterEach(cleanup);

describe("Composer context window", () => {
  it.each([
    {
      name: "uses the saved window when server metadata is missing",
      contextWindow: undefined,
      modelConfig: savedConfig,
      expected: "Context 14% · 90.2k of 655k",
    },
    {
      name: "keeps the server window ahead of the saved setting",
      contextWindow: 200_000,
      modelConfig: savedConfig,
      expected: "Context 45% · 90.2k of 200k",
    },
    {
      name: "does not use another model's saved window",
      contextWindow: undefined,
      modelConfig: { "acme:other-model": { context_size: 655_360 } },
      expected: "In context: 90.2k · window unknown",
    },
  ])("$name", ({ contextWindow, modelConfig, expected }) => {
    render(
      <Composer
        mode="interactive"
        model={model}
        models={[model]}
        running={false}
        connected={true}
        onSend={vi.fn()}
        onInterrupt={vi.fn()}
        onModeChange={vi.fn()}
        onModelChange={vi.fn()}
        usage={{
          context: 90_200,
          byModel: { [model]: { model, input: 90_200, output: 200, cache_read: 0, cache_write: 0 } },
        }}
        contextWindow={contextWindow}
        modelConfig={modelConfig}
        contextBar={true}
      />,
    );

    expect(screen.getByTestId("usage-chip").getAttribute("aria-label")).toBe(expected);
  });
});
