// `items_proposed` payloads (and the parked `gate: "items"` Inbox item's data).
import type { CardState } from "../types";
import { launchProposal, securityProposal, marketingProposal } from "./proposal-examples";

export const workItemsStates: CardState[] = [
  { id: "launch-plan", title: "Launch plan · eleven tasks", payload: launchProposal },
  { id: "security-plan", title: "Security · explicit external actions", payload: securityProposal },
  { id: "marketing-plan", title: "Marketing · publishing undetermined", payload: marketingProposal },
  {
    id: "three-items",
    title: "Three items",
    note: "The common case: everything fits without the expander.",
    payload: {
      items: [
        { title: "Add GET /invoices/:id/pdf", criteria: "Returns the stored PDF for the caller's own invoice; 404 for a missing file; 403 for another account." },
        { title: "Download button in the invoice drawer", criteria: "The button downloads the PDF and shows an error toast when the API fails." },
        { title: "Tests for both", criteria: "API and UI tests pass in CI." },
      ],
      note: "Split by surface so the API and the web client can move in parallel.",
    },
  },
  {
    id: "statements-split",
    title: "Four items, one long criterion",
    note: "A criterion past the clamp gets “Show full criteria”; the fourth item sits behind the expander.",
    payload: {
      items: [
        { title: "Statement API endpoint", criteria: "returns opening/closing balances over the chosen range; 8 endpoint tests green; malformed, missing, and reversed date ranges return 400; draft invoices are excluded from issued totals; inclusive boundaries verified end to end" },
        { title: "Statements dashboard page", criteria: "renders seeded data for Ada / Northgate; empty + error states covered" },
        { title: "Statement totals reconcile", criteria: "running balance matches invoices minus payments for the range" },
        { title: "Verification pass", criteria: "tester confirms page renders with live API data" },
      ],
      note: "Shared journal case: statements.",
    },
  },
  {
    id: "seven-items",
    title: "Seven items, long criteria",
    note: "More than three items collapse behind the expander; long criteria wrap.",
    payload: {
      items: [
        { title: "Schema: add invoices.pdf_generated_at", criteria: "Migration applies cleanly on an empty database and on the seeded one; rollback drops only the new column." },
        { title: "Worker: regenerate missing statements", criteria: "A nightly job finds invoices with a null pdf_key, renders the statement, uploads it under statements/<account>/<invoice>.pdf and records the key. Re-running the job makes no second upload.", description: "The seed data ships with null keys; the first run fills them." },
        { title: "API: GET /invoices/:id/pdf", criteria: "Streams the file with the billing role; 404 when there is no key; 403 for another account's invoice." },
        { title: "API: rate-limit the PDF route", criteria: "More than 30 requests a minute from one account returns 429 with a Retry-After header." },
        { title: "Web: download button", criteria: "Visible only when the invoice has a PDF; disabled with a tooltip while the statement is still rendering." },
        { title: "Web: error states", criteria: "A failed download shows a toast and leaves the drawer open." },
        { title: "Docs: runbook entry", criteria: "The runbook explains how to regenerate one statement by hand." },
      ],
      note: "",
    },
  },
  {
    id: "done-when-prefix",
    title: "Criteria with a “Done when” prefix",
    note: "Leads often write “Done when: …”. The card prints its own label, so the prefix is stripped.",
    payload: {
      items: [
        { title: "Cap the retry backoff", criteria: "Done when: no wait exceeds maxDelayMs, with a unit test for the cap." },
        { title: "Log the final attempt", criteria: "done when the last failure is logged once at warn level" },
      ],
      note: "",
    },
  },
  {
    id: "minimal",
    title: "Minimal payload",
    note: "One item, no note, no description: the least a server can send.",
    payload: { items: [{ title: "Fix the typo in the README", criteria: "The heading reads “Billing service”." }] },
  },
];
