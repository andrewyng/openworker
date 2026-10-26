// UX-045 frame C/D: machine rows, arming enrollment, join detection.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MachinesSection } from "./MachinesSection";

afterEach(cleanup);

const baseMachines = () => [
    {
      id: "m1",
      name: "hetzner-box",
      fingerprint: "8aaaac6fdc97e081",
      app_version: "0.2.0",
      created_at: 1_755_000_000,
      last_seen: Date.now() / 1000 - 30,
      connected: true,
    },
    {
      id: "m2",
      name: "old-macpro",
      fingerprint: "1234567890abcdef",
      app_version: "0.1.9",
      created_at: 1_754_000_000,
      last_seen: Date.now() / 1000 - 200_000,
      connected: false,
    },
  ];

const machinesPayload: { machines: any[]; armed: boolean } = {
  machines: baseMachines(),
  armed: false,
};

let armCalls = 0;
let disarmCalls = 0;

beforeEach(() => {
  armCalls = 0;
  disarmCalls = 0;
  machinesPayload.machines = baseMachines();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/v1/remote/arm")) {
        if (init?.method === "DELETE") {
          disarmCalls += 1;
          return { json: async () => ({ armed: false }) } as Response;
        }
        armCalls += 1;
        return {
          json: async () => ({
            join_url: "http://127.0.0.1:9787/j/tok123",
            expires_at: Date.now() / 1000 + 600,
          }),
        } as Response;
      }
      return { json: async () => machinesPayload } as Response;
    }),
  );
});

describe("MachinesSection", () => {
  it("renders This Mac plus machine rows with presence", async () => {
    render(<MachinesSection />);
    expect(screen.getByText("This Mac")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("hetzner-box")).toBeTruthy());
    expect(screen.getByText(/online/)).toBeTruthy();
    expect(screen.getByText(/last seen/)).toBeTruthy();
    expect(screen.getByText(/v0\.2\.0/)).toBeTruthy();
  });

  it("opening Add a machine arms enrollment and shows the one-liner; closing disarms", async () => {
    render(<MachinesSection />);
    await waitFor(() => expect(screen.getByText("hetzner-box")).toBeTruthy());
    fireEvent.click(screen.getByText("Add a machine…"));
    await waitFor(() =>
      expect(screen.getByTestId("join-command").textContent).toContain(
        "openworker join http://127.0.0.1:9787/j/tok123",
      ),
    );
    expect(armCalls).toBe(1);
    expect(screen.getByText(/Waiting for the machine/)).toBeTruthy();
    expect(screen.getByText("SSH reverse tunnel")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Close"));
    await waitFor(() => expect(disarmCalls).toBe(1));
  });

  it("a machine that appears while the card is open flips to the joined state", async () => {
    render(<MachinesSection />);
    await waitFor(() => expect(screen.getByText("hetzner-box")).toBeTruthy());
    fireEvent.click(screen.getByText("Add a machine…"));
    await waitFor(() => expect(screen.getByTestId("join-command")).toBeTruthy());
    machinesPayload.machines = [
      ...machinesPayload.machines,
      {
        id: "m3",
        name: "my-box",
        fingerprint: "feedfacecafebeef",
        app_version: "0.2.0",
        created_at: Date.now() / 1000,
        last_seen: Date.now() / 1000,
        connected: true,
      },
    ];
    await waitFor(
      () => expect(screen.getByTestId("join-success").textContent).toContain("my-box joined"),
      { timeout: 5000 },
    );
    expect(screen.getByTestId("join-success").textContent).toContain("feedfacecafebeef");
  });

  it("typed-code approval: look up shows name + fingerprint, approve confirms", async () => {
    const decisions: string[] = [];
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/v1/remote/device/")) {
        if (init?.method === "POST") {
          decisions.push(url.split("/").pop()!);
          return { ok: true, json: async () => ({ ok: true }) } as Response;
        }
        if (url.endsWith("/WXYZ-2345")) {
          return {
            ok: true,
            json: async () => ({
              user_code: "WXYZ-2345",
              name: "auth-vm",
              fingerprint: "deadbeef00112233",
              expires_at: Date.now() / 1000 + 600,
            }),
          } as Response;
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }
      return { ok: true, json: async () => machinesPayload } as Response;
    });

    render(<MachinesSection />);
    await waitFor(() => expect(screen.getByText("hetzner-box")).toBeTruthy());
    fireEvent.click(screen.getByTestId("approve-machine-button"));

    // A wrong code is a dead end, not a grant.
    const input = screen.getByTestId("approval-code-input");
    fireEvent.change(input, { target: { value: "aaaa-0000" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("approval-unknown")).toBeTruthy());

    // The right code shows the machine's claim; approving records the decision.
    fireEvent.change(input, { target: { value: "wxyz-2345" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("approval-fingerprint").textContent).toContain(
        "deadbeef00112233",
      ),
    );
    fireEvent.click(screen.getByTestId("approve-button"));
    await waitFor(() =>
      expect(screen.getByTestId("approval-result").textContent).toContain("Approved"),
    );
    expect(decisions).toEqual(["approve"]);
  });
});

