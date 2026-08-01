import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  MUTATING_COMMANDS,
  canonicalJson,
  classifyLiveAction,
  confirmationMaterial,
  redactAxNode,
} = require("../security.js");

const editable = redactAxNode(
  {
    role: { value: "textbox" },
    value: { value: "a-secret-password" },
    properties: [{ name: "editable", value: { value: "plaintext" } }],
  },
  { role: "textbox", name: "Password", value: "a-secret-password" },
);
assert.deepEqual(editable, {
  role: "textbox",
  name: "Password",
  editable: true,
  value_state: "non-empty",
});
assert.doesNotMatch(JSON.stringify(editable), /a-secret-password/);
const unnamedEditable = redactAxNode(
  { role: { value: "textbox" } },
  {
    role: "textbox",
    name: "Current value a-secret-password",
    value: "a-secret-password",
  },
);
assert.doesNotMatch(JSON.stringify(unnamedEditable), /a-secret-password/);

for (const command of ["click", "fill", "keypress", "scroll"]) {
  assert.equal(MUTATING_COMMANDS.has(command), true);
}
assert.equal(MUTATING_COMMANDS.has("snapshot"), false);

const passwordFill = classifyLiveAction("browser_fill", {
  role: "textbox",
  accessible_name: "Password",
  element_type: "password",
  data_classification: ["authentication"],
});
assert.equal(passwordFill.requires_confirmation, true);
assert.ok(passwordFill.reasons.includes("credential_disclosure"));
assert.ok(passwordFill.reasons.includes("sensitive_input"));

const personalFill = classifyLiveAction("browser_fill", {
  role: "textbox",
  accessible_name: "Email",
  element_type: "email",
  data_classification: ["personal"],
});
assert.equal(personalFill.requires_confirmation, true);

for (const name of ["Authorize OAuth access", "Pay now", "Delete account", "Continue"]) {
  const decision = classifyLiveAction("browser_click", {
    role: "button",
    accessible_name: name,
    element_type: "button",
  });
  assert.equal(decision.requires_confirmation, true, name);
}

const base = {
  action: "browser_click",
  snapshot: {
    documentId: "document-a",
    snapshotId: "snapshot-a",
    urlToken: "url-a",
  },
  target: {
    ref: "e1",
    role: "button",
    accessible_name: "Publish",
  },
};
const first = canonicalJson(confirmationMaterial(base));
const same = canonicalJson(confirmationMaterial(base));
const changedDocument = canonicalJson(confirmationMaterial({
  ...base,
  snapshot: { ...base.snapshot, documentId: "document-b" },
}));
const changedTarget = canonicalJson(confirmationMaterial({
  ...base,
  target: { ...base.target, accessible_name: "Delete" },
}));
assert.equal(first, same);
assert.notEqual(first, changedDocument);
assert.notEqual(first, changedTarget);

console.log("external Chrome security helpers verified");
