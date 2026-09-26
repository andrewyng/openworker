// `directory_requested` payloads.
import type { CardState } from "../types";

export const directoryRequestStates: CardState[] = [
  {
    id: "read-only",
    title: "Read a folder",
    payload: { reason: "Compare the statement template with the one marketing uses", path: "/Users/sam/Documents/brand/templates", writable: false, primary: false },
  },
  {
    id: "writable",
    title: "Write to a folder",
    note: "The access level arrives pre-selected as the agent asked; the human can lower it.",
    payload: { reason: "Save the regenerated statements next to the originals", path: "/Users/sam/SpaceSol/statements", writable: true, primary: false },
  },
  {
    id: "make-workspace",
    title: "Make it the workspace",
    note: "Root promotion: the folder becomes the session's workspace, always writable.",
    payload: { reason: "The repository lives here; I need it as the workspace to run the tests", path: "/Users/sam/fleet/demo-universe/billing-service", writable: true, primary: true },
  },
  {
    id: "no-path",
    title: "No folder suggested",
    note: "The agent gave a reason but no path: the human has to pick one.",
    payload: { reason: "I need somewhere to put the exported CSV files" },
  },
];
