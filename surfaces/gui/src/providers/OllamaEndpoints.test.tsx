/** Component tests for the Ollama multi-endpoint manager (Issue #455). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OllamaEndpoints } from "./OllamaEndpoints";
import type { ProviderInfo } from "../api";

const addOllamaEndpoint = vi.fn();
const updateOllamaEndpoint = vi.fn();
const deleteOllamaEndpoint = vi.fn();
const selectOllamaEndpoint = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    addOllamaEndpoint: (...args: unknown[]) => addOllamaEndpoint(...args),
    updateOllamaEndpoint: (...args: unknown[]) => updateOllamaEndpoint(...args),
    deleteOllamaEndpoint: (...args: unknown[]) => deleteOllamaEndpoint(...args),
    selectOllamaEndpoint: (...args: unknown[]) => selectOllamaEndpoint(...args),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function info(partial?: Partial<ProviderInfo>): ProviderInfo {
  return {
    name: "ollama",
    title: "Ollama (local models)",
    needs_key: false,
    configured: true,
    values: { base_url: "http://192.168.1.10:11434" },
    suggested_models: [],
    recommended_model: "qwen3-coder:30b",
    fields: [
      {
        key: "base_url",
        label: "Endpoint",
        secret: false,
        required: false,
        help: "",
        placeholder: "http://localhost:11434",
      },
    ],
    endpoints: [
      {
        id: "ep_a",
        label: "MacBook",
        base_url: "http://192.168.1.10:11434",
        enabled: true,
      },
      {
        id: "ep_b",
        label: "GPU Server",
        base_url: "http://10.0.0.5:11434",
        enabled: true,
      },
    ],
    selected_endpoint_id: "ep_a",
    ...partial,
  };
}

describe("OllamaEndpoints", () => {
  it("marks the selected endpoint and shows Use this for the other", () => {
    render(
      <OllamaEndpoints
        info={info()}
        tp="t"
        onChanged={async () => {}}
        onDetect={() => {}}
        detecting={false}
        detected={false}
      />,
    );
    expect(screen.getByTestId("t-ollama-ep-ep_a").getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("t-ollama-selected-badge").textContent).toMatch(/In use/i);
    expect(screen.getByTestId("t-ollama-select-ep_b")).toBeTruthy();
    expect(screen.queryByTestId("t-ollama-select-ep_a")).toBeNull();
  });

  it("shows Disabled and hides Use this when an endpoint is off", () => {
    render(
      <OllamaEndpoints
        info={info({
          endpoints: [
            {
              id: "ep_a",
              label: "MacBook",
              base_url: "http://192.168.1.10:11434",
              enabled: true,
            },
            {
              id: "ep_b",
              label: "GPU Server",
              base_url: "http://10.0.0.5:11434",
              enabled: false,
            },
          ],
        })}
        tp="t"
        onChanged={async () => {}}
        onDetect={() => {}}
        detecting={false}
        detected={false}
      />,
    );
    expect(screen.getByTestId("t-ollama-ep-ep_b").getAttribute("data-enabled")).toBe("false");
    expect(screen.getByText("Disabled")).toBeTruthy();
    expect(screen.queryByTestId("t-ollama-select-ep_b")).toBeNull();
  });

  it("adds an endpoint with nickname + URL and surfaces API errors", async () => {
    addOllamaEndpoint.mockResolvedValueOnce({ ok: true, endpoints: [], selected_endpoint_id: null });
    const onChanged = vi.fn(async () => {});
    render(
      <OllamaEndpoints
        info={info({ endpoints: [], selected_endpoint_id: null, values: {} })}
        tp="t"
        onChanged={onChanged}
        onDetect={() => {}}
        detecting={false}
        detected={false}
      />,
    );
    fireEvent.change(screen.getByTestId("t-ollama-add-label"), { target: { value: "LAN Box" } });
    fireEvent.change(screen.getByTestId("t-ollama-add-url"), {
      target: { value: "http://192.168.1.20:11434" },
    });
    fireEvent.click(screen.getByTestId("t-ollama-add-save"));
    await waitFor(() => expect(addOllamaEndpoint).toHaveBeenCalled());
    expect(addOllamaEndpoint).toHaveBeenCalledWith({
      label: "LAN Box",
      base_url: "http://192.168.1.20:11434",
      enabled: true,
      select: true,
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());

    addOllamaEndpoint.mockResolvedValueOnce({ ok: false, error: "Nickname is required." });
    fireEvent.click(screen.getByTestId("t-ollama-add"));
    fireEvent.change(screen.getByTestId("t-ollama-add-label"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("t-ollama-add-save"));
    await waitFor(() => expect(screen.getByTestId("t-ollama-error").textContent).toMatch(/Nickname/));
  });

  it("selects, edits, toggles, and deletes via the API helpers", async () => {
    selectOllamaEndpoint.mockResolvedValue({ ok: true });
    updateOllamaEndpoint.mockResolvedValue({ ok: true });
    deleteOllamaEndpoint.mockResolvedValue({ ok: true });
    const onChanged = vi.fn(async () => {});

    render(
      <OllamaEndpoints
        info={info()}
        tp="t"
        onChanged={onChanged}
        onDetect={() => {}}
        detecting={false}
        detected={false}
      />,
    );

    fireEvent.click(screen.getByTestId("t-ollama-select-ep_b"));
    await waitFor(() => expect(selectOllamaEndpoint).toHaveBeenCalledWith("ep_b"));

    fireEvent.click(screen.getByTestId("t-ollama-edit-ep_a"));
    fireEvent.change(screen.getByTestId("t-ollama-edit-label"), {
      target: { value: "Renamed" },
    });
    fireEvent.click(screen.getByTestId("t-ollama-edit-save"));
    await waitFor(() =>
      expect(updateOllamaEndpoint).toHaveBeenCalledWith("ep_a", {
        label: "Renamed",
        base_url: "http://192.168.1.10:11434",
      }),
    );

    const toggle = screen.getByTestId("t-ollama-toggle-ep_b").querySelector('[role="switch"]')!;
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(updateOllamaEndpoint).toHaveBeenCalledWith("ep_b", { enabled: false }),
    );

    fireEvent.click(screen.getByTestId("t-ollama-delete-ep_b"));
    await waitFor(() => expect(deleteOllamaEndpoint).toHaveBeenCalledWith("ep_b"));
    expect(onChanged.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
