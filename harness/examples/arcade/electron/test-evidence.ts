import { existsSync } from "node:fs";
import { ToolImageResult, type Json } from "@openworker/harness";
import { atomicWrite, readBounded, scoped } from "./files.js";
import type { Library } from "./library.js";
import type { TestingSummary, TestCheck } from "../shared/contracts.js";
export const CHECKS = [
  "start",
  "movement",
  "pause",
  "compact",
  "level",
] as const;
type Stored = Omit<TestingSummary, "current" | "error"> & { version: 1 };
function empty(digest: string): Stored {
  return {
    version: 1,
    digest,
    updatedAt: new Date().toISOString(),
    screenshots: 0,
    keys: 0,
    pointer: 0,
    viewports: [],
    hostErrors: 0,
    checks: Object.fromEntries(
      CHECKS.map((k) => [
        k,
        { status: "not_tested", note: "No assessment submitted." },
      ]),
    ) as Record<TestCheck, { status: "not_tested"; note: string }>,
  };
}
export class TestEvidence {
  constructor(private library: Library) {}
  private path(id: string) {
    return scoped(this.library.get(id).directory, "playtest-evidence.json");
  }
  private load(id: string): Stored | null {
    const path = this.path(id);
    if (!existsSync(path)) return null;
    const r = JSON.parse(readBounded(path, 32000));
    if (
      r.version !== 1 ||
      typeof r.digest !== "string" ||
      !/^[a-f0-9]{64}$/.test(r.digest) ||
      typeof r.updatedAt !== "string" ||
      !["screenshots", "keys", "pointer", "hostErrors"].every(
        (k) => Number.isSafeInteger(r[k]) && r[k] >= 0 && r[k] <= 10000,
      ) ||
      !Array.isArray(r.viewports) ||
      r.viewports.length > 4 ||
      r.viewports.some(
        (v: unknown) => typeof v !== "string" || !/^\d{3,4}x\d{3,4}$/.test(v),
      ) ||
      !CHECKS.every(
        (k) =>
          ["passed", "failed", "not_tested"].includes(r.checks?.[k]?.status) &&
          typeof r.checks[k].note === "string" &&
          r.checks[k].note.length <= 400,
      )
    )
      throw Error("Saved testing evidence is invalid; it was preserved.");
    return r;
  }
  private save(id: string, r: Stored) {
    r.updatedAt = new Date().toISOString();
    atomicWrite(this.path(id), JSON.stringify(r));
  }
  record(id: string, action: Json | undefined, result: Json | ToolImageResult) {
    const old = this.load(id);
    if (result instanceof ToolImageResult) {
      const data = result.data as Record<string, Json>;
      if (
        typeof data.digest !== "string" ||
        !/^[a-f0-9]{64}$/.test(data.digest)
      )
        return;
      const r = old?.digest === data.digest ? old : empty(data.digest);
      r.screenshots = Math.min(10000, r.screenshots + 1);
      if (action === "keys") r.keys = Math.min(10000, r.keys + 1);
      if (action === "pointer") r.pointer = Math.min(10000, r.pointer + 1);
      const size = `${data.width}x${data.height}`;
      if (/^\d{3,4}x\d{3,4}$/.test(size))
        r.viewports = [...new Set([...r.viewports, size])].slice(-4);
      this.save(id, r);
    } else if (
      result &&
      typeof result === "object" &&
      !Array.isArray(result) &&
      result.error
    ) {
      const r = old ?? empty(this.library.draft(id).digest);
      r.hostErrors = Math.min(10000, r.hostErrors + 1);
      this.save(id, r);
    }
  }
  report(id: string, args: Record<string, Json>) {
    const d = this.library.draft(id);
    if (args.digest !== d.digest)
      throw Error("Testing report is for an outdated draft.");
    const old = this.load(id),
      r = old?.digest === d.digest ? old : empty(d.digest),
      checks = args.checks;
    if (!checks || typeof checks !== "object" || Array.isArray(checks))
      throw Error("Supply a testing checklist.");
    for (const key of CHECKS) {
      const c = checks[key] as { status?: string; note?: string } | undefined;
      if (
        !c ||
        !["passed", "failed", "not_tested"].includes(c.status ?? "") ||
        typeof c.note !== "string" ||
        c.note.length > 400
      )
        throw Error("Invalid testing check.");
      if (
        c.status === "passed" &&
        (!r.screenshots ||
          (key === "compact" && !r.viewports.includes("960x600")) ||
          (["movement", "pause"].includes(key) && !r.keys && !r.pointer))
      )
        throw Error("Missing recorded preview activity for a claimed pass.");
    }
    r.checks = Object.fromEntries(
      CHECKS.map((k) => [k, checks[k]]),
    ) as Stored["checks"];
    this.save(id, r);
    return { saved: true, digest: d.digest };
  }
  summary(id: string): TestingSummary {
    try {
      const r = this.load(id);
      if (!r) return { ...empty(""), current: true };
      const digest = this.library.draftSummaryDigest(id);
      return { ...r, current: r.digest === digest };
    } catch {
      return {
        ...empty(""),
        current: false,
        error:
          "Testing summary unavailable. No verification claims can be made.",
      };
    }
  }
}
const checkSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["passed", "failed", "not_tested"] },
    note: { type: "string", maxLength: 400 },
  },
  required: ["status", "note"],
  additionalProperties: false,
};
export const REPORT_SCHEMA = {
  type: "object",
  properties: {
    digest: { type: "string", pattern: "^[a-f0-9]{64}$" },
    checks: {
      type: "object",
      properties: Object.fromEntries(CHECKS.map((k) => [k, checkSchema])),
      required: [...CHECKS],
      additionalProperties: false,
    },
  },
  required: ["digest", "checks"],
  additionalProperties: false,
};
