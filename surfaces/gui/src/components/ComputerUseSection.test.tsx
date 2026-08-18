import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ComputerUseSection } from "./ComputerUseSection";

const SETTINGS = {
  enabled: false,
  supported: true,
  allowed_programs: [
    {
      name: "Editor",
      path: "C:\\Program Files\\Editor\\editor.exe",
      available: true,
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ComputerUseSection", () => {
  it("adds an executable and saves the explicit allowlist", async () => {
    const calls: Array<{ method: string; body?: any }> = [];
    vi.stubGlobal("__OCW_PLATFORM__", "windows");
    vi.stubGlobal("__TAURI__", {
      core: {
        invoke: vi.fn(async (command: string) =>
          command === "pick_program" ? "C:\\Office\\writer.exe" : null,
        ),
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        const method = (init?.method || "GET").toUpperCase();
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ method, body });
        if (method === "POST") {
          return {
            ok: true,
            json: async () => ({
              ...SETTINGS,
              ok: true,
              enabled: body.enabled,
              allowed_programs: body.allowed_programs.map((program: any) => ({
                ...program,
                available: true,
              })),
            }),
          } as Response;
        }
        return { ok: true, json: async () => SETTINGS } as Response;
      }),
    );

    render(<ComputerUseSection />);
    expect(await screen.findByText("Editor")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add program…" }));
    expect(await screen.findByText("writer")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Allow local computer use"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      enabled: true,
      allowed_programs: [
        { name: "Editor", path: SETTINGS.allowed_programs[0].path },
        { name: "writer", path: "C:\\Office\\writer.exe" },
      ],
    });
  });
});
