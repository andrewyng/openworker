"use strict";

importScripts("security.js");

const {
  MUTATING_COMMANDS,
  canonicalJson,
  classifyLiveAction,
  confirmationMaterial,
  redactAxNode,
} = globalThis.OpenWorkerBrowserSecurity;

const PROTOCOL_VERSION = 1;
const NATIVE_HOST = "com.openworker.browser";
const POLL_ALARM = "openworker-browser-poll";
const BADGE_COLOR = "#2563EB";
const MAX_SNAPSHOTS = 12;
const MAX_COMMAND_JOURNAL = 128;
const NATIVE_REQUEST_TIMEOUT_MS = 10_000;
const NATIVE_POLL_TIMEOUT_MS = 35_000;

let pollInFlight = false;
let nextPollTimer = null;
let nativePort = null;
let nativeSession = null;
let nativeSessionPromise = null;
let reconnectTimer = null;
const pendingNativeRequests = new Map();
const navigationEpochs = new Map();

const storage = {
  async localGet(keys) {
    return chrome.storage.local.get(keys);
  },
  async localSet(value) {
    return chrome.storage.local.set(value);
  },
  async sessionGet(keys) {
    return chrome.storage.session.get(keys);
  },
  async sessionSet(value) {
    return chrome.storage.session.set(value);
  },
};

function apiError(code, message, retryable = false) {
  const error = new Error(message);
  error.code = code;
  error.retryable = retryable;
  return error;
}

function cleanError(error) {
  const code = typeof error?.code === "string" ? error.code : "EXTENSION_ERROR";
  const rawMessage = error instanceof Error ? error.message : String(error || "Unknown error");
  return {
    code: code.slice(0, 128),
    message: rawMessage.slice(0, 2048),
    retryable: Boolean(error?.retryable),
  };
}

function rejectPendingNativeRequests(error) {
  for (const pending of pendingNativeRequests.values()) {
    clearTimeout(pending.timer);
    pending.reject(error);
  }
  pendingNativeRequests.clear();
}

function closeNativePort() {
  const port = nativePort;
  nativePort = null;
  nativeSession = null;
  nativeSessionPromise = null;
  if (port) {
    try { port.disconnect(); } catch (_error) { /* already disconnected */ }
  }
}

function scheduleReconnect(delayMs = 2_000) {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectToOpenWorker().then(() => schedulePoll(0)).catch(() => scheduleReconnect(5_000));
  }, delayMs);
}

function openNativePort() {
  if (nativePort) return Promise.resolve(nativePort);
  let port;
  try {
    port = chrome.runtime.connectNative(NATIVE_HOST);
  } catch (_error) {
    return Promise.reject(apiError("NATIVE_HOST_UNAVAILABLE", "OpenWorker is not reachable. Keep the desktop app open and try again.", true));
  }
  port.onMessage.addListener((message) => {
    const pending = pendingNativeRequests.get(message?.id);
    if (!pending) return;
    pendingNativeRequests.delete(message.id);
    clearTimeout(pending.timer);
    if (message.ok) pending.resolve(message.result || {});
    else {
      const details = message.error || {};
      pending.reject(apiError(details.code || "NATIVE_HOST_ERROR", details.message || "OpenWorker rejected the browser request", Boolean(details.retryable)));
    }
  });
  port.onDisconnect.addListener(() => {
    const detail = chrome.runtime.lastError?.message || "OpenWorker's browser connection closed";
    if (nativePort === port) closeNativePort();
    rejectPendingNativeRequests(apiError("NATIVE_HOST_DISCONNECTED", detail, true));
    scheduleReconnect();
  });
  nativePort = port;
  return Promise.resolve(port);
}

async function nativeRequest(type, payload = {}, timeoutMs = NATIVE_REQUEST_TIMEOUT_MS) {
  const port = await openNativePort();
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingNativeRequests.delete(id);
      reject(apiError("NATIVE_HOST_TIMEOUT", "OpenWorker did not answer the browser request", true));
    }, timeoutMs);
    pendingNativeRequests.set(id, { resolve, reject, timer });
    try {
      port.postMessage({ version: PROTOCOL_VERSION, id, type, payload });
    } catch (_error) {
      clearTimeout(timer);
      pendingNativeRequests.delete(id);
      reject(apiError("NATIVE_HOST_DISCONNECTED", "OpenWorker's browser connection closed", true));
    }
  });
}

async function browserMetadata() {
  const userAgent = navigator.userAgent;
  const versionMatch = userAgent.match(/Chrome\/([\d.]+)/);
  let clientId = (await storage.localGet("clientId")).clientId;
  if (!clientId) {
    clientId = crypto.randomUUID();
    await storage.localSet({ clientId });
  }
  return {
    browser: "chrome",
    browser_version: versionMatch?.[1] || "unknown",
    extension_version: chrome.runtime.getManifest().version,
    platform: navigator.platform || "unknown",
    client_id: clientId,
  };
}

