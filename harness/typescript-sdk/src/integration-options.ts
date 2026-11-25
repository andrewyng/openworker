import { HarnessError } from "./types.js";

/** New SDK requests use objects; omission inherits and an empty object enables. */
export function validateIntegrationOptions(value: unknown): void {
  if (value === undefined) return;
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new HarnessError("InvalidInput", "integrations must be an object");
  }
  for (const [id, options] of Object.entries(value)) {
    if (options === null || typeof options !== "object" || Array.isArray(options)) {
      throw new HarnessError(
        "InvalidInput",
        `Integration ${id} must be an object. Use {} to enable or { enabled: false } to disable.`,
      );
    }
    if ("enabled" in options && typeof options.enabled !== "boolean") {
      throw new HarnessError("InvalidInput", `Integration ${id}.enabled must be a boolean`);
    }
  }
}
