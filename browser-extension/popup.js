"use strict";

const unavailable = document.querySelector("#unavailable");
const sharing = document.querySelector("#sharing");
const statusLine = document.querySelector("#status-line");
const errorBox = document.querySelector("#error");
const retryButton = document.querySelector("#retry");
const claimButton = document.querySelector("#claim");
const releaseButton = document.querySelector("#release");

let activeTabId = null;

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  return tab;
}

async function message(payload) {
  const response = await chrome.runtime.sendMessage(payload);
  if (!response?.ok) throw new Error(response?.error?.message || "The extension request failed");
  return response.value;
}

function busy(value) {
  for (const button of document.querySelectorAll("button")) button.disabled = value;
}

function showError(error) {
  errorBox.textContent = error instanceof Error ? error.message : String(error);
  errorBox.hidden = false;
}

function clearError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

function render(state) {
  unavailable.hidden = state.connected;
  sharing.hidden = !state.connected;
  claimButton.hidden = Boolean(state.activeTabClaimed);
  releaseButton.hidden = !state.activeTabClaimed;
  statusLine.textContent = state.connected
    ? state.activeTabClaimed
      ? "This tab is shared"
      : "Connected — this tab is private"
    : "OpenWorker is not running";
}

async function refresh() {
  await activeTab();
  const state = await message({ type: "STATUS", activeTabId });
  render(state);
}

retryButton.addEventListener("click", async () => {
  clearError();
  busy(true);
  try {
    await refresh();
  } catch (error) {
    showError(error);
  } finally {
    busy(false);
  }
});

claimButton.addEventListener("click", async () => {
  clearError();
  busy(true);
  try {
    await activeTab();
    render(await message({ type: "CLAIM_TAB", tabId: activeTabId }));
  } catch (error) {
    showError(error);
  } finally {
    busy(false);
  }
});

releaseButton.addEventListener("click", async () => {
  clearError();
  busy(true);
  try {
    render(await message({ type: "RELEASE_TAB", tabId: activeTabId }));
  } catch (error) {
    showError(error);
  } finally {
    busy(false);
  }
});

refresh().catch(showError);
