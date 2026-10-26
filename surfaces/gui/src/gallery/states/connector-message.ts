// Messages that arrive from a connector: `turn_start` payloads with a `source` sidecar.
import type { CardState } from "../types";

const from = (connector: string, kind: "channel" | "dm", channel: [string, string], sender: [string, string], text: string) => ({
  source: {
    connector,
    kind,
    channel_id: channel[0],
    channel_name: channel[1],
    sender_id: sender[0],
    sender_name: sender[1],
    ts: 1789900000,
    text,
  },
});

export const connectorMessageStates: CardState[] = [
  {
    id: "slack-channel",
    title: "Slack channel",
    note: "Names by default; hover swaps to the raw ids.",
    payload: from("slack", "channel", ["C0BD7KZ1AH5", "#billing-eng"], ["U04ABCD12", "Jordan Lee"], "Can the staging deploy go out before the 3pm demo? The invoice PDF change needs to be on it."),
  },
  {
    id: "slack-dm",
    title: "Slack direct message",
    payload: from("slack", "dm", ["D07XYZ", "Direct message"], ["U04ABCD12", "Jordan Lee"], "quick one — what's the status of BILL-212?"),
  },
  {
    id: "github-mention",
    title: "GitHub mention",
    note: "How a relay mention on an issue arrives on the box.",
    payload: from("github", "channel", ["acme/billing-service#3", "acme/billing-service #3"], ["demo-user", "demo-user"], "[lead] Add a PDF download to every invoice in the drawer. The statements worker already renders them; the API needs a route and the web client a button."),
  },
  {
    id: "long-message",
    title: "Long multi-line message",
    payload: from(
      "slack",
      "channel",
      ["C0BD7KZ1AH5", "#billing-eng"],
      ["U04ABCD12", "Jordan Lee"],
      "Three things from the customer call:\n\n1. They want the PDF to carry the PO number in the header.\n2. Statements for closed accounts should stay downloadable for seven years.\n3. The March invoices show the old tariff name.\n\nCan you look at 3 first? It is going in front of their finance team on Friday and I would rather not explain it live. Happy to pair on it after lunch if the tariff table is as confusing as I remember.",
    ),
  },
  {
    id: "unknown-connector",
    title: "Unknown connector, ids only",
    note: "No registry entry and unresolved names: plug glyph, neutral colour, ids shown as names.",
    payload: from("pagerduty", "channel", ["PXY123", "PXY123"], ["U999", "U999"], "Incident #4821 acknowledged."),
  },
];
