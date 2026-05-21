/**
 * popup.js — SponsorScope.ai extension popup
 */

const SESSION_DEFAULTS = {
  filteringEnabled: true,
  jobsScanned: 0,
  sponsorsFound: 0,
};

/** Prefer session storage; fall back to local if unavailable. */
function getPopupStorage() {
  return chrome.storage?.session ?? chrome.storage?.local;
}

function getSessionData() {
  return new Promise((resolve) => {
    const storage = getPopupStorage();
    if (!storage) {
      resolve({ ...SESSION_DEFAULTS });
      return;
    }
    storage.get(SESSION_DEFAULTS, (data) => resolve(data));
  });
}

function setSessionData(partial) {
  return new Promise((resolve) => {
    const storage = getPopupStorage();
    if (!storage) {
      resolve();
      return;
    }
    storage.set(partial, resolve);
  });
}

function updateStatsDisplay(jobsScanned, sponsorsFound) {
  const jobsEl = document.getElementById('jobsCount');
  const sponsorsEl = document.getElementById('sponsorsCount');
  if (jobsEl) jobsEl.textContent = String(jobsScanned ?? 0);
  if (sponsorsEl) sponsorsEl.textContent = String(sponsorsFound ?? 0);
}

async function notifyActiveTab(enabled) {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab?.id) return;
    await chrome.tabs.sendMessage(tab.id, {
      action: 'setFilteringEnabled',
      enabled,
    });
  } catch {
    // Content script may not be loaded on this tab — storage listener handles sync.
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const toggle = document.getElementById('scanToggle');
  if (!toggle) return;

  const data = await getSessionData();
  toggle.checked = data.filteringEnabled !== false;
  updateStatsDisplay(data.jobsScanned, data.sponsorsFound);

  toggle.addEventListener('change', async () => {
    const enabled = toggle.checked;
    await setSessionData({ filteringEnabled: enabled });
    await notifyActiveTab(enabled);
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.action !== 'statsUpdate' || !message.data) return;
    updateStatsDisplay(message.data.jobsScanned, message.data.sponsorsFound);
  });
});
