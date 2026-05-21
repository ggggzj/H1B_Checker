/**
 * background.js — proxy API calls (avoids LinkedIn page CORS on /check and /config).
 */

const API_URL = 'https://h1bchecker-production.up.railway.app';
const DEFAULT_TIMEOUT_MS = 8000;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.action !== 'apiFetch') return false;

  const path = message.path;
  if (typeof path !== 'string' || !path.startsWith('/')) {
    sendResponse({ ok: false, error: 'Invalid API path' });
    return false;
  }

  const controller = new AbortController();
  const timeoutMs = message.timeoutMs || DEFAULT_TIMEOUT_MS;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  const init = {
    method: message.method || 'GET',
    signal: controller.signal,
    headers: { 'Content-Type': 'application/json' },
  };
  if (message.body) init.body = JSON.stringify(message.body);

  fetch(`${API_URL}${path}`, init)
    .then(async (response) => {
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!response.ok) {
        sendResponse({ ok: false, status: response.status, error: text.slice(0, 300) });
        return;
      }
      sendResponse({ ok: true, data });
    })
    .catch((err) => sendResponse({ ok: false, error: String(err) }))
    .finally(() => clearTimeout(timeoutId));

  return true;
});
