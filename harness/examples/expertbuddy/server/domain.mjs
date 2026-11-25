import { guides } from "./catalog.mjs";
export class AppError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
export const text = (v, n = 2000) =>
  typeof v === "string" ? v.trim().slice(0, n) : "";
const severity = new Set(["low", "moderate", "high", "uncertain"]);
export function assessment(raw) {
  let x;
  try {
    x = JSON.parse(
      raw
        .trim()
        .replace(/^```(?:json)?\s*/, "")
        .replace(/\s*```$/, ""),
    );
  } catch {
    throw new AppError(
      502,
      "The assessment could not be read. The photo is saved; start a new assessment to try again.",
    );
  }
  if (
    !x ||
    typeof x !== "object" ||
    !severity.has(x.severity) ||
    !["inspect", "rework", "hold", "no_visible_concern"].includes(
      x.recommendation,
    ) ||
    !text(x.title) ||
    !text(x.summary) ||
    !["clear_visual_evidence", "limited_view", "needs_measurement"].includes(
      x.confidence,
    ) ||
    !Array.isArray(x.findings) ||
    x.findings.length > 4 ||
    !Array.isArray(x.nextChecks) ||
    !Array.isArray(x.limitations) ||
    !Array.isArray(x.references)
  )
    throw new AppError(
      502,
      "The model returned an incomplete assessment. Your photo has been saved.",
    );
  return {
    title: text(x.title, 150),
    summary: text(x.summary, 1600),
    severity: x.severity,
    recommendation: x.recommendation,
    confidence: x.confidence,
    findings: x.findings.map((f, i) => {
      if (!text(f.label) || !text(f.evidence))
        throw new AppError(502, "A finding was missing visible evidence.");
      let box;
      if (f.box !== undefined) {
        if (
          !Array.isArray(f.box) ||
          f.box.length !== 4 ||
          !f.box.every(Number.isFinite) ||
          f.box.some((v) => v < 0 || v > 1) ||
          f.box[2] <= 0 ||
          f.box[3] <= 0 ||
          f.box[0] + f.box[2] > 1.001 ||
          f.box[1] + f.box[3] > 1.001
        )
          throw new AppError(
            502,
            "The model returned an invalid image annotation.",
          );
        box = f.box;
      }
      return {
        id: `F${i + 1}`,
        label: text(f.label, 150),
        evidence: text(f.evidence, 1000),
        impact: text(f.impact, 1000),
        ...(box ? { box } : {}),
      };
    }),
    nextChecks: x.nextChecks
      .slice(0, 6)
      .map((v) => text(v, 400))
      .filter(Boolean),
    limitations: x.limitations
      .slice(0, 5)
      .map((v) => text(v, 600))
      .filter(Boolean),
    references: x.references
      .filter((v) => guides.some((g) => g.id === v))
      .slice(0, 4),
  };
}
export function photoInput(input) {
  if (!input || typeof input !== "object")
    throw new AppError(400, "Choose a photo.");
  const mime = input.mediaType;
  if (!["image/jpeg", "image/png", "image/webp"].includes(mime))
    throw new AppError(400, "Use a JPEG, PNG or WebP photo.");
  if (
    typeof input.data !== "string" ||
    !/^[A-Za-z0-9+/]*={0,2}$/.test(input.data) ||
    input.data.length % 4 !== 0 ||
    input.data.length > 11200000
  )
    throw new AppError(400, "The image could not be read.");
  const bytes = Buffer.from(input.data, "base64");
  if (bytes.length < 12 || bytes.length > 8 * 1024 * 1024)
    throw new AppError(400, "Photos must be under 8 MB.");
  const png = bytes
    .subarray(0, 8)
    .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  const webp =
    bytes.subarray(0, 4).toString() === "RIFF" &&
    bytes.subarray(8, 12).toString() === "WEBP";
  if (!(
    (mime === "image/png" && png) ||
    (mime === "image/jpeg" && jpg) ||
    (mime === "image/webp" && webp)
  ))
    throw new AppError(400, "The file contents do not match the image type.");
  return {
    bytes,
    mediaType: mime,
    name:
      text(input.name, 160).replace(/[\x00-\x1f/\\]/g, "_") ||
      "inspection-photo",
  };
}
export function decision(input) {
  if (
    !input ||
    !["pass", "inspect", "rework", "hold"].includes(input.disposition) ||
    !text(input.inspector, 100)
  )
    throw new AppError(
      400,
      "Choose a disposition and enter the inspector name.",
    );
  return {
    disposition: input.disposition,
    inspector: text(input.inspector, 100),
    note: text(input.note, 1600),
  };
}
