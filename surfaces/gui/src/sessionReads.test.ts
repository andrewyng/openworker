import { afterEach, expect, it, vi } from "vitest";
import { createSessionReader } from "./sessionReads";

afterEach(() => vi.useRealTimers());
it("shares only in-flight reads in the same scope and gives callers independent rows", async () => {
  let finish!: (r: Response) => void;
  const fetcher = vi.fn().mockReturnValueOnce(new Promise<Response>(resolve => { finish = resolve; }))
    .mockImplementation(async () => Response.json({ rows: [] }));
  const read = createSessionReader(fetcher);
  const a = read<{ rows: number[] }>("/sessions", "a", true);
  const b = read<{ rows: number[] }>("/sessions", "a", true);
  await read("/sessions", "b", true);
  expect(fetcher).toHaveBeenCalledTimes(2);
  finish(Response.json({ rows: [1] }));
  const [first, second] = await Promise.all([a, b]);
  first.rows.push(2);
  expect(second.rows).toEqual([1]);
  await read("/sessions", "a", true);
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it.each(["headers", "body"])("times out during %s and permits a subsequent retry", async stage => {
  vi.useFakeTimers();
  const fetcher = vi.fn().mockImplementationOnce((_url, init) => {
    const stalled = new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    });
    return stage === "headers" ? stalled : Promise.resolve({ ok: true, json: () => stalled });
  }).mockImplementation(async () => Response.json([]));
  const read = createSessionReader(fetcher, 100);
  const check = expect(read("/sessions", "a", true)).rejects.toMatchObject({ name: "TimeoutError" });
  await vi.advanceTimersByTimeAsync(100);
  await check;
  await expect(read("/sessions", "a", true)).resolves.toEqual([]);
});

it("does not mistake an HTTP error for successful JSON", async () => {
  const read = createSessionReader(async () => Response.json({ detail: "failed" }, { status: 500 }));
  await expect(read("/sessions", "a")).rejects.toMatchObject({ status: 500 });
});
