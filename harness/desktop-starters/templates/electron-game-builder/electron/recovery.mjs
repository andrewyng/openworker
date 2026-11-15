// A read-only projection of durable history. Never execute a tool to restore a view.
export async function recoverProject(harness, session, preferences = null) {
  if (!session) return { messages: [], html: "", settings: preferences };
  const snapshot = await session.snapshot();
  const messages = [],
    calls = new Map();
  let html = "",
    after = 0,
    target,
    textBytes = 0;
  while (true) {
    const page = await harness.request("history/read", {
      session_id: session.id,
      after,
    });
    target ??= page.total_items;
    for (const { index, item } of page.items) {
      if (index >= target) break;
      if (
        (item.role === "user" || item.role === "assistant") &&
        !item.runtime_notice
      ) {
        const text =
          typeof item.content === "string"
            ? item.content
            : (item.content ?? [])
                .filter((p) =>
                  ["text", "input_text", "output_text"].includes(p.type),
                )
                .map((p) => p.text ?? "")
                .join("");
        textBytes += Buffer.byteLength(text);
        if (textBytes > 10_000_000)
          throw Error(
            "Conversation exceeds the starter display limit; export or paginate the project history.",
          );
        if (text) messages.push(text);
      }
      if (item.type === "function_call" && item.name === "game__show_preview") {
        const args =
          typeof item.arguments === "string"
            ? JSON.parse(item.arguments)
            : item.arguments;
        if (typeof args?.html === "string" && [...args.html].length <= 100000)
          calls.set(item.call_id, args.html);
        if (calls.size > 64) throw Error("Invalid preview call history");
      }
      if (item.type === "function_call_output" && calls.has(item.call_id)) {
        let output;
        try {
          output =
            typeof item.output === "string"
              ? JSON.parse(item.output)
              : item.output;
        } catch {
          output = null;
        }
        // Denied, failed, cancelled and unconfirmed calls are never restored.
        if (
          output?.isError === false &&
          output?.structuredContent?.shown === true
        )
          html = calls.get(item.call_id);
        calls.delete(item.call_id);
      }
    }
    if (page.cursor >= target) break;
    if (page.cursor <= after) throw Error("Invalid history cursor");
    after = page.cursor;
  }
  const selected = snapshot.session.model_selection;
  const providers = await harness.request("provider/list", {});
  const provider = providers.connections.find(
    (p) => p.id === selected?.connection,
  );
  const settings = provider
    ? {
        provider: provider.id,
        api: provider.api,
        baseUrl: provider.base_url,
        model: selected.model,
      }
    : preferences;
  return { messages, html, settings };
}
