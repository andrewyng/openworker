// Maps the raw transcript from GET /v1/sessions/{id}/messages into the GUI Item model.
// Connector-delivered user messages retain their structured source metadata.

import type { ConversationMessage } from "./api";
import type { Attachment, Item } from "./types";

type ToolReplayIndex = {
  results: Record<string, string>;
  hiddenCounts: Record<string, number>;
};

function indexToolResults(messages: ConversationMessage[]): ToolReplayIndex {
  const results: Record<string, string> = {};
  const hiddenCounts: Record<string, number> = {};
  for (const message of messages) {
    if (message.role !== "tool" || !message.tool_call_id) continue;
    results[message.tool_call_id] =
      typeof message.content === "string"
        ? message.content
        : JSON.stringify(message.content);
    const hidden = Number(message._display?.hidden_by_filters || 0);
    if (hidden > 0) hiddenCounts[message.tool_call_id] = hidden;
  }
  return { results, hiddenCounts };
}

function userMessageItems(message: ConversationMessage): Item[] {
  if (message.source?.connector)
    return [{ kind: "connector", source: message.source }];
  const user = userItemFromContent(message.content);
  if (typeof message.ts === "number") user.ts = message.ts;
  return user.text || user.attachments?.length ? [user] : [];
}

function parseToolArguments(
  value: string | undefined,
): Record<string, unknown> {
  try {
    return JSON.parse(value || "{}");
  } catch {
    return {};
  }
}

function assistantMessageItems(
  message: ConversationMessage,
  index: ToolReplayIndex,
): Item[] {
  const items: Item[] = [];
  if (message.content || message.reasoning) {
    items.push({
      kind: "assistant",
      text: message.content || "",
      ...(typeof message.ts === "number" ? { ts: message.ts } : {}),
      ...(message.reasoning ? { reasoning: message.reasoning } : {}),
    });
  }
  for (const toolCall of message.tool_calls || []) {
    const hidden = index.hiddenCounts[toolCall.id];
    items.push({
      kind: "tool",
      id: toolCall.id,
      name: toolCall.function?.name,
      args: parseToolArguments(toolCall.function?.arguments),
      status: "ok",
      preview: index.results[toolCall.id],
      ...(hidden ? { hidden } : {}),
    });
  }
  return items;
}

function noticeMessageItem(message: ConversationMessage): Item {
  if (message.kind === "interrupted") {
    return { kind: "notice", tone: "warn", text: "Interrupted." };
  }
  if (message.kind === "model_switch") {
    return {
      kind: "notice",
      tone: "info",
      text: message.text || "Model switched",
    };
  }
  return {
    kind: "notice",
    tone: "warn",
    text: "Error: " + (message.text || "unknown"),
    retriable: true,
  };
}

function replayItemsForMessage(
  message: ConversationMessage,
  index: ToolReplayIndex,
): Item[] {
  switch (message.role) {
    case "user":
      return userMessageItems(message);
    case "assistant":
      return assistantMessageItems(message, index);
    case "notice":
      return [noticeMessageItem(message)];
    default:
      // System messages are omitted; tool results are folded into their tool row.
      return [];
  }
}

export function itemsFromMessages(messages: ConversationMessage[]): Item[] {
  const replayMessages = messages || [];
  const index = indexToolResults(replayMessages);
  const items: Item[] = [];
  for (const message of replayMessages) {
    items.push(...replayItemsForMessage(message, index));
  }
  return items;
}

export function userItemFromContent(
  content: any,
): Extract<Item, { kind: "user" }> {
  if (typeof content === "string") return { kind: "user", text: content };
  if (!Array.isArray(content)) return { kind: "user", text: "" };

  const text: string[] = [];
  const attachments: Attachment[] = [];
  for (const part of content) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && part.text) {
      text.push(String(part.text));
    } else if (part.type === "image_url") {
      const url = part.image_url?.url;
      if (typeof url === "string" && url.startsWith("data:image/")) {
        attachments.push({ kind: "image", name: "image", data_url: url });
      }
    }
  }
  return { kind: "user", text: text.join("\n\n"), attachments };
}
