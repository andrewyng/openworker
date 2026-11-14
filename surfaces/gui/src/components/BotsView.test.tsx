import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BotsView } from "./BotsView";
import { createBot, delegateBotTask, getBot, getBots, getBotRuntimes, setBotEnabled, type PersonalBot } from "../api";

vi.mock("../api", () => ({
  createBot: vi.fn(), delegateBotTask: vi.fn(), getBot: vi.fn(), getBots: vi.fn(), getBotRuntimes: vi.fn(), setBotEnabled: vi.fn(),
}));

const bot: PersonalBot = { id: "bot-a", name: "Researcher", instructions: "Track research",
  session_id: "bot-a", workspace: "/bots/a", agent: "cowork", enabled: true, cron: "", timezone: "UTC", next_check: null };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getBots).mockResolvedValue([bot]);
  vi.mocked(getBotRuntimes).mockResolvedValue([]);
  vi.mocked(getBot).mockResolvedValue({ bot, jobs: [], running: false });
});
afterEach(cleanup);

it("creates a named bot with standing instructions and the displayed timezone", async () => {
  vi.mocked(createBot).mockResolvedValue({ bot });
  render(<BotsView onOpen={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Researcher" } });
  fireEvent.change(screen.getByLabelText("Role, goals and standing instructions"), { target: { value: "Track research" } });
  fireEvent.click(screen.getByRole("button", { name: "Create bot" }));
  await waitFor(() => expect(createBot).toHaveBeenCalledWith({ name: "Researcher", instructions: "Track research", cron: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" }));
  expect(await screen.findByRole("button", { name: "Open conversation" })).toBeTruthy();
});

it("opens the durable conversation and pauses without losing delegated tasks", async () => {
  const onOpen = vi.fn();
  vi.mocked(delegateBotTask).mockResolvedValue({ job_id: "job-a" });
  vi.mocked(setBotEnabled).mockResolvedValue({ bot: { ...bot, enabled: false } });
  render(<BotsView onOpen={onOpen} />);
  fireEvent.click(await screen.findByRole("button", { name: "Researcher · Active" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open conversation" }));
  expect(onOpen).toHaveBeenCalledWith("bot-a", "/bots/a", "cowork");
  fireEvent.change(screen.getByLabelText("Delegate a task"), { target: { value: "Review my draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await waitFor(() => expect(delegateBotTask).toHaveBeenCalledWith("bot-a", "Review my draft"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Pause bot" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Pause bot" }));
  await waitFor(() => expect(setBotEnabled).toHaveBeenCalledWith("bot-a", false));
});

it("shows backend errors and preserves the unsent delegation", async () => {
  vi.mocked(delegateBotTask).mockRejectedValue(new Error("Host unavailable"));
  render(<BotsView onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Researcher · Active" }));
  const input = await screen.findByLabelText("Delegate a task");
  fireEvent.change(input, { target: { value: "Review my draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Host unavailable");
  expect((input as HTMLTextAreaElement).value).toBe("Review my draft");
});

it("creates on the selected dedicated cloud runtime", async () => {
  const cloud = { ...bot, machine_id: "remote", machine_name: "Cloud VM", execution: "dedicated" as const };
  vi.mocked(getBots).mockResolvedValueOnce([]).mockResolvedValue([cloud]);
  vi.mocked(getBotRuntimes).mockResolvedValue([{ id: "remote", name: "Cloud VM", connected: true,
    fingerprint: "fingerprint", app_version: "0.3.4", created_at: 1, last_seen: 1 }]);
  vi.mocked(createBot).mockResolvedValue({ bot: cloud });
  render(<BotsView onOpen={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Researcher" } });
  fireEvent.change(screen.getByLabelText("Where this bot runs"), { target: { value: "remote" } });
  fireEvent.change(screen.getByLabelText("Role, goals and standing instructions"), { target: { value: "Track research" } });
  fireEvent.click(screen.getByRole("button", { name: "Create bot" }));
  await waitFor(() => expect(createBot).toHaveBeenCalledWith({ name: "Researcher", instructions: "Track research", cron: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" }, "remote"));
  expect(await screen.findByText("Cloud VM · Dedicated runtime")).toBeTruthy();
});

it("disables mutations for an offline cloud bot", async () => {
  vi.mocked(getBots).mockResolvedValue([{ ...bot, machine_id: "remote", machine_name: "Cloud VM", machine_offline: true }]);
  render(<BotsView onOpen={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Researcher · Active" }));
  expect(await screen.findByRole("status")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Delegate a task"), { target: { value: "Private task" } });
  expect(screen.getByRole("button", { name: "Add to queue" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Pause bot" }).hasAttribute("disabled")).toBe(true);
  expect(getBot).not.toHaveBeenCalled();
});