async function announceClaimedTabs() {
  const claimed = await claimedTabs();
  for (const record of Object.values(claimed)) {
    await nativeRequest("events", {
      event: {
        type: "tab_claimed",
        tab_id: record.tabId,
        title: record.title || "",
        url: record.url || "",
      },
    }).catch(() => {});
  }
}

async function connectToOpenWorker() {
  if (nativeSession) return nativeSession;
  if (nativeSessionPromise) return nativeSessionPromise;
  nativeSessionPromise = (async () => {
    const response = await nativeRequest("connect", {
      client: await browserMetadata(),
      protocol_version: PROTOCOL_VERSION,
    });
    if (!response.session_id) {
      throw apiError("INVALID_CONNECT_RESPONSE", "OpenWorker returned an incomplete connection response");
    }
    nativeSession = { sessionId: response.session_id, browser: "chrome" };
    await chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
    await announceClaimedTabs();
    schedulePoll(0);
    return nativeSession;
  })();
  try {
    return await nativeSessionPromise;
  } finally {
    nativeSessionPromise = null;
  }
}

async function connection() {
  try {
    return await connectToOpenWorker();
  } catch (error) {
    if (error?.retryable) scheduleReconnect();
    return null;
  }
}

async function bridgeFetch(path, options = {}) {
  if (!(await connection())) {
    throw apiError("OPENWORKER_UNAVAILABLE", "OpenWorker is not reachable. Keep the desktop app open and try again.", true);
  }
  const type = path.replace(/^\//, "");
  const payload = typeof options.body === "string" ? JSON.parse(options.body || "{}") : (options.body || {});
  try {
    return await nativeRequest(type, payload, type === "poll" ? NATIVE_POLL_TIMEOUT_MS : NATIVE_REQUEST_TIMEOUT_MS);
  } catch (error) {
    if ([
      "UNAUTHENTICATED",
      "NATIVE_HOST_DISCONNECTED",
      "BRIDGE_UNAVAILABLE",
      "BRIDGE_NOT_READY",
      "OPENWORKER_UNAVAILABLE",
    ].includes(error?.code)) {
      nativeSession = null;
      nativeSessionPromise = null;
      scheduleReconnect();
    }
    throw error;
  }
}

async function claimedTabs() {
  const value = await storage.sessionGet("claimedTabs");
  return value.claimedTabs || {};
}

async function snapshots() {
  const value = await storage.sessionGet("snapshots");
  return value.snapshots || {};
}

async function commandJournal() {
  const value = await storage.sessionGet("commandJournal");
  return value.commandJournal || {};
}

async function setCommandJournal(value) {
  const entries = Object.entries(value);
  entries.sort((left, right) => (left[1].updatedAt || 0) - (right[1].updatedAt || 0));
  while (entries.length > MAX_COMMAND_JOURNAL) entries.shift();
  await storage.sessionSet({ commandJournal: Object.fromEntries(entries) });
}

async function setClaimedTabs(value) {
  await storage.sessionSet({ claimedTabs: value });
}

async function setSnapshots(value) {
  const entries = Object.entries(value);
  entries.sort((left, right) => (left[1].createdAt || 0) - (right[1].createdAt || 0));
  while (entries.length > MAX_SNAPSHOTS) {
    entries.shift();
  }
  await storage.sessionSet({ snapshots: Object.fromEntries(entries) });
}

async function updateBadge(tabId, attached) {
  await chrome.action.setBadgeBackgroundColor({ tabId, color: BADGE_COLOR });
  await chrome.action.setBadgeText({ tabId, text: attached ? "ON" : "" });
  await chrome.action.setTitle({
    tabId,
    title: attached ? "Shared with OpenWorker — click to manage" : "Share this tab with OpenWorker",
  });
}

async function sendEvent(event) {
  try {
    await bridgeFetch("/events", { method: "POST", body: JSON.stringify({ event }) });
  } catch (_error) {
    // Navigation and detach events are best-effort. The next poll/command also
    // verifies the attachment and fails closed if the tab is no longer shared.
  }
}

async function claimTab(tabId) {
  if (!Number.isInteger(tabId) || tabId < 0) {
    throw apiError("INVALID_TAB", "No active browser tab was found");
  }
  if (!(await connection())) {
    throw apiError("OPENWORKER_UNAVAILABLE", "OpenWorker is not reachable. Keep the desktop app open and try again.", true);
  }
  const tab = await chrome.tabs.get(tabId);
  const blocked = /^(chrome|devtools|chrome-extension):/i.test(tab.url || "");
  if (blocked) {
    throw apiError("PROTECTED_TAB", "This browser page cannot be shared");
  }
  const claimed = await claimedTabs();
  if (claimed[String(tabId)]) {
    return status(tabId);
  }
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (error) {
    throw apiError(
      "ATTACH_FAILED",
      error?.message || "Chrome would not share this tab",
    );
  }
  claimed[String(tabId)] = {
    tabId,
    title: tab.title || "Untitled tab",
    url: tab.url || "",
    claimedAt: Date.now(),
  };
  await setClaimedTabs(claimed);
  await updateBadge(tabId, true);
  await sendEvent({
    type: "tab_claimed",
    tab_id: tabId,
    title: tab.title || "",
    url: tab.url || "",
  });
  return status(tabId);
}

async function releaseTab(tabId, reason = "user_released", { notify = true } = {}) {
  const claimed = await claimedTabs();
  if (!claimed[String(tabId)]) {
    await updateBadge(tabId, false).catch(() => {});
    return;
  }
  // Remove the claim before detaching so chrome.debugger.onDetach cannot race
  // this explicit release and emit a duplicate detach event.
  delete claimed[String(tabId)];
  await setClaimedTabs(claimed);
  await removeTabSnapshots(tabId);
  try {
    await chrome.debugger.detach({ tabId });
  } catch (_error) {
    // The target may already have closed or another debugger may have detached it.
  }
  await updateBadge(tabId, false).catch(() => {});
  if (notify) {
    await sendEvent({ type: "tab_released", tab_id: tabId, reason });
  }
}

async function removeTabSnapshots(tabId) {
  const values = await snapshots();
  for (const [snapshotId, snapshot] of Object.entries(values)) {
    if (snapshot.tabId === tabId) {
      delete values[snapshotId];
    }
  }
  await setSnapshots(values);
}

async function releaseAllTabs(reason = "extension_disconnect", { notify = true } = {}) {
  const claimed = await claimedTabs();
  await Promise.all(
    Object.keys(claimed).map((tabId) => releaseTab(Number(tabId), reason, { notify })),
  );
  await storage.sessionSet({ claimedTabs: {}, snapshots: {} });
}

async function forgetConnection({ detach = true } = {}) {
  if (detach) {
    await releaseAllTabs("session_disconnected", { notify: false });
  }
  await chrome.alarms.clear(POLL_ALARM);
  if (nextPollTimer !== null) {
    clearTimeout(nextPollTimer);
    nextPollTimer = null;
  }
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  closeNativePort();
}

async function status(activeTabId = null) {
  const current = await connection();
  const claimed = await claimedTabs();
  return {
    connected: Boolean(current),
    browser: "chrome",
    sessionId: current?.sessionId || null,
    claimedTabIds: Object.values(claimed).map((value) => value.tabId),
    activeTabClaimed: activeTabId === null ? false : Boolean(claimed[String(activeTabId)]),
  };
}

async function assertClaimed(tabId) {
  const claimed = await claimedTabs();
  if (!claimed[String(tabId)]) {
    throw apiError("TAB_NOT_CLAIMED", "This tab is not shared with OpenWorker");
  }
  return claimed[String(tabId)];
}

async function cdp(tabId, method, params = {}) {
  await assertClaimed(tabId);
  try {
    return await chrome.debugger.sendCommand({ tabId }, method, params);
  } catch (error) {
    throw apiError("CDP_COMMAND_FAILED", error?.message || `${method} failed`);
  }
}

function axValue(field) {
  const value = field?.value;
  if (value === undefined || value === null) return "";
  return String(value).replace(/\s+/g, " ").trim().slice(0, 1000);
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function currentDocumentIdentity(tabId) {
  const [frameTree, documentTree, tab] = await Promise.all([
    cdp(tabId, "Page.getFrameTree"),
    cdp(tabId, "DOM.getDocument", { depth: 0, pierce: false }),
    chrome.tabs.get(tabId),
  ]);
  const frame = frameTree.frameTree?.frame || {};
  const documentBackendNodeId = documentTree.root?.backendNodeId || "";
  const url = String(tab.url || frame.url || "");
  const documentId = await sha256(
    `${frame.id || ""}\n${frame.loaderId || ""}\n${documentBackendNodeId}\n${url}`,
  );
  return {
    documentId,
    url,
    urlToken: await sha256(url),
  };
}

function sameDocument(left, right) {
  return Boolean(
    left && right
    && left.documentId === right.documentId
    && left.urlToken === right.urlToken
  );
}

async function commandTabs() {
  const claimed = await claimedTabs();
  const tabs = [];
  for (const record of Object.values(claimed)) {
    try {
      const tab = await chrome.tabs.get(record.tabId);
      tabs.push({
        tab_id: tab.id,
        title: tab.title || "Untitled tab",
        url: tab.url || "",
        active: Boolean(tab.active),
        window_id: tab.windowId,
        attached: true,
      });
    } catch (_error) {
      await releaseTab(record.tabId, "tab_missing");
    }
  }
  return { tabs };
}

async function commandSnapshot(params) {
  const tabId = params.tab_id;
  const startEpoch = navigationEpochs.get(tabId) || 0;
  const identity = await currentDocumentIdentity(tabId);
  await cdp(tabId, "Accessibility.enable");
  const tree = await cdp(tabId, "Accessibility.getFullAXTree");
  const nodes = [];
  const refs = {};
  let sequence = 1;
  for (const node of tree.nodes || []) {
    if (node.ignored || !Number.isInteger(node.backendDOMNodeId)) continue;
    const role = axValue(node.role) || "generic";
    const name = axValue(node.name);
    const value = axValue(node.value);
    if (!name && !value && !["RootWebArea", "heading", "main", "navigation"].includes(role)) {
      continue;
    }
    const ref = `e${sequence++}`;
    refs[ref] = node.backendDOMNodeId;
    nodes.push({ ref, ...redactAxNode(node, { role, name, value }) });
    if (nodes.length >= 500) break;
  }
  const beforeWrite = await currentDocumentIdentity(tabId);
  if (!sameDocument(identity, beforeWrite) || startEpoch !== (navigationEpochs.get(tabId) || 0)) {
    throw apiError("STALE_DOCUMENT", "The page navigated while OpenWorker was taking the snapshot", true);
  }
  const snapshotId = crypto.randomUUID();
  const values = await snapshots();
  values[snapshotId] = {
    snapshotId,
    tabId,
    refs,
    createdAt: Date.now(),
    documentId: identity.documentId,
    url: identity.url,
    urlToken: identity.urlToken,
  };
  await setSnapshots(values);
  const afterWrite = await currentDocumentIdentity(tabId);
  if (!sameDocument(identity, afterWrite) || startEpoch !== (navigationEpochs.get(tabId) || 0)) {
    const current = await snapshots();
    delete current[snapshotId];
    await setSnapshots(current);
    throw apiError("STALE_DOCUMENT", "The page navigated while OpenWorker was taking the snapshot", true);
  }
  const tab = await chrome.tabs.get(tabId);
  const text = nodes
    .map((node) => {
      const display = node.editable
        ? `${node.name || ""} [value=${node.value_state}]`
        : (node.name || node.value || "");
      return `[ref=${node.ref}] ${node.role} ${JSON.stringify(display)}`;
    })
    .join("\n");
  return {
    tab_id: tabId,
    snapshot_id: snapshotId,
    title: tab.title || "Untitled tab",
    url: tab.url || "",
    document_id: identity.documentId,
    url_token: identity.urlToken,
    snapshot: text,
    nodes,
    truncated: nodes.length >= 500,
  };
}

async function snapshotNode(params) {
  const values = await snapshots();
  const snapshot = values[params.snapshot_id];
  if (!snapshot || snapshot.tabId !== params.tab_id) {
    throw apiError("STALE_SNAPSHOT", "Take a new snapshot before acting on this tab");
  }
  const identity = await currentDocumentIdentity(params.tab_id);
  if (!sameDocument(snapshot, identity)) {
    const current = await snapshots();
    delete current[params.snapshot_id];
    await setSnapshots(current);
    throw apiError("STALE_SNAPSHOT", "The page changed after this snapshot was taken");
  }
  const backendNodeId = snapshot.refs?.[params.ref];
  if (!Number.isInteger(backendNodeId)) {
    throw apiError("REF_NOT_FOUND", "The selected element is not in this snapshot");
  }
  return { backendNodeId, snapshot };
}

function targetDataClassifications(target) {
  const descriptor = [
    target.accessible_name,
    target.autocomplete,
    target.name,
    target.id,
    ...(target.page_risk_hints || []),
  ].join(" ").toLocaleLowerCase();
  const autocomplete = new Set(
    String(target.autocomplete || "").replace(/,/g, " ").split(/\s+/).filter(Boolean).map((value) => value.toLocaleLowerCase()),
  );
  const labels = new Set();
  if (["email", "tel"].includes(target.element_type)) labels.add("personal");
  if (target.element_type === "password" || ["current-password", "new-password", "one-time-code", "username"].some((value) => autocomplete.has(value))) {
    labels.add("authentication");
  }
  if (["name", "given-name", "family-name", "email", "tel", "street-address", "address-line1", "address-line2", "address-line3", "postal-code", "country", "country-name", "bday"].some((value) => autocomplete.has(value))) {
    labels.add("personal");
  }
  if ([...autocomplete].some((value) => value.startsWith("cc-"))) labels.add("financial");
  if (/\b(password|passcode|otp|one.?time|login|sign.?in|username|authentication|oauth|token)\b/i.test(descriptor)) labels.add("authentication");
  if (/\b(email|e-mail|phone|mobile|address|postcode|postal|birthday|birth.?date|full.?name|first.?name|last.?name|ssn|social.?security|passport|driver.?licen[cs]e)\b/i.test(descriptor)) labels.add("personal");
  if (/\b(card|cvv|cvc|bank|routing|iban|account.?number|payment|billing)\b/i.test(descriptor)) labels.add("financial");
  if (/\b(health|medical|diagnosis|prescription|insurance)\b/i.test(descriptor)) labels.add("health");
  return [...labels].sort();
}

async function liveTarget(params, action) {
  const { backendNodeId, snapshot } = await snapshotNode(params);
  const resolved = await cdp(params.tab_id, "DOM.resolveNode", { backendNodeId });
  if (!resolved.object?.objectId) {
    throw apiError("ELEMENT_UNAVAILABLE", "The selected element is no longer available");
  }
  const inspected = await cdp(params.tab_id, "Runtime.callFunctionOn", {
    objectId: resolved.object.objectId,
    functionDeclaration: `function() {
      const element = this;
      const tag = String(element.tagName || "").toLowerCase();
      const type = String(element.getAttribute?.("type") || tag).toLowerCase();
      const form = element.closest ? element.closest("form") : null;
      const anchor = element.closest ? element.closest("a[href]") : null;
      const labels = element.labels
        ? Array.from(element.labels).map(label => label.innerText || label.textContent || "")
        : [];
      const editable = Boolean(
        element.isContentEditable
        || tag === "textarea"
        || tag === "select"
        || (tag === "input" && !["button", "submit", "reset", "image", "checkbox", "radio", "file"].includes(type))
      );
      const accessible = [
        element.getAttribute?.("aria-label"),
        ...labels,
        element.getAttribute?.("alt"),
        element.getAttribute?.("title"),
        element.getAttribute?.("placeholder"),
        editable ? "" : element.innerText,
      ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim().slice(0, 512);
      const submits = Boolean(form && (
        (tag === "button" && (!element.hasAttribute("type") || type === "submit"))
        || (tag === "input" && (type === "submit" || type === "image"))
      ));
      let destination = "";
      try {
        if (anchor?.href) destination = new URL(anchor.href, document.baseURI).href;
        else if (submits && form) destination = new URL(
          element.getAttribute("formaction") || form.getAttribute("action") || location.href,
          document.baseURI,
        ).href;
      } catch (_) {}
      const hints = [
        form?.getAttribute("aria-label"),
        form?.getAttribute("name"),
        form?.id,
        element.getAttribute?.("name"),
        element.id,
        element.getAttribute?.("autocomplete"),
      ].filter(Boolean).map(value => String(value).replace(/\\s+/g, " ").trim().slice(0, 128));
      return {
        role: String(element.getAttribute?.("role") || tag),
        accessible_name: accessible,
        element_type: type,
        inside_form: Boolean(form),
        submits_form: submits,
        destination_url: destination,
        autocomplete: String(element.getAttribute?.("autocomplete") || ""),
        name: String(element.getAttribute?.("name") || ""),
        id: String(element.id || ""),
        editable,
        value_state: editable && String(element.value ?? element.textContent ?? "").length > 0 ? "non-empty" : "empty",
        page_risk_hints: hints,
      };
    }`,
    returnByValue: true,
  });
  const target = { ...(inspected.result?.value || {}), ref: params.ref };
  target.data_classification = targetDataClassifications(target);
  const actionArgs = action === "browser_press" ? { key: params.key } : {};
  const policy = classifyLiveAction(action, target, actionArgs);
  const material = confirmationMaterial({ action, actionArgs, snapshot, target });
  const confirmationToken = await sha256(canonicalJson(material));
  // Catch same-document DOM replacement or navigation that raced inspection.
  const finalNode = await snapshotNode(params);
  if (finalNode.backendNodeId !== backendNodeId) {
    throw apiError("STALE_SNAPSHOT", "The selected element changed after inspection");
  }
  return {
    backendNodeId,
    snapshot,
    target,
    policy,
    confirmationToken,
  };
}

async function commandInspect(params) {
  const action = params.action;
  const inspected = await liveTarget(params, action);
  return {
    tab_id: params.tab_id,
    snapshot_id: params.snapshot_id,
    ref: params.ref,
    document_id: inspected.snapshot.documentId,
    url: inspected.snapshot.url,
    url_token: inspected.snapshot.urlToken,
    target: inspected.target,
    requires_confirmation: inspected.policy.requires_confirmation,
    reasons: inspected.policy.reasons,
    confirmation_token: inspected.confirmationToken,
    ...(inspected.target.destination_url ? { destination_url: inspected.target.destination_url } : {}),
  };
}

async function authorizeLiveAction(params, action) {
  const inspected = await liveTarget(params, action);
  if (inspected.policy.requires_confirmation && params.confirmation_token !== inspected.confirmationToken) {
    throw apiError(
      "BROWSER_CONFIRMATION_REQUIRED",
      `This live browser action requires confirmation (${inspected.policy.reasons.join(", ")})`,
    );
  }
  return inspected;
}

async function boxCenter(tabId, backendNodeId) {
  try {
    const model = await cdp(tabId, "DOM.getBoxModel", { backendNodeId });
    const quad = model.model?.border || model.model?.content;
    if (!Array.isArray(quad) || quad.length < 8) throw new Error("missing box");
    return {
      x: (quad[0] + quad[2] + quad[4] + quad[6]) / 4,
      y: (quad[1] + quad[3] + quad[5] + quad[7]) / 4,
    };
  } catch (_error) {
    const resolved = await cdp(tabId, "DOM.resolveNode", { backendNodeId });
    await cdp(tabId, "Runtime.callFunctionOn", {
      objectId: resolved.object.objectId,
      functionDeclaration: "function(){ this.scrollIntoView({block:'center',inline:'center'}); }",
      returnByValue: true,
    });
    const model = await cdp(tabId, "DOM.getBoxModel", { backendNodeId });
    const quad = model.model?.border || model.model?.content;
    if (!Array.isArray(quad) || quad.length < 8) {
      throw apiError("ELEMENT_NOT_VISIBLE", "The selected element has no visible bounds");
    }
    return {
      x: (quad[0] + quad[2] + quad[4] + quad[6]) / 4,
      y: (quad[1] + quad[3] + quad[5] + quad[7]) / 4,
    };
  }
}

async function commandClick(params) {
  let inspected = await authorizeLiveAction(params, "browser_click");
  let backendNodeId = inspected.backendNodeId;
  await boxCenter(params.tab_id, backendNodeId);
  inspected = await authorizeLiveAction(params, "browser_click");
  backendNodeId = inspected.backendNodeId;
  const point = await boxCenter(params.tab_id, backendNodeId);
  await cdp(params.tab_id, "Input.dispatchMouseEvent", {
    type: "mouseMoved",
    x: point.x,
    y: point.y,
  });
  await cdp(params.tab_id, "Input.dispatchMouseEvent", {
    type: "mousePressed",
    x: point.x,
    y: point.y,
    button: "left",
    clickCount: 1,
  });
  await cdp(params.tab_id, "Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x: point.x,
    y: point.y,
    button: "left",
    clickCount: 1,
  });
  return { clicked: true, point };
}

async function commandFill(params) {
  const finalTarget = await authorizeLiveAction(params, "browser_fill");
  const resolved = await cdp(params.tab_id, "DOM.resolveNode", { backendNodeId: finalTarget.backendNodeId });
  if (!resolved.object?.objectId) {
    throw apiError("ELEMENT_UNAVAILABLE", "The selected field is no longer available");
  }
  await cdp(params.tab_id, "Runtime.callFunctionOn", {
    objectId: resolved.object.objectId,
    functionDeclaration: `function(value) {
      this.focus();
      if (this.isContentEditable) {
        this.textContent = value;
        this.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
        return;
      }
      const proto = this instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (!setter) throw new Error('Element is not fillable');
      setter.call(this, value);
      this.dispatchEvent(new Event('input', {bubbles: true}));
      this.dispatchEvent(new Event('change', {bubbles: true}));
    }`,
    arguments: [{ value: params.text }],
    awaitPromise: false,
    returnByValue: true,
    userGesture: true,
  });
  return { filled: true };
}

const KEY_CODES = {
  Backspace: 8,
  Tab: 9,
  Enter: 13,
  Escape: 27,
  " ": 32,
  ArrowLeft: 37,
  ArrowUp: 38,
  ArrowRight: 39,
  ArrowDown: 40,
  Delete: 46,
  Home: 36,
  End: 35,
  PageUp: 33,
  PageDown: 34,
};

async function commandKeypress(params) {
  const finalTarget = await authorizeLiveAction(params, "browser_press");
  const resolved = await cdp(params.tab_id, "DOM.resolveNode", { backendNodeId: finalTarget.backendNodeId });
  if (!resolved.object?.objectId) {
    throw apiError("ELEMENT_UNAVAILABLE", "The selected element is no longer available");
  }
  await cdp(params.tab_id, "Runtime.callFunctionOn", {
    objectId: resolved.object.objectId,
    functionDeclaration: "function(){ this.focus(); }",
    returnByValue: true,
    userGesture: true,
  });
  const parts = String(params.key).split("+").map((part) => part.trim()).filter(Boolean);
  const key = parts.pop();
  if (!key) throw apiError("INVALID_KEY", "A key is required");
  let modifiers = 0;
  for (const modifier of parts) {
    const lowered = modifier.toLowerCase();
    if (lowered === "alt" || lowered === "option") modifiers |= 1;
    else if (lowered === "ctrl" || lowered === "control") modifiers |= 2;
    else if (lowered === "meta" || lowered === "command" || lowered === "cmd") modifiers |= 4;
    else if (lowered === "shift") modifiers |= 8;
    else throw apiError("INVALID_KEY", `Unsupported modifier: ${modifier}`);
  }
  const virtualKeyCode = KEY_CODES[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
  if (!virtualKeyCode) throw apiError("INVALID_KEY", `Unsupported key: ${key}`);
  const event = {
    key,
    code: key.length === 1 ? `Key${key.toUpperCase()}` : key,
    modifiers,
    windowsVirtualKeyCode: virtualKeyCode,
    nativeVirtualKeyCode: virtualKeyCode,
  };
  const printableText = key.length === 1 && (modifiers & 7) === 0 ? key : undefined;
  await cdp(params.tab_id, "Input.dispatchKeyEvent", {
    type: "rawKeyDown",
    ...event,
    ...(printableText ? { text: printableText, unmodifiedText: printableText } : {}),
  });
  await cdp(params.tab_id, "Input.dispatchKeyEvent", { type: "keyUp", ...event });
  return { pressed: params.key };
}

async function commandScroll(params) {
  const metrics = await cdp(params.tab_id, "Page.getLayoutMetrics");
  const viewport = metrics.cssVisualViewport || metrics.cssLayoutViewport || {};
  let x = params.x ?? Math.max(0, Number(viewport.clientWidth || 0) / 2);
  let y = params.y ?? Math.max(0, Number(viewport.clientHeight || 0) / 2);
  if (params.snapshot_id && params.ref) {
    const { backendNodeId } = await snapshotNode(params);
    const point = await boxCenter(params.tab_id, backendNodeId);
    x = point.x;
    y = point.y;
  }
  await cdp(params.tab_id, "Input.dispatchMouseEvent", {
    type: "mouseWheel",
    x,
    y,
    deltaX: params.delta_x || 0,
    deltaY: params.delta_y || 0,
  });
  return { scrolled: true, delta_x: params.delta_x || 0, delta_y: params.delta_y || 0 };
}

async function commandScreenshot(params) {
  const format = params.format || "jpeg";
  const options = {
    format,
    fromSurface: true,
    captureBeyondViewport: Boolean(params.full_page),
  };
  if (format === "jpeg") options.quality = params.quality ?? 80;
  let width;
  let height;
  if (params.full_page) {
    const metrics = await cdp(params.tab_id, "Page.getLayoutMetrics");
    const content = metrics.cssContentSize || metrics.contentSize;
    width = Math.min(Math.ceil(content.width), 16384);
    height = Math.min(Math.ceil(content.height), 16384);
    options.clip = { x: 0, y: 0, width, height, scale: 1 };
  } else {
    const metrics = await cdp(params.tab_id, "Page.getLayoutMetrics");
    const viewport = metrics.cssVisualViewport || metrics.cssLayoutViewport;
    width = Math.ceil(viewport.clientWidth);
    height = Math.ceil(viewport.clientHeight);
  }
  const capture = await cdp(params.tab_id, "Page.captureScreenshot", options);
  return {
    tab_id: params.tab_id,
    mime_type: format === "png" ? "image/png" : "image/jpeg",
    width,
    height,
    data_base64: capture.data,
  };
}

async function executeCommand(envelope) {
  const params = envelope.params || {};
  switch (envelope.command) {
    case "tabs": return commandTabs();
    case "snapshot": return commandSnapshot(params);
    case "inspect": return commandInspect(params);
    case "screenshot": return commandScreenshot(params);
    case "click": return commandClick(params);
    case "fill": return commandFill(params);
    case "keypress": return commandKeypress(params);
    case "scroll": return commandScroll(params);
    default: throw apiError("UNSUPPORTED_COMMAND", "OpenWorker requested an unsupported command");
  }
}

async function submitCommandResult(requestId, outcome) {
  await bridgeFetch("/results", {
    method: "POST",
    body: JSON.stringify({ request_id: requestId, ...outcome }),
  });
}

async function submitJournalOutcome(requestId, entry, journal) {
  try {
    await submitCommandResult(requestId, entry.outcome);
    journal[requestId] = { ...entry, submitted: true, updatedAt: Date.now() };
    await setCommandJournal(journal);
    return true;
  } catch (error) {
    if (!error?.retryable) {
      journal[requestId] = { ...entry, submitted: true, updatedAt: Date.now() };
      await setCommandJournal(journal);
    }
    return false;
  }
}

async function flushCommandJournal() {
  const journal = await commandJournal();
  for (const [requestId, entry] of Object.entries(journal)) {
    if (entry.state === "completed" && !entry.submitted && entry.outcome) {
      if (!(await submitJournalOutcome(requestId, entry, journal))) break;
    }
  }
}

async function processCommand(command) {
  const requestId = String(command.request_id || "");
  if (!requestId) throw apiError("INVALID_COMMAND", "OpenWorker sent a command without a request ID");
  if (!MUTATING_COMMANDS.has(command.command)) {
    try {
      return { ok: true, result: await executeCommand(command) };
    } catch (error) {
      return { ok: false, error: cleanError(error) };
    }
  }

  const journal = await commandJournal();
  const previous = journal[requestId];
  if (previous?.state === "completed" && previous.outcome) return previous.outcome;
  if (previous?.state === "executing") {
    const outcome = {
      ok: false,
      error: cleanError(apiError(
        "BROWSER_ACTION_OUTCOME_UNKNOWN",
        "OpenWorker will not repeat a browser action whose earlier outcome is unknown",
      )),
    };
    journal[requestId] = {
      ...previous,
      state: "completed",
      outcome,
      submitted: false,
      updatedAt: Date.now(),
    };
    await setCommandJournal(journal);
    return outcome;
  }

  journal[requestId] = {
    command: command.command,
    state: "executing",
    submitted: false,
    updatedAt: Date.now(),
  };
  await setCommandJournal(journal);
  let outcome;
  try {
    outcome = { ok: true, result: await executeCommand(command) };
  } catch (error) {
    outcome = { ok: false, error: cleanError(error) };
  }
  const current = await commandJournal();
  current[requestId] = {
    ...current[requestId],
    command: command.command,
    state: "completed",
    outcome,
    submitted: false,
    updatedAt: Date.now(),
  };
  await setCommandJournal(current);
  return outcome;
}

async function pollOnce() {
  if (pollInFlight || !(await connection())) return;
  pollInFlight = true;
  try {
    await flushCommandJournal();
    const response = await bridgeFetch("/poll", {
      method: "POST",
      body: JSON.stringify({ wait_seconds: 25, limit: 1 }),
    });
    for (const command of response.commands || []) {
      const outcome = await processCommand(command);
      await submitCommandResult(command.request_id, outcome);
      if (MUTATING_COMMANDS.has(command.command)) {
        const journal = await commandJournal();
        if (journal[command.request_id]) {
          journal[command.request_id] = {
            ...journal[command.request_id],
            submitted: true,
            updatedAt: Date.now(),
          };
          await setCommandJournal(journal);
        }
      }
    }
  } catch (error) {
    schedulePoll(error?.retryable ? 2000 : 5000);
  } finally {
    pollInFlight = false;
  }
  if (nativeSession) schedulePoll(0);
}

function schedulePoll(delayMs = 0) {
  if (nextPollTimer !== null) clearTimeout(nextPollTimer);
  nextPollTimer = setTimeout(() => {
    nextPollTimer = null;
    pollOnce();
  }, delayMs);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message?.type) {
      case "STATUS": return status(message.activeTabId ?? null);
      case "CLAIM_TAB": return claimTab(message.tabId);
      case "RELEASE_TAB":
        await releaseTab(message.tabId);
        return status(message.tabId);
      default: throw apiError("UNKNOWN_MESSAGE", "Unsupported extension request");
    }
  })().then(
    (value) => sendResponse({ ok: true, value }),
    (error) => sendResponse({ ok: false, error: cleanError(error) }),
  );
  return true;
});

