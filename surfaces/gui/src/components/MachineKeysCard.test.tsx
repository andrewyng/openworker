// OPE-149: browser-sealed key deploy. The card seals IN THE TEST (real
// crypto) and the assertion that matters most: the plaintext key never
// appears in any request body — the backend only ever sees ciphertext.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MachinesSection } from "./MachinesSection";
import { setWalletAvailable } from "../api";
import { x25519 } from "@noble/curves/ed25519.js";

afterEach(() => {
  cleanup();
  setWalletAvailable(true);
});

const sealPriv = x25519.utils.randomSecretKey();
const sealPubB64 = btoa(String.fromCharCode(...x25519.getPublicKey(sealPriv)));

const machine = {
  id: "m1",
  name: "cloud-box",
  fingerprint: "8aaaac6fdc97e081",
  app_version: "0.2.0",
  created_at: 1_755_000_000,
  last_seen: Date.now() / 1000,
  connected: true,
  seal_pubkey: sealPubB64,
  seal_fingerprint: "feedfacecafe0123",
};

let deployBodies: any[] = [];

beforeEach(() => {
  setWalletAvailable(false);
  deployBodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/v1/machines/m1/secrets")) {
        if (init?.method === "POST") {
          deployBodies.push(JSON.parse(String(init.body)));
          return { ok: true, json: async () => ({ ok: true, deployed: ["provider:anthropic"] }) } as Response;
        }
        return { ok: true, json: async () => ({ secrets: [] }) } as Response;
      }
      if (url.endsWith("/v1/machines")) {
        return { ok: true, json: async () => ({ machines: [machine], armed: false }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
});

describe("MachineKeysCard", () => {
  it("seals in the tab: the request carries ciphertext, never the key", async () => {
    render(<MachinesSection />);
    await waitFor(() => expect(screen.getByText("cloud-box")).toBeTruthy());
    fireEvent.click(screen.getByTestId("keys-cloud-box"));
    expect(screen.getByTestId("seal-fingerprint").textContent).toContain("feedfacecafe0123");

    fireEvent.change(screen.getByTestId("keys-value"), {
      target: { value: "sk-super-secret-123" },
    });
    fireEvent.click(screen.getByTestId("keys-deploy"));
    await waitFor(() => expect(screen.getByTestId("keys-note").textContent).toContain("Deployed"));

    expect(deployBodies).toHaveLength(1);
    const body = deployBodies[0];
    expect(body.profiles).toEqual(["provider:anthropic"]);
    expect(typeof body.sealed_b64).toBe("string");
    expect(body.sealed_b64.length).toBeGreaterThan(60);
    expect(body.hashes["provider:anthropic"]).toMatch(/^[0-9a-f]{64}$/);
    // The doctrine assertion: plaintext never leaves the tab.
    expect(JSON.stringify(body)).not.toContain("sk-super-secret-123");
  });
});
