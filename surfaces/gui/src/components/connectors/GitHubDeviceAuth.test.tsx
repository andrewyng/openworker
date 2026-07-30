import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GitHubDeviceAuth } from "./GitHubDeviceAuth";

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  poll: vi.fn(),
  cancel: vi.fn(),
  openExternal: vi.fn(),
}));

vi.mock("../../api", () => ({
  startGithubDeviceAuth: mocks.start,
  pollGithubDeviceAuth: mocks.poll,
  cancelGithubDeviceAuth: mocks.cancel,
}));
vi.mock("../../tauri", () => ({ openExternal: mocks.openExternal }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const started = {
  ok: true,
  flow_id: "opaque-flow",
  user_code: "WDJB-MJHT",
  verification_uri: "https://github.com/login/device",
  expires_in: 900,
  interval: 5,
};

describe("GitHubDeviceAuth", () => {
  it("starts locally and displays the user code while polling", async () => {
    mocks.start.mockResolvedValue(started);
    mocks.poll.mockResolvedValue({ ok: true, state: "pending", retry_after: 30 });

    render(<GitHubDeviceAuth onConnected={vi.fn()} />);
    fireEvent.click(screen.getByTestId("github-device-start"));

    expect((await screen.findByTestId("github-user-code")).textContent).toContain("WDJB-MJHT");
    expect(screen.getByTestId("github-device-waiting").textContent).toContain(
      "Waiting for approval on GitHub",
    );
    await waitFor(() => expect(mocks.poll).toHaveBeenCalledWith("opaque-flow"));
  });

  it("copies the code and opens GitHub from the user gesture", async () => {
    mocks.start.mockResolvedValue(started);
    mocks.poll.mockResolvedValue({ ok: true, state: "pending", retry_after: 30 });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<GitHubDeviceAuth onConnected={vi.fn()} />);
    fireEvent.click(screen.getByTestId("github-device-start"));
    const open = await screen.findByTestId("github-device-copy-open");
    fireEvent.click(open);

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("WDJB-MJHT"));
    expect(mocks.openExternal).toHaveBeenCalledWith("https://github.com/login/device");
    expect(open.textContent).toContain("Copied");
  });

  it("reports completion to the modal", async () => {
    mocks.start.mockResolvedValue(started);
    mocks.poll.mockResolvedValue({
      ok: true,
      state: "complete",
      account: "octocat",
    });
    const connected = vi.fn();

    render(<GitHubDeviceAuth onConnected={connected} />);
    fireEvent.click(screen.getByTestId("github-device-start"));

    await waitFor(() => expect(connected).toHaveBeenCalledTimes(1));
  });

  it("cancels sidecar state when the modal unmounts", async () => {
    mocks.start.mockResolvedValue(started);
    mocks.poll.mockResolvedValue({ ok: true, state: "pending", retry_after: 30 });
    mocks.cancel.mockResolvedValue({ ok: true });
    const view = render(<GitHubDeviceAuth onConnected={vi.fn()} />);
    fireEvent.click(screen.getByTestId("github-device-start"));
    await screen.findByTestId("github-user-code");

    view.unmount();

    await waitFor(() => expect(mocks.cancel).toHaveBeenCalledWith("opaque-flow"));
  });

  it("shows configuration failures without entering the waiting state", async () => {
    mocks.start.mockResolvedValue({
      ok: false,
      error: "GitHub device sign-in is not configured.",
    });

    render(<GitHubDeviceAuth onConnected={vi.fn()} />);
    fireEvent.click(screen.getByTestId("github-device-start"));

    expect(
      await screen.findByText("GitHub device sign-in is not configured."),
    ).toBeTruthy();
    expect(screen.queryByTestId("github-user-code")).toBeNull();
  });
});
