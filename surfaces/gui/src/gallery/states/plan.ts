// `plan_proposed` payloads.
import type { CardState } from "../types";

export const planStates: CardState[] = [
  {
    id: "short-plan",
    title: "Short plan",
    payload: {
      plan: "1. Add `GET /invoices/:id/pdf` to the API.\n2. Add the download button to the invoice drawer.\n3. Write tests for both and run the suites.",
    },
  },
  {
    id: "long-plan",
    title: "Long plan with headings and code",
    note: "Markdown: headings, lists, inline code, a fenced block.",
    payload: {
      plan: "## Goal\nEvery invoice gets a PDF download.\n\n## Steps\n1. **API** — add `GET /invoices/:id/pdf`. Stream the file from S3 with the billing role.\n   - 404 when `pdf_key` is null\n   - 403 for another account's invoice\n2. **Worker** — backfill statements for seeded invoices:\n\n```bash\npython -m worker statements --missing-only\n```\n\n3. **Web** — a download button in the invoice drawer, disabled while the statement renders.\n4. **Tests** — API (happy path, missing file, foreign account), UI (button and error toast).\n\n## Risks\n- The bucket policy in `infra/storage.tf` is wider than it should be; I will not touch it in this change.\n- Rendering is slow for accounts with more than 500 meters.\n\n## Out of scope\nEmailing the PDF.",
    },
  },
  {
    id: "minimal",
    title: "Minimal payload",
    note: "An empty plan: the card must not collapse.",
    payload: { plan: "" },
  },
];
