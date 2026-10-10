// A folder on a machine: type a path, Check asks the machine, Use only after a pass.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MachineFolderDialog } from "./MachineFolderDialog";
import type { Machine } from "../api";

const MACHINE = { id: "m1", name: "linux-exe" } as unknown as Machine;

function stubFetch(answers: Record<string, unknown>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(url);
      if (url.includes("/workspaces/recent") || url.includes("recent")) {
        return { ok: true, json: async () => ({ workspaces: [{ path: "/home/ubuntu/code/billing", name: "billing", exists: true }] }) } as Response;
      }
      const body = JSON.parse(String(init?.body || "{}"));
      const a = answers[body.path] ?? { ok: false, error: `No folder at ${body.path} on linux-exe.` };
      return { ok: true, json: async () => a } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MachineFolderDialog", () => {
  it("checks a typed path on the machine and enables Use only after a pass", async () => {
    stubFetch({ "/home/ubuntu/code/invoices": { ok: true, path: "/home/ubuntu/code/invoices", git_branch: "main" } });
    const onPick = vi.fn();
    render(<MachineFolderDialog coworkerName="Coworker" machine={MACHINE} onPick={onPick} onCancel={() => {}} />);
    const use = screen.getByTestId("machine-folder-use") as HTMLButtonElement;
    expect(use.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("machine-folder-input"), { target: { value: "/home/ubuntu/code/invoice" } });
    fireEvent.click(screen.getByTestId("machine-folder-check"));
    await waitFor(() => expect(screen.getByTestId("machine-folder-status").textContent).toContain("No folder at /home/ubuntu/code/invoice"));
    expect(use.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("machine-folder-input"), { target: { value: "/home/ubuntu/code/invoices" } });
    expect(screen.getByTestId("machine-folder-status").textContent).toBe(""); // a new path needs a new check
    fireEvent.submit(screen.getByTestId("machine-folder-input").closest("form")!);
    await waitFor(() => expect(screen.getByTestId("machine-folder-status").textContent).toContain("git branch main"));
    expect(use.disabled).toBe(false);
    fireEvent.click(use);
    expect(onPick).toHaveBeenCalledWith("/home/ubuntu/code/invoices", "main");
  });

  it("a recent folder on the machine is checked and used in one click", async () => {
    stubFetch({ "/home/ubuntu/code/billing": { ok: true, path: "/home/ubuntu/code/billing", git_branch: null } });
    const onPick = vi.fn();
    render(<MachineFolderDialog coworkerName="Coworker" machine={MACHINE} onPick={onPick} onCancel={() => {}} />);
    const recent = await screen.findByTestId("machine-folder-recent");
    fireEvent.click(recent);
    await waitFor(() => expect(onPick).toHaveBeenCalledWith("/home/ubuntu/code/billing", null));
  });
});
