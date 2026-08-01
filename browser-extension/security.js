"use strict";

// Pure helpers shared by the MV3 service worker and Node contract tests.  Keep
// this file free of Chrome APIs: the service worker owns trusted DOM metadata
// collection while these helpers make the resulting policy deterministic.
(function installOpenWorkerBrowserSecurity(root) {
  const MUTATING_COMMANDS = new Set(["click", "fill", "keypress", "scroll"]);
  const EDITABLE_ROLES = new Set([
    "textbox",
    "searchbox",
    "combobox",
    "spinbutton",
  ]);
  const AMBIGUOUS_CONTROL = /^\s*(continue|next|confirm|ok(?:ay)?|done|finish|proceed)\s*[.!\u2026]?\s*$/i;
  const CONSEQUENTIAL_CONTROL = /\b(send|submit|publish|post|share|purchase|buy|pay|place\s+order|checkout|book|reserve|transfer|wire|subscribe|sign\s*up|create\s+account|manage\s+account|account\s+settings|connect\s+account|disconnect\s+account|accept|agree|delete|remove|erase|cancel|close\s+account|deactivate|change\s+password|reset\s+password|security|privacy|permission|authorize|allow|consent|log\s*in|login|sign\s*in|log\s*out|logout|sign\s*out|oauth)\b/i;
  const SENSITIVE_CLASSES = new Set([
    "secret",
    "credential",
    "authentication",
    "personal",
    "personal_data",
    "financial",
    "health",
  ]);

  function normalizeText(value) {
    return String(value || "").normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, " ").trim();
  }

  function isEditableAxNode(node) {
    const role = normalizeText(node?.role?.value ?? node?.role);
    const properties = Array.isArray(node?.properties) ? node.properties : [];
    return EDITABLE_ROLES.has(role) || properties.some((property) => (
      property?.name === "editable" && Boolean(property?.value?.value)
    ));
  }

  function redactAxNode(node, { role, name, value }) {
    const editable = isEditableAxNode(node);
    if (!editable) {
      return { role, name, ...(value ? { value } : {}) };
    }
    const safeName = value && String(name).includes(String(value))
      ? String(name).split(String(value)).join("").replace(/\s+/g, " ").trim()
      : name;
    return {
      role,
      name: safeName,
      editable: true,
      value_state: value ? "non-empty" : "empty",
    };
  }

  function classifyLiveAction(action, target = {}, args = {}) {
    const reasons = [];
    const normalizedAction = normalizeText(action).replace(/ /g, "_");
    const classifications = new Set(
      (target.data_classification || []).map((value) => normalizeText(value).replace(/ /g, "_")),
    );
    const elementType = normalizeText(target.element_type);
    const label = normalizeText([
      target.accessible_name,
      target.element_type,
      target.role,
      ...(target.page_risk_hints || []),
    ].join(" "));

    if ([...classifications].some((value) => SENSITIVE_CLASSES.has(value))) {
      reasons.push("sensitive_data_disclosure");
    }
    if (elementType === "password") reasons.push("credential_disclosure");

    if (["browser_fill", "browser_type"].includes(normalizedAction) && (
      elementType === "password" || [...classifications].some((value) => SENSITIVE_CLASSES.has(value))
    )) {
      reasons.push("sensitive_input");
    }

    if (normalizedAction === "browser_press") {
      const key = normalizeText(args.key);
      if (["enter", "return"].includes(key) && target.inside_form) {
        reasons.push("form_submission");
      }
    }

    if (normalizedAction === "browser_click") {
      if (target.submits_form || (
        target.inside_form && ["submit", "image"].includes(elementType)
      )) {
        reasons.push("form_submission");
      }
      if (CONSEQUENTIAL_CONTROL.test(label)) reasons.push("consequential_control");
      if (AMBIGUOUS_CONTROL.test(String(target.accessible_name || "")) && !target.consequence_known_safe) {
        reasons.push("ambiguous_control");
      }
    }

    if ((target.page_risk_hints || []).some((hint) => CONSEQUENTIAL_CONTROL.test(normalizeText(hint)))) {
      reasons.push("page_risk_hint");
    }

    return {
      requires_confirmation: reasons.length > 0,
      reasons: [...new Set(reasons)],
    };
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => (
        `${JSON.stringify(key)}:${canonicalJson(value[key])}`
      )).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function confirmationMaterial({ action, actionArgs = {}, snapshot, target }) {
    return {
      action,
      action_args: {
        ...(action === "browser_press" ? { key: String(actionArgs.key || "") } : {}),
      },
      document_id: String(snapshot.documentId || ""),
      ref: String(target.ref || ""),
      snapshot_id: String(snapshot.snapshotId || ""),
      target: {
        accessible_name: String(target.accessible_name || ""),
        autocomplete: String(target.autocomplete || ""),
        data_classification: [...(target.data_classification || [])].sort(),
        element_type: String(target.element_type || ""),
        inside_form: Boolean(target.inside_form),
        page_risk_hints: [...(target.page_risk_hints || [])],
        role: String(target.role || ""),
        submits_form: Boolean(target.submits_form),
      },
      url_token: String(snapshot.urlToken || ""),
    };
  }

  const api = {
    MUTATING_COMMANDS,
    canonicalJson,
    classifyLiveAction,
    confirmationMaterial,
    isEditableAxNode,
    redactAxNode,
  };
  root.OpenWorkerBrowserSecurity = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
}(typeof globalThis === "undefined" ? this : globalThis));
