// The gallery's card registry: which cards exist, their states, and how the app renders
// each one inline. The render functions use the SAME payload helpers and components the
// session view uses (cardPayloads.ts) — nothing here is a copy of a card.
import type { ReactNode } from "react";
import type { MessageSource } from "../api";
import {
  approvalItemFromPayload,
  approvalMessage,
  connectorItemFromPayload,
  connectorResponseMessage,
  directoryItemFromPayload,
  directoryResponseMessage,
  itemsResponseMessage,
  liveQuestionInboxItem,
  planItemFromPayload,
  planResponseMessage,
  questionItemFromPayload,
  questionResponseMessage,
  teamItemFromPayload,
  teamResponseMessage,
  toolItemFromPayload,
  toolResponseMessage,
  workItemsItemFromPayload,
} from "../cardPayloads";
import { ApprovalCard } from "../components/ApprovalCard";
import { TeamUpdateLine } from "../components/TeamUpdateLine";
import { TeamViewGallery } from "./TeamViewGallery";
import type { TeamSummary } from "../teamView";
import { ConnectorMessageCard } from "../components/ConnectorMessageCard";
import { ConnectorRequestCard } from "../components/ConnectorRequestCard";
import { DirectoryRequestCard } from "../components/DirectoryRequestCard";
import { InboxItemCard } from "../components/InboxItemCard";
import { PlanCard } from "../components/PlanCard";
import { TeamRequestCard } from "../components/TeamRequestCard";
import { ToolRequestCard } from "../components/ToolRequestCard";
import { WorkItemsCard } from "../components/WorkItemsCard";
import { STATES } from "./states";
import type { CardState } from "./types";

// What /v1/settings.model_labels looks like in the app; the gallery passes it where the app does.
export const GALLERY_MODEL_LABELS: Record<string, string> = {
  "anthropic:claude-opus-4-8": "Claude Opus 4.8 · Anthropic",
  "anthropic:claude-sonnet-4-8": "Claude Sonnet 4.8 · Anthropic",
  "openai:gpt-5.2": "GPT-5.2 · OpenAI",
};

export type CardGroup = "Approvals" | "Team" | "Requests" | "Messages";
export const GROUPS: CardGroup[] = ["Approvals", "Team", "Requests", "Messages"];

export interface CardDef {
  id: string;
  title: string;
  group: CardGroup;
  /** The websocket event that raises the card inline. */
  event: string;
  states: CardState<any>[];
  /** Where the app puts the card: docked above the composer (default) or in the transcript. */
  stage?: "dock" | "transcript";
  /** `send` receives the websocket message the app would send for this click. */
  renderInline: (state: CardState<any>, send: (message: unknown, note?: string) => void) => ReactNode;
}

