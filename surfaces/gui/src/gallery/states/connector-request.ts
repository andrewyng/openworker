// `connector_requested` payloads.
import type { CardState } from "../types";

export const connectorRequestStates: CardState[] = [
  {
    id: "connect-jira",
    title: "Connect a service",
    note: "The lead needs a connector that is not connected on this machine yet.",
    payload: {
      request: "connect",
      connector: "jira",
      worker: "",
      reason: 'You asked to "track the work in Jira", and Jira is not connected on this machine.',
    },
  },
  {
    id: "connect-linear",
    title: "Connect a service (short reason)",
    payload: { request: "connect", connector: "linear", worker: "", reason: "keep the tracker in step with the board" },
  },
  {
    id: "grant-github",
    title: "Give a worker a connector",
    note: "Mid-run grant: the worker exists already and needs one more connector.",
    payload: { request: "grant", connector: "github", worker: "nia", reason: "push the branch for #1" },
  },
  {
    id: "long-reason",
    title: "Long reason",
    payload: {
      request: "grant",
      connector: "github",
      worker: "checks",
      reason:
        "checks found that the UI test needs the preview deployment's URL, which GitHub posts as a deployment status on the pull request. Reading that status needs the GitHub connector; without it checks would have to guess the URL from the branch name, which has been wrong twice this week.",
    },
  },
  {
    id: "minimal",
    title: "Minimal payload",
    note: "No reason, no worker.",
    payload: { request: "connect", connector: "slack" },
  },
];
