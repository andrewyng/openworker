// The fleet-change announcement has to carry the machine NAMES, not just the ids:
// the Settings rail's machine picker shows the name, and a rename leaves the id set
// untouched — so an id-only fingerprint kept the old name in the picker until the
// user reloaded the page.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MACHINES_CHANGED } from "../api";
import { MachinesSection } from "./MachinesSection";

afterEach(cleanup);

const machine = (name: string) => ({
  id: "m1",
  name,
  fingerprint: "8aaaac6fdc97e081",
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: Date.now() / 1000 - 30,
  connected: true,
});

let payload: { machines: any[]; armed: boolean };

beforeEach(() => {
  payload = { machines: [machine("hetzner-box")], armed: false };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PATCH" && url.includes("/v1/machines/")) {
        payload = { machines: [machine("renamed-box")], armed: false };
        return {
          ok: true,
          json: async () => ({ machine: payload.machines[0] }),
        } as Response;
      }
      return { ok: true, json: async () => payload } as Response;
    }),
  );
});

/** Counts the fleet-change announcements the component dispatches. */
const announcements = () => {
  const seen: Event[] = [];
  const listener = (e: Event) => seen.push(e);
  window.addEventListener(MACHINES_CHANGED, listener);
  return {
    seen,
    stop: () => window.removeEventListener(MACHINES_CHANGED, listener),
  };
};

describe("MachinesSection fleet announcements", () => {
  it("announces a rename, so the other pickers show the new name", async () => {
    const { seen, stop } = announcements();
    try {
      render(<MachinesSection />);
      await waitFor(() => expect(screen.getByText("hetzner-box")).toBeTruthy());

      fireEvent.click(screen.getByText("Rename"));
      const input = screen.getByDisplayValue("hetzner-box");
      fireEvent.change(input, { target: { value: "renamed-box" } });
      fireEvent.keyDown(input, { key: "Enter" });

      // The row itself shows the new name...
      await waitFor(() => expect(screen.getByText("renamed-box")).toBeTruthy());
      // ...and the fleet change is announced, so the Settings rail's picker reloads.
      await waitFor(() => expect(seen.length).toBe(1));
    } finally {
      stop();
    }
  });

  it("announces nothing while the fleet stays empty", async () => {
    const { seen, stop } = announcements();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      payload = { machines: [], armed: false };
      render(<MachinesSection />);
      await waitFor(() => expect(screen.getByText("This Mac")).toBeTruthy());
      // One poll cycle later the fleet is still empty — nothing changed, so nothing
      // is announced (an empty fleet used to look like a change on every poll).
      await vi.advanceTimersByTimeAsync(16_000);
      expect(seen.length).toBe(0);
    } finally {
      vi.useRealTimers();
      stop();
    }
  });
});
