// The full-stop screen for a sidecar that never came up (#382): the headline tracks the
// failure kind, diagnostics (detail / log / binary) render only when actually known, and
// Reload triggers the caller's retry.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ServerFault } from "./ServerFault";

afterEach(cleanup);

const base = {
  detail: null as string | null,
  bin_path: "/Applications/OpenWorker.app/Contents/Resources/sidecar/openworker-server",
  log_path: "/Users/me/.config/coworker/logs/openworker-server.log",
};

describe("ServerFault", () => {
  it("reports an early exit with the exit detail and both diagnostic paths", () => {
    render(
      <ServerFault
        fault={{ ...base, status: "exited", detail: "exit status: 3" }}
        onRetry={() => {}}
      />,
    );
    const text = screen.getByTestId("server-fault").textContent ?? "";
    expect(text).toContain("exited during startup");
    expect(screen.getByTestId("server-fault-detail").textContent).toContain("exit status: 3");
    expect(text).toContain(base.log_path);
    expect(text).toContain(base.bin_path);
  });

  it("reports an unresponsive server without inventing diagnostics it doesn't have", () => {
    render(
      <ServerFault
        fault={{ status: "starting", detail: null, bin_path: "", log_path: "" }}
        onRetry={() => {}}
      />,
    );
    const text = screen.getByTestId("server-fault").textContent ?? "";
    expect(text).toContain("isn't responding");
    expect(screen.queryByTestId("server-fault-detail")).toBeNull();
    expect(text).not.toContain("Server log");
  });

  it("Reload invokes the retry callback", () => {
    const onRetry = vi.fn();
    render(
      <ServerFault
        fault={{ ...base, status: "spawn_failed", detail: "No such file or directory (os error 2)" }}
        onRetry={onRetry}
      />,
    );
    fireEvent.click(screen.getByTestId("server-fault-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
