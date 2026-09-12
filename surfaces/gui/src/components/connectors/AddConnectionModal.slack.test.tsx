import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";

// issue #658 — "Add to Slack" dead-ended on a bare "not signed in" when the
// cloud-status poll still said signed_in:true but the session had actually
// expired (begin_managed_connect is the authoritative check and correctly
// rejects it). The fix: recover into the sign-in flow instead of leaving a
// dead error, then automatically resume the Slack connect once signed in.

const connectManaged =
  vi.fn<(name: string, options?: { access?: "read" | "write" }) => Promise<{ ok: boolean; error?: string; signed_in?: boolean }>>();
const cloudLogin = vi.fn<() => Promise<{ ok: boolean }>>(async () => ({ ok: true }));
let signInDone: ((s: { signed_in: boolean; account: string; user_id: string } | null) => void) | null = null;
const waitForCloudSignIn = vi.fn((onDone: (s: { signed_in: boolean; account: string; user_id: string } | null) => void) => {
  signInDone = onDone;
  return () => {
    signInDone = null;
  };
});
const announceCloudChanged = vi.fn();

vi.mock("../../api", () => ({
  connectConnector: vi.fn(),
  connectManaged: (name: string, options?: { access?: "read" | "write" }) => connectManaged(name, options),
  connectMcpBacked: vi.fn(),
  getConnectors: vi.fn(async () => []),
  cloudLogin: () => cloudLogin(),
  waitForCloudSignIn: (onDone: (s: { signed_in: boolean; account: string; user_id: string } | null) => void) =>
    waitForCloudSignIn(onDone),
  announceCloudChanged: () => announceCloudChanged(),
}));

import { AddConnectionModal } from "./AddConnectionModal";
import type { Connector } from "../../api";

const slackConnector = (): Connector => ({
  name: "slack",
  title: "Slack",
  icon: "slack",
  blurb: "",
  auth: "oauth",
  two_way: true,
  channels: true,
  available: true,
  fields: [],
  instructions: [],
  connected: false,
  account: null,
  enabled: true,
  brand_color: "#611f69",
  logo: "slack",
  allowed_users: [],
  tools: [],
  managed: true,
  managed_profile: false,
});

afterEach(() => {
  cleanup();
  connectManaged.mockReset();
  cloudLogin.mockClear();
  waitForCloudSignIn.mockClear();
  announceCloudChanged.mockClear();
  signInDone = null;
});

describe("AddConnectionModal — Slack one-click (issue #658)", () => {
  it("shows the Add to Slack button when the cloud status says signed in", () => {
    render(
      <AddConnectionModal
        c={slackConnector()}
        cloud={{ signed_in: true, account: "a@b.com", user_id: "u1" }}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByTestId("modal-add-to-slack")).toBeTruthy();
    expect(screen.queryByTestId("inline-cloud-sign-in")).toBeNull();
  });

  it("a stale signed-in status: connect rejects as not-signed-in, and the pane recovers into sign-in instead of dead-ending", async () => {
    // Frontend still thinks it's signed in (last poll), but the session actually
    // expired — begin_managed_connect's fresh_access_token check fails server-side.
    connectManaged.mockResolvedValueOnce({ ok: false, error: "not signed in", signed_in: false });
    render(
      <AddConnectionModal
        c={slackConnector()}
        cloud={{ signed_in: true, account: "a@b.com", user_id: "u1" }}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("modal-add-to-slack"));
    await waitFor(() => expect(connectManaged).toHaveBeenCalledWith("slack", undefined));
    // No dead-end: a real sign-in button appears, not just inert red text.
    await waitFor(() => expect(screen.getByTestId("inline-cloud-sign-in")).toBeTruthy());
    expect(screen.queryByText("not signed in")).toBeNull();
  });

  it("signing back in from the recovered pane automatically resumes the Slack connect", async () => {
    connectManaged
      .mockResolvedValueOnce({ ok: false, error: "not signed in", signed_in: false })
      .mockResolvedValueOnce({ ok: true });
    render(
      <AddConnectionModal
        c={slackConnector()}
        cloud={{ signed_in: true, account: "a@b.com", user_id: "u1" }}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("modal-add-to-slack"));
    await waitFor(() => expect(screen.getByTestId("inline-cloud-sign-in")).toBeTruthy());

    fireEvent.click(screen.getByTestId("inline-cloud-sign-in"));
    await waitFor(() => expect(cloudLogin).toHaveBeenCalled());
    await waitFor(() => expect(waitForCloudSignIn).toHaveBeenCalled());

    // The browser sign-in lands; the poll callback fires with the fresh status.
    signInDone?.({ signed_in: true, account: "a@b.com", user_id: "u1" });

    // Slack connect is retried on its own — the user never re-clicks anything.
    await waitFor(() => expect(connectManaged).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("modal-add-to-slack")).toBeTruthy());
    expect(screen.getByTestId("modal-add-to-slack").textContent).toMatch(/browser/i);
  });

  it("a genuine sign-in error (not a session-expiry) still surfaces as an error, not a sign-in prompt", async () => {
    connectManaged.mockResolvedValueOnce({ ok: false, error: "cloud unreachable" });
    render(
      <AddConnectionModal
        c={slackConnector()}
        cloud={{ signed_in: true, account: "a@b.com", user_id: "u1" }}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("modal-add-to-slack"));
    await waitFor(() => expect(screen.getByText("cloud unreachable")).toBeTruthy());
    expect(screen.queryByTestId("inline-cloud-sign-in")).toBeNull();
  });

  it("signed out from the start: sign-in also auto-continues into the Slack connect", async () => {
    connectManaged.mockResolvedValueOnce({ ok: true });
    render(
      <AddConnectionModal
        c={slackConnector()}
        cloud={{ signed_in: false, account: "", user_id: "" }}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByTestId("inline-cloud-sign-in")).toBeTruthy();
    fireEvent.click(screen.getByTestId("inline-cloud-sign-in"));
    await waitFor(() => expect(waitForCloudSignIn).toHaveBeenCalled());
    signInDone?.({ signed_in: true, account: "a@b.com", user_id: "u1" });
    await waitFor(() => expect(connectManaged).toHaveBeenCalledWith("slack", undefined));
    await waitFor(() => expect(screen.getByTestId("modal-add-to-slack").textContent).toMatch(/browser/i));
  });
});
