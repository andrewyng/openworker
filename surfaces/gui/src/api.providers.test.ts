import { afterEach, expect, it, vi } from "vitest";
import { engineBase, getLocalModels } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("keeps machine routing and bounds model discovery requests", async () => {
  const signal = new AbortController().signal;
  const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(signal);
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ models: [] })));
  vi.stubGlobal("fetch", request);

  await getLocalModels("deepinfra", "machine-1");

  expect(request).toHaveBeenCalledWith(
    `${engineBase("machine-1")}/v1/providers/deepinfra/models`,
    expect.objectContaining({ signal }),
  );
  expect(timeout).toHaveBeenCalledWith(15_000);
});

it("rejects an HTTP failure instead of treating its JSON as a model catalog", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }),
  ));

  await expect(getLocalModels("deepinfra")).rejects.toThrow("HTTP 503");
});