chrome.debugger.onDetach.addListener(async (source, reason) => {
  if (!Number.isInteger(source.tabId)) return;
  const claimed = await claimedTabs();
  if (!claimed[String(source.tabId)]) return;
  delete claimed[String(source.tabId)];
  await setClaimedTabs(claimed);
  await removeTabSnapshots(source.tabId);
  await updateBadge(source.tabId, false).catch(() => {});
  await sendEvent({
    type: "debugger_detached",
    tab_id: source.tabId,
    reason: String(reason || "unknown"),
  });
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.url || changeInfo.status === "loading") {
    navigationEpochs.set(tabId, (navigationEpochs.get(tabId) || 0) + 1);
  }
  const claimed = await claimedTabs();
  if (!claimed[String(tabId)]) return;
  claimed[String(tabId)] = {
    ...claimed[String(tabId)],
    title: tab.title || claimed[String(tabId)].title,
    url: tab.url || claimed[String(tabId)].url,
  };
  await setClaimedTabs(claimed);
  if (changeInfo.url || changeInfo.status === "complete") {
    await removeTabSnapshots(tabId);
    await sendEvent({
      type: "tab_navigated",
      tab_id: tabId,
      url: tab.url || "",
      title: tab.title || "",
      status: changeInfo.status || "loading",
    });
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const claimed = await claimedTabs();
  if (!claimed[String(tabId)]) return;
  delete claimed[String(tabId)];
  await setClaimedTabs(claimed);
  await removeTabSnapshots(tabId);
  await sendEvent({ type: "tab_released", tab_id: tabId, reason: "tab_closed" });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) schedulePoll(0);
});

chrome.runtime.onStartup.addListener(() => connectToOpenWorker().then(() => schedulePoll(0)).catch(() => scheduleReconnect()));
chrome.runtime.onInstalled.addListener(() => connectToOpenWorker().then(() => schedulePoll(0)).catch(() => scheduleReconnect()));
connectToOpenWorker().then(() => schedulePoll(0)).catch(() => scheduleReconnect());