export const CARDS: CardDef[] = [
  { id: "team-view", title: "Team View", group: "Team", event: "team summary", stage: "transcript", states: STATES["team-view"], renderInline: s => <TeamViewGallery key={s.id} summary={s.payload.summary as TeamSummary} /> },
  {
    id: "approval",
    title: "Approval",
    group: "Approvals",
    event: "permission_required",
    states: STATES.approval,
    renderInline: (s, send) => (
      <ApprovalCard
        item={approvalItemFromPayload(s.payload)}
        onApprove={(decision) => send(approvalMessage(decision))}
        runTask={s.context?.runTask ?? null}
        autoApprove={s.context?.mode === "auto-approve"}
        compact
      />
    ),
  },
  {
    id: "plan",
    title: "Plan",
    group: "Approvals",
    event: "plan_proposed",
    states: STATES.plan,
    renderInline: (s, send) => (
      <PlanCard
        item={planItemFromPayload(s.payload)}
        onRespond={(approved, mode, feedback) => send(planResponseMessage(approved, mode, feedback))}
      />
    ),
  },
  {
    id: "team-request",
    title: "Team staffing",
    group: "Team",
    event: "team_proposed",
    states: STATES["team-request"],
    renderInline: (s, send) => (
      <TeamRequestCard
        item={teamItemFromPayload(s.payload)}
        // In the app this is the composer's mode, which for a lead matches the payload.
        leadMode={s.context?.mode ?? (typeof s.payload.lead_mode === "string" ? s.payload.lead_mode : undefined)}
        modelLabels={GALLERY_MODEL_LABELS}
        onRespond={(approved, feedback, enableChat, members) =>
          send(teamResponseMessage(approved, feedback, enableChat, members))
        }
      />
    ),
  },
  {
    id: "worker-decision",
    title: "Lead decides a worker's call",
    group: "Team",
    event: "permission_required (decide_worker_call)",
    states: STATES["worker-decision"],
    // Rendered through ApprovalCard, as the app does: it hands this tool to its own card.
    renderInline: (s, send) => (
      <ApprovalCard
        item={approvalItemFromPayload(s.payload)}
        onApprove={(decision) => send(approvalMessage(decision))}
        onAnswerWorkerCall={(callId, resolution) =>
          send({ resolution }, `POST /v1/inbox/${callId}/resolve  ·  answers the WORKER'S call directly`)
        }
        autoApprove={s.context?.mode !== "interactive"}
        compact
      />
    ),
  },
  {
    id: "work-items",
    title: "Work items",
    group: "Team",
    event: "items_proposed",
    states: STATES["work-items"],
    renderInline: (s, send) => (
      <WorkItemsCard
        item={workItemsItemFromPayload(s.payload)}
        onRespond={(approved, feedback) => send(itemsResponseMessage(approved, feedback))}
      />
    ),
  },
  {
    id: "connector-request",
    title: "Connector request",
    group: "Requests",
    event: "connector_requested",
    states: STATES["connector-request"],
    renderInline: (s, send) => (
      <ConnectorRequestCard
        item={connectorItemFromPayload(s.payload)}
        onRespond={(approved) => send(connectorResponseMessage(approved))}
        onOpenConnectors={() => send(null, "opens Settings ▸ Connectors (nothing is sent)")}
      />
    ),
  },
  {
    id: "question",
    title: "Question",
    group: "Requests",
    event: "question_requested",
    states: STATES.question,
    // A live question wears the Inbox card (one question UI), docked and compact.
    renderInline: (s, send) => (
      <InboxItemCard
        item={liveQuestionInboxItem(questionItemFromPayload(s.payload), "gallery-session")}
        onResolve={(_id, answer) => send(questionResponseMessage(answer))}
        compact
      />
    ),
  },
  {
    id: "directory-request",
    title: "Folder request",
    group: "Requests",
    event: "directory_requested",
    states: STATES["directory-request"],
    renderInline: (s, send) => (
      <DirectoryRequestCard
        item={directoryItemFromPayload(s.payload)}
        onRespond={(granted, path, writable) => send(directoryResponseMessage(granted, path, writable))}
      />
    ),
  },
  {
    id: "tool-request",
    title: "Tool request",
    group: "Requests",
    event: "tool_requested",
    states: STATES["tool-request"],
    renderInline: (s, send) => (
      <ToolRequestCard
        item={toolItemFromPayload(s.payload)}
        onRespond={(approved) => send(toolResponseMessage(approved))}
      />
    ),
  },
  {
    id: "board-wake",
    title: "Team update line",
    group: "Messages",
    event: "turn_start (source.connector = board)",
    stage: "transcript",
    states: STATES["board-wake"],
    renderInline: (s) => <TeamUpdateLine sources={(s.payload.sources as MessageSource[]) || [s.payload.source as MessageSource]} defaultOpen={s.payload.expanded === true} />,
  },
  {
    id: "connector-message",
    title: "Connector message",
    group: "Messages",
    event: "turn_start (source)",
    stage: "transcript",
    states: STATES["connector-message"],
    renderInline: (s) => <ConnectorMessageCard source={s.payload.source as MessageSource} />,
  },
];
