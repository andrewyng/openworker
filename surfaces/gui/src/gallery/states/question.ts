// `question_requested` payloads (ask_user). Parked, the same fields sit on the Inbox item.
import type { CardState } from "../types";

export const questionStates: CardState[] = [
  {
    id: "quick-replies",
    title: "Quick replies",
    note: "Plain options answer on click; free text is always available.",
    payload: {
      question: "Which environment should I restart?",
      options: ["staging", "production"],
      allow_text: true,
      multi: false,
    },
  },
  {
    id: "rich-options",
    title: "Options with descriptions",
    note: "A description on any option turns the pills into rows; one is marked Recommended.",
    payload: {
      header: "PDF storage",
      question: "Where should generated invoice PDFs live?",
      options: [
        { label: "S3, private bucket", description: "Served through the API with the billing role. Matches how statements work today.", recommended: true },
        { label: "Database (bytea)", description: "No new infrastructure, but invoices average 180 KB and the table is already large." },
        { label: "Generate on demand", description: "Nothing stored; every download costs a render of about two seconds." },
      ],
      allow_text: true,
      multi: false,
    },
  },
  {
    id: "options-with-preview",
    title: "Options with a preview pane",
    note: "A preview on any option adds the side pane; it follows hover and focus.",
    payload: {
      header: "Error shape",
      question: "Which error body should the PDF route return?",
      options: [
        { label: "Problem JSON", description: "RFC 9457, as the newer routes do.", recommended: true, preview: '{\n  "type": "about:blank",\n  "title": "Invoice has no PDF",\n  "status": 404\n}' },
        { label: "Legacy envelope", description: "What the web client parses today.", preview: '{\n  "error": {\n    "code": "NO_PDF",\n    "message": "Invoice has no PDF"\n  }\n}' },
      ],
      allow_text: false,
      multi: false,
    },
  },
  {
    id: "multi-select",
    title: "Multi-select",
    note: "Ticks accumulate; the answer is sent with the button.",
    payload: {
      question: "Which suites should run before I open the pull request?",
      options: ["API", "Web", "Worker", "Terraform validate"],
      allow_text: true,
      multi: true,
    },
  },
  {
    id: "grouped",
    title: "Grouped questions (stepper)",
    note: "Several questions in one ask: a stepper, answered as one JSON object.",
    payload: {
      question: "",
      questions: [
        { header: "Scope", question: "Should the download work for draft invoices too?", options: ["Issued only", "Drafts too"], allow_text: true },
        { header: "Naming", question: "What should the file be called?", options: ["invoice-<number>.pdf", "<account>-<period>.pdf"], allow_text: true },
        { header: "Rollout", question: "Ship behind a flag?", options: [{ label: "Yes", description: "Off by default; on for the demo account.", recommended: true }, { label: "No", description: "Visible to every account at once." }], allow_text: false },
      ],
    },
  },
  {
    id: "minimal",
    title: "Minimal payload",
    note: "A bare question: free text only.",
    payload: { question: "What should the branch be called?" },
  },
];
