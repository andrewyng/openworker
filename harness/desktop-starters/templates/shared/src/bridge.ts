export type Event = { type: string; [key: string]: unknown };
declare global {
  interface Window {
    arcade?: {
      action(input: unknown): Promise<unknown>;
      onEvent(listener: (event: Event) => void): () => void;
    };
  }
}
export async function action(input: unknown): Promise<unknown> {
  if (window.arcade) return window.arcade.action(input);
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("action", { input });
}
export async function subscribe(
  listener: (event: Event) => void,
): Promise<() => void> {
  if (window.arcade) return window.arcade.onEvent(listener);
  const { listen } = await import("@tauri-apps/api/event");
  return listen<Event>("agent-event", (event) => listener(event.payload));
}
