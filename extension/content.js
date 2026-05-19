/**
 * content.js — H1B Checker Chrome Extension Content Script
 *
 * Runs automatically on LinkedIn job pages. No user action required.
 *
 * Resilience strategy (in order):
 *   1. Company identity from linkedin.com/company/{slug} URLs (stable across CSS changes)
 *   2. Job cards via [data-job-id] or server-provided selectors
 *   3. Infer job cards from company links when selectors miss
 *   4. Remote /config hot-reloads backup CSS selectors without a store update
 */

const API_URL = 'https://h1bchecker-production.up.railway.app';
const CONFIG_STORAGE_KEY = 'h1bExtensionConfig';

// Matches linkedin.com/company/acme-corp/... — product URL, not hashed CSS classes.
const LINKEDIN_COMPANY_PATH = /linkedin\.com\/company\/([^/?#]+)/i;

const DEFAULT_COMPANY_SELECTORS = [
  '.artdeco-entity-lockup__subtitle div[dir="ltr"]',
  '.artdeco-entity-lockup__subtitle',
  '.job-card-container__company-name',
  '.base-search-card__subtitle',
  '.base-card__subtitle',
  '.base-main-card__subtitle',
];

const DEFAULT_JOB_CARD_SELECTORS = [
  '[data-job-id]',
  'li.jobs-search-results__list-item',
  '.job-card-list__entity-lockup',
  '.jobs-search-results-list__list-item',
];

let companySelectors = [...DEFAULT_COMPANY_SELECTORS];
let jobCardSelectors = [...DEFAULT_JOB_CARD_SELECTORS];

const cache = new Map();
const reportedMissCards = new WeakSet();
const cardCompanyAnchors = new WeakMap();

const MAX_NAME_ATTEMPTS = 8;
const FETCH_TIMEOUT_MS = 8000;
const CONFIG_FETCH_TIMEOUT_MS = 5000;
const CONFIG_REFRESH_MS = 30 * 60 * 1000;


// ─────────────────────────────────────────────
// Remote config
// ─────────────────────────────────────────────

function applySelectorList(list, fallback, assign) {
  if (!Array.isArray(list) || list.length === 0) return false;
  if (!list.every((s) => typeof s === 'string' && s.length > 0 && s.length < 200)) {
    return false;
  }
  assign([...list]);
  return true;
}

function applyExtensionConfig(config) {
  if (!config?.selectors) return false;
  let updated = false;
  if (applySelectorList(config.selectors.company_name, DEFAULT_COMPANY_SELECTORS, (v) => {
    companySelectors = v;
  })) {
    updated = true;
  }
  if (applySelectorList(config.selectors.job_card, DEFAULT_JOB_CARD_SELECTORS, (v) => {
    jobCardSelectors = v;
  })) {
    updated = true;
  }
  return updated;
}

function readCachedConfig() {
  return new Promise((resolve) => {
    if (!chrome.storage?.local) {
      resolve(null);
      return;
    }
    chrome.storage.local.get([CONFIG_STORAGE_KEY], (result) => {
      resolve(result[CONFIG_STORAGE_KEY] || null);
    });
  });
}

function writeCachedConfig(config) {
  if (!chrome.storage?.local) return;
  chrome.storage.local.set({ [CONFIG_STORAGE_KEY]: config });
}

async function loadConfig() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CONFIG_FETCH_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_URL}/config`, { signal: controller.signal });
    if (!response.ok) {
      console.warn('[H1B] Config fetch HTTP error:', response.status);
      return false;
    }

    const config = await response.json();
    if (applyExtensionConfig(config)) {
      writeCachedConfig(config);
      console.log('[H1B] Config loaded, version:', config.version);
      return true;
    }
    console.warn('[H1B] Config response missing valid selectors');
    return false;
  } catch (error) {
    console.warn('[H1B] Config fetch failed, using bundled defaults:', error);
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function bootstrapConfig() {
  const cached = await readCachedConfig();
  if (cached) applyExtensionConfig(cached);

  loadConfig().then((updated) => {
    if (updated) processJobs();
  });

  setInterval(() => {
    loadConfig().then((updated) => {
      if (updated) processJobs();
    });
  }, CONFIG_REFRESH_MS);
}


// ─────────────────────────────────────────────
// Company extraction (URL-first)
// ─────────────────────────────────────────────

function _trimCompanyText(s) {
  if (!s) return null;
  const t = String(s).replace(/\s+/g, ' ').trim();
  if (!t || t.length > 200) return null;
  if (/^\d+\s+school alumni/i.test(t)) return null;
  return t;
}

function parseLinkedInCompanySlug(url) {
  if (!url) return null;
  const m = String(url).match(LINKEDIN_COMPANY_PATH);
  if (!m) return null;
  const slug = decodeURIComponent(m[1]).trim();
  if (!slug || slug === 'unavailable') return null;
  return slug;
}

function slugToLookupName(slug) {
  return slug.replace(/-/g, ' ').replace(/\s+/g, ' ').trim();
}

function getLinkHref(node) {
  return (
    node.href ||
    node.getAttribute('href') ||
    node.getAttribute('data-original-url') ||
    ''
  );
}

function getCompanyLinkCandidates(card) {
  const nodes = card.querySelectorAll(
    "a[href*='/company/'], [data-original-url*='/company/']"
  );
  const candidates = [];

  for (const node of nodes) {
    const href = getLinkHref(node);
    const slug = parseLinkedInCompanySlug(href);
    if (!slug) continue;
    candidates.push({
      node,
      slug,
      text: _trimCompanyText(node.textContent),
      href,
    });
  }

  return candidates;
}

/**
 * Resolve company name for API lookup. Prefers visible link text, then URL slug.
 */
function getCompanyLookup(card) {
  const candidates = getCompanyLinkCandidates(card);

  for (const c of candidates) {
    if (c.text) {
      cardCompanyAnchors.set(card, c.node);
      return c.text;
    }
  }

  for (const c of candidates) {
    const fromSlug = slugToLookupName(c.slug);
    if (fromSlug) {
      cardCompanyAnchors.set(card, c.node);
      return fromSlug;
    }
  }

  for (const selector of companySelectors) {
    const el = card.querySelector(selector);
    const name = _trimCompanyText(el?.textContent);
    if (name) {
      cardCompanyAnchors.set(card, el);
      return name;
    }
  }

  return null;
}


// ─────────────────────────────────────────────
// Job card discovery
// ─────────────────────────────────────────────

function outermostDataJobIdCard(el) {
  const card = el.matches?.('[data-job-id]') ? el : el.closest?.('[data-job-id]');
  if (!card) return null;
  if (card.parentElement?.closest('[data-job-id]')) return null;
  return card;
}

function findJobCardsFromSelectors() {
  const seen = new Set();
  const cards = [];

  for (const selector of jobCardSelectors) {
    let nodes;
    try {
      nodes = document.querySelectorAll(selector);
    } catch {
      continue;
    }

    for (const el of nodes) {
      let card = el;
      if (selector === '[data-job-id]') {
        card = outermostDataJobIdCard(el);
        if (!card) continue;
      }

      if (seen.has(card)) continue;
      seen.add(card);
      cards.push(card);
    }
  }

  return cards;
}

function findJobCardContainerFromLink(link) {
  let el = link;

  for (let depth = 0; depth < 14; depth++) {
    if (!el.parentElement) break;
    el = el.parentElement;

    const byId = outermostDataJobIdCard(el);
    if (byId) return byId;

    const companyLinks = el.querySelectorAll("a[href*='/company/']");
    const rect = el.getBoundingClientRect?.();
    const h = rect?.height ?? 0;

    if (companyLinks.length >= 1 && h > 48 && h < 600) {
      const parent = el.parentElement;
      const parentLinks = parent?.querySelectorAll("a[href*='/company/']")?.length ?? 0;
      if (parentLinks > 1) return el;
      if (parentLinks === 1 && (parent?.getBoundingClientRect?.().height ?? 0) < h * 2.5) {
        continue;
      }
      return el;
    }
  }

  return (
    link.closest('li, article, [role="listitem"]') ||
    link.parentElement?.parentElement ||
    null
  );
}

function findJobCardsFromCompanyLinks() {
  const seen = new Set();
  const cards = [];
  const links = document.querySelectorAll(
    "a[href*='/company/'], [data-original-url*='/company/']"
  );

  for (const link of links) {
    if (!parseLinkedInCompanySlug(getLinkHref(link))) continue;

    const card = findJobCardContainerFromLink(link);
    if (!card || seen.has(card)) continue;

    seen.add(card);
    cards.push(card);
  }

  return cards;
}

function findJobCards() {
  const fromSelectors = findJobCardsFromSelectors();
  if (fromSelectors.length > 0) return fromSelectors;
  return findJobCardsFromCompanyLinks();
}


// ─────────────────────────────────────────────
// processJobs / API / badge
// ─────────────────────────────────────────────

async function processJobs() {
  const jobCards = findJobCards();
  const pending = [];

  for (const card of jobCards) {
    const status = card.dataset.h1bDone;
    if (status === '1' || status === 'abandon' || status === 'pending') continue;

    const companyName = getCompanyLookup(card);

    if (!companyName) {
      const n = parseInt(card.dataset.h1bNameAttempts || '0', 10) + 1;
      card.dataset.h1bNameAttempts = String(n);
      if (n >= MAX_NAME_ATTEMPTS) {
        card.dataset.h1bDone = 'abandon';
        reportSelectorMiss(card);
      }
      continue;
    }

    card.dataset.h1bNameAttempts = '0';
    card.dataset.h1bDone = 'pending';
    pending.push({ card, companyName });
  }

  if (pending.length === 0) return;

  await Promise.all(
    pending.map(async ({ card, companyName }) => {
      const sponsorInfo = await getH1BInfo(companyName);
      if (!sponsorInfo) {
        if (card.dataset.h1bDone === 'pending') delete card.dataset.h1bDone;
        return;
      }
      addBadge(card, sponsorInfo);
      card.dataset.h1bDone = '1';
    })
  );
}

function reportSelectorMiss(card) {
  if (reportedMissCards.has(card)) return;
  reportedMissCards.add(card);

  fetch(`${API_URL}/report-selector-miss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      html: card.innerHTML.slice(0, 300),
      url: window.location.href,
      selectors_tried: {
        company: companySelectors,
        job_card: jobCardSelectors,
      },
    }),
  }).catch(() => {});
}

