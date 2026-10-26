// The OpenWorker Cloud machines list is not announced yet (2026-09-30): only internal
// builds show it, or even ask the cloud for it. Sign-in itself is untouched.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MachinesSection } from "./MachinesSection";

function stub(internal: boolean) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      if (url.endsWith("/v1/personas")) return { ok: true, json: async () => ({ personas: [], internal }) } as Response;
      if (url.includes("/v1/cloud/machines"))
        return { ok: true, json: async () => ({ session: "expired", machines: [], org: { name: "Acme" } }) } as Response;
      return { ok: true, json: async () => ({ machines: [], armed: false }) } as Response;
    }),
  );
  return urls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("cloud machines section", () => {
  it("does not exist on a release build, and the cloud is not asked", async () => {
    const urls = stub(false);
    render(<MachinesSection />);
    await waitFor(() => expect(urls.some((u) => u.endsWith("/v1/personas"))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("cloud-machines-section")).toBeNull();
    expect(urls.some((u) => u.includes("/v1/cloud/machines"))).toBe(false);
  });

  it("shows on an internal build", async () => {
    stub(true);
    render(<MachinesSection />);
    await waitFor(() => expect(screen.getByTestId("cloud-machines-section")).toBeTruthy());
    expect(screen.getByTestId("cloud-machines-section").textContent).toContain("OpenWorker Cloud");
  });
});
