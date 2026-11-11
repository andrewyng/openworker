export class SessionReadError extends Error {
  constructor(message: string, public status?: number) { super(message); }
}

const copyJson = <T>(value: unknown): T => JSON.parse(JSON.stringify(value));

// Only session-list/discovery reads use this helper. No settled-result cache:
// a refresh after delete/archive must still observe the next server response.
export function createSessionReader(
  fetcher: (url: string, init: RequestInit) => Promise<Response>,
  timeoutMs = 15_000,
) {
  const pending = new Map<string, Promise<unknown>>();
  return function read<T>(url: string, scope: string, share = false): Promise<T> {
    const key = JSON.stringify([url, scope]);
    const existing = share ? pending.get(key) : undefined;
    if (existing) return existing.then(value => copyJson<T>(value));
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        reject(new DOMException("Session refresh timed out", "TimeoutError"));
        controller.abort();
      }, timeoutMs);
    });
    const operation = (async () => {
      const response = await fetcher(url, { signal: controller.signal });
      if (!response.ok) throw new SessionReadError(`HTTP ${response.status}`, response.status);
      return await response.json(); // Keep the deadline active while reading the body.
    })();
    const request = Promise.race([operation, deadline]).finally(() => {
      clearTimeout(timer);
      if (pending.get(key) === request) pending.delete(key);
    });
    if (share) pending.set(key, request);
    // Callers tag rows with machine metadata; do not share mutable response objects.
    return request.then(value => copyJson<T>(value));
  };
}