function getH1BInfo(companyName) {
  const key = companyName.toLowerCase();

  if (cache.has(key)) {
    return cache.get(key);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

  const promise = (async () => {
    try {
      const response = await fetch(
        `${API_URL}/check?company=${encodeURIComponent(companyName)}`,
        { signal: controller.signal }
      );

      if (!response.ok) {
        console.error('H1B API HTTP error:', response.status, await response.text());
        cache.delete(key);
        return null;
      }

      const data = await response.json();
      return {
        sponsors: data.sponsors_h1b,
        count: data.h1b_count,
      };
    } catch (error) {
      console.error('H1B API error:', error);
      cache.delete(key);
      return null;
    } finally {
      clearTimeout(timeoutId);
    }
  })();

  cache.set(key, promise);
  return promise;
}

function addBadge(card, info) {
  if (card.querySelector('.h1b-badge')) return;

  const badge = document.createElement('div');
  badge.className = `h1b-badge ${info.sponsors ? 'sponsor-yes' : 'sponsor-no'}`;
  badge.innerHTML = info.sponsors ? '✓ Sponsors H1B' : '✗ No Sponsor';

  const anchor =
    cardCompanyAnchors.get(card) ||
    card.querySelector("a[href*='/company/']") ||
    card.querySelector('[class*="company"]');

  if (anchor?.parentElement) {
    anchor.parentElement.appendChild(badge);
  } else {
    card.insertBefore(badge, card.firstChild);
  }
}


// ─────────────────────────────────────────────
// Startup & DOM observer
// ─────────────────────────────────────────────

function scheduleInitialPasses() {
  processJobs();
  setTimeout(processJobs, 300);
  setTimeout(processJobs, 800);
  setTimeout(processJobs, 1800);
  setTimeout(processJobs, 3500);
}

function start() {
  bootstrapConfig();
  scheduleInitialPasses();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start);
} else {
  start();
}

let debounceTimer = null;

function mutationLooksLikeJobContent(mutations) {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (node.nodeType !== 1) continue;
      if (node.matches?.('[data-job-id]')) return true;
      if (node.querySelector?.('[data-job-id]')) return true;
      if (node.matches?.("a[href*='/company/']")) return true;
      if (node.querySelector?.("a[href*='/company/']")) return true;
    }
  }
  return false;
}

const observer = new MutationObserver((mutations) => {
  if (mutationLooksLikeJobContent(mutations)) {
    processJobs();
  }

  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(processJobs, 300);
});

observer.observe(document.body, {
  childList: true,
  subtree: true,
});