// -- managed sandboxes (spec §Fly sandboxes) ------------------------------------

describe("MachinesSection sandboxes (cloud mode)", () => {
  it("offers New sandbox from the listing's cap, shows the pending row, and routes a sandbox machine's Remove through the sandbox", async () => {
    const { setAppMode } = await import("../api");
    setAppMode("cloud");
    const sandboxes: any = { sandboxes: [], cap: 3, used: 0 };
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        calls.push(`${method} ${url.replace(/^https?:\/\/[^/]+/, "")}`);
        if (url.endsWith("/v1/sandboxes") && method === "POST") {
          sandboxes.sandboxes = [
            { id: "sb-1", name: "sandbox-1", owner: "me", mine: true, state: "provisioning", phase: "provisioning", error: "", machine_id: "", connected: false, created_at: 1, updated_at: 1 },
          ];
          sandboxes.used = 1;
          return { ok: true, status: 202, json: async () => ({ sandbox: sandboxes.sandboxes[0] }) } as Response;
        }
        if (url.endsWith("/v1/sandboxes")) {
          return { ok: true, status: 200, json: async () => sandboxes } as Response;
        }
        if (url.includes("/v1/sandboxes/sb-1") && method === "DELETE") {
          sandboxes.sandboxes = [];
          sandboxes.used = 0;
          machinesPayload.machines = [];
          return { ok: true, status: 200, json: async () => ({ removed: "sb-1" }) } as Response;
        }
        return { ok: true, status: 200, json: async () => machinesPayload } as Response;
      }),
    );
    try {
      render(<MachinesSection />);
      const button = await screen.findByTestId("new-sandbox-button");
      expect(button.textContent).toContain("New sandbox (0 of 3)");
      fireEvent.click(button);
      // The pending row appears with its phase, the count moves.
      await waitFor(() => expect(screen.getByTestId("sandbox-phase-sandbox-1").textContent).toContain("creating the machine"));
      await waitFor(() => expect(screen.getByTestId("new-sandbox-button").textContent).toContain("(1 of 3)"));

      // The box joins: the machine row wears the sandbox tag and Remove goes
      // through the sandbox (machine + disk), not the plain machine delete.
      sandboxes.sandboxes[0] = { ...sandboxes.sandboxes[0], state: "running", phase: "online", machine_id: "m9", connected: true };
      machinesPayload.machines = [
        { id: "m9", name: "sandbox-1", fingerprint: "abcdefabcdef0000", app_version: "0.2.0", created_at: 2, last_seen: Date.now() / 1000, connected: true, provenance: "fly", provenance_ref: "sb-1" },
      ];
      await waitFor(() => expect(screen.getByTestId("sandbox-tag-sandbox-1")).toBeTruthy(), { timeout: 5000 });
      vi.stubGlobal("confirm", () => true);
      fireEvent.click(screen.getAllByText("Remove")[0]);
      await waitFor(() => expect(calls).toContain("DELETE /v1/sandboxes/sb-1"));
      expect(calls.some((c) => c === "DELETE /v1/machines/m9")).toBe(false);
    } finally {
      setAppMode("desktop");
    }
  });

  it("shows nothing about sandboxes when the cap is zero", async () => {
    const { setAppMode } = await import("../api");
    setAppMode("cloud");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/v1/sandboxes")) return { ok: true, status: 200, json: async () => ({ sandboxes: [], cap: 0, used: 0 }) } as Response;
        return { ok: true, status: 200, json: async () => machinesPayload } as Response;
      }),
    );
    try {
      render(<MachinesSection />);
      await waitFor(() => expect(screen.getByText("hetzner-box")).toBeTruthy());
      expect(screen.queryByTestId("new-sandbox-button")).toBeNull();
    } finally {
      setAppMode("desktop");
    }
  });
});
