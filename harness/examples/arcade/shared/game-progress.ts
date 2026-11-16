export type ProgressRequest =
  | { op: "load"; slot: string; version: number }
  | { op: "save"; slot: string; version: number; value: unknown }
  | { op: "remove"; slot: string }
  | { op: "best"; board: string }
  | { op: "score"; board: string; score: number; order: "lower" | "higher" };
export const SAVE_BYTES = 256 * 1024;
export function validateProgressRequest(value: unknown): ProgressRequest {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid save request.");
  const r = value as Record<string, unknown>;
  const fields: Record<string, string[]> = {
    load: ["op", "slot", "version"],
    save: ["op", "slot", "version", "value"],
    remove: ["op", "slot"],
    best: ["op", "board"],
    score: ["op", "board", "score", "order"],
  };
  if (
    typeof r.op !== "string" ||
    !Object.hasOwn(fields, r.op) ||
    Object.keys(r).some((k) => !fields[r.op as string].includes(k))
  )
    throw new Error("Invalid save operation.");
  const key = r.op === "best" || r.op === "score" ? r.board : r.slot;
  if (typeof key !== "string" || !/^[a-zA-Z0-9_-]{1,40}$/.test(key))
    throw new Error("Invalid save slot or score board.");
  if (
    (r.op === "save" || r.op === "load") &&
    (!Number.isSafeInteger(r.version) ||
      Number(r.version) < 1 ||
      Number(r.version) > 10000)
  )
    throw new Error("Invalid save version.");
  if (r.op === "save") {
    const json = JSON.stringify(r.value);
    if (
      json === undefined ||
      new TextEncoder().encode(json).length > SAVE_BYTES
    )
      throw new Error("Save exceeds 256 KB.");
  }
  if (
    r.op === "score" &&
    (typeof r.score !== "number" ||
      !Number.isFinite(r.score) ||
      r.score < 0 ||
      r.score > 1e12 ||
      !["lower", "higher"].includes(String(r.order)))
  )
    throw new Error("Invalid score.");
  return r as ProgressRequest;
}
