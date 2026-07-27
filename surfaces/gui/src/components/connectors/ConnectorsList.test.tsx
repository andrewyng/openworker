// Listener health in the connected list (#257): a two-way connector whose inbound
// listener never came up must not read as "Live". `connected` only means the
// credentials are saved, so the chip has to follow `listen_error` instead.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ConnectorsList } from "./ConnectorsList";
import { type Connector } from "../../api";

afterEach(cleanup);

// Only the fields the list actually reads; the rest of Connector is noise here.
const telegram = (over: Partial<Connector> = {}): Connector =>
  ({
    name: "telegram",
    title: "Telegram",
    blurb: "Chat with your coworker",
    auth: "token",
    two_way: true,
    channels: true,
    available: true,
    connected: true,
    account: "@testbot",
    enabled: true,
    brand_color: "#229ED9",
    logo: "telegram",
    fields: [],
    instructions: [],
    allowed_users: [],
    tools: [],
    managed: false,
    managed_profile: false,
    ...over,
  }) as Connector;

const list = (c: Connector) =>
  render(
    <ConnectorsList
      connectors={[c]}
      cloud={null}
      slack={null}
      onOpen={vi.fn()}
      onChanged={vi.fn()}
    />,
  );

describe("ConnectorsList listener health", () => {
  it("shows Live when the listener is up", () => {
    list(telegram({ listening: true }));
    expect(screen.getByText("● Live")).toBeTruthy();
    expect(screen.queryByText("● Not receiving")).toBeNull();
  });

  it("shows Not receiving when the listener failed to start", () => {
    list(
      telegram({
        listening: false,
        listen_error: "Telegram rejected the bot token — re-copy it from @BotFather",
      }),
    );
    expect(screen.getByText("● Not receiving")).toBeTruthy();
    expect(screen.queryByText("● Live")).toBeNull();
  });

  it("puts the reason on the row, so it reads without opening the connector", () => {
    const reason = "python-telegram-bot is not installed — reinstall with the messaging extra";
    list(telegram({ listening: false, listen_error: reason }));
    expect(screen.getByText(reason)).toBeTruthy();
    expect(screen.queryByText("@testbot")).toBeNull(); // the reason replaces the account line
  });

  it("stays Live when no listener state is reported at all", () => {
    // A server with no gateway running reports neither field. Absence is not a failure —
    // downgrading on it would flag every two-way connector as broken.
    list(telegram());
    expect(screen.getByText("● Live")).toBeTruthy();
    expect(screen.getByText("@testbot")).toBeTruthy();
  });
});
