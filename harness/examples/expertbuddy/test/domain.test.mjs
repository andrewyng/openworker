import { test } from "node:test";
import assert from "node:assert/strict";
import { assessment, photoInput, decision } from "../server/domain.mjs";
const valid = {
  title: "Visible scratch",
  summary: "A surface mark is visible.",
  severity: "moderate",
  recommendation: "inspect",
  confidence: "clear_visual_evidence",
  findings: [
    {
      label: "Scratch",
      evidence: "Light diagonal line.",
      impact: "Possible finish damage.",
      box: [0.2, 0.2, 0.3, 0.1],
    },
  ],
  nextChecks: ["Inspect under good lighting."],
  limitations: ["Depth cannot be determined."],
  references: ["FIN-01"],
};
test("assessment accepts bounded findings and preserves only actual guide references", () => {
  const a = assessment(
    JSON.stringify({ ...valid, references: ["FIN-01", "invented-standard"] }),
  );
  assert.equal(a.findings[0].id, "F1");
  assert.deepEqual(a.references, ["FIN-01"]);
});
test("assessment rejects invented severity and off-image annotations", () => {
  assert.throws(() =>
    assessment(JSON.stringify({ ...valid, severity: "guaranteed safe" })),
  );
  assert.throws(() =>
    assessment(
      JSON.stringify({
        ...valid,
        findings: [{ ...valid.findings[0], box: [0.9, 0.1, 0.3, 0.1] }],
      }),
    ),
  );
});
test("healthy-looking photos may have no localized findings without claiming production pass", () => {
  assert.equal(
    assessment(
      JSON.stringify({
        ...valid,
        recommendation: "no_visible_concern",
        findings: [],
      }),
    ).findings.length,
    0,
  );
});
test("photos require image signature, supported type and bounded size", () => {
  const b = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4K0AAAAASUVORK5CYII=",
    "base64",
  );
  assert.equal(
    photoInput({
      mediaType: "image/png",
      data: b.toString("base64"),
      name: "../a.png",
    }).name,
    ".._a.png",
  );
  assert.throws(() =>
    photoInput({
      mediaType: "image/png",
      data: Buffer.from('<svg onload="x">').toString("base64"),
    }),
  );
  assert.throws(() =>
    photoInput({ mediaType: "text/html", data: b.toString("base64") }),
  );
});
test("only explicit valid inspector decisions are accepted", () => {
  assert.equal(
    decision({ inspector: "Sam", disposition: "hold" }).inspector,
    "Sam",
  );
  assert.throws(() => decision({ inspector: "", disposition: "pass" }));
  assert.throws(() => decision({ inspector: "Sam", disposition: "AI-pass" }));
});
