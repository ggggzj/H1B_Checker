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

// Left list layout (2025–2026) — tried after /company/ URLs, before detail-panel fallbacks.
const LEFT_LIST_COMPANY_SELECTORS = [
  '.job-card-container__company-name',
  '[class*="job-card-container__company-name"]',
  '.job-card-container__primary-description',
  '[class*="job-card-list__company-name"]',
  '.job-card-list__entity-lockup .artdeco-entity-lockup__subtitle',
  '[class*="entity-lockup__subtitle"]',
];

const DEFAULT_COMPANY_SELECTORS = [
  ...LEFT_LIST_COMPANY_SELECTORS,
  '.artdeco-entity-lockup__subtitle div[dir="ltr"]',
  '.artdeco-entity-lockup__subtitle',
  '.base-search-card__subtitle',
  '.base-card__subtitle',
  '.base-main-card__subtitle',
  '.jobs-unified-top-card__company-name',
  '.job-details-jobs-unified-top-card__company-name',
];

/** Temporary debug — set false before release. */
const DEBUG_PROCESS_JOBS = false;

/** Process left list cards first; detail panel can be re-enabled later. */
const FOCUS_LIST_BADGES_ONLY = true;

const API_CHECK_CONCURRENCY = 4;

// Left list often uses data-occludable-job-id; detail panel uses data-job-id.
const LEFT_LIST_CARD_SELECTORS = [
  '[data-occludable-job-id]',
  '.jobs-search-results-list__list-item',
  'li.jobs-search-results__list-item',
  'li.scaffold-layout__list-item',
  '.job-card-list__entity-lockup',
];

const DEFAULT_JOB_CARD_SELECTORS = [
  '[data-job-id]',
  ...LEFT_LIST_CARD_SELECTORS,
];

const DETAIL_PANEL_ROOT_SELECTORS = [
  '.jobs-unified-top-card',
  '.job-details-jobs-unified-top-card',
  '.jobs-search__job-details',
];

// Where the full job description lives — detail panel only (list cards have no JD text).
const DETAIL_JD_SELECTORS = [
  '#job-details',
  '.jobs-description__content',
  '.jobs-box__html-content',
  'article.jobs-description__container',
];

// Per-posting 🔴 "no sponsorship" detection. Defaults here; /config can override them
// (see applyExtensionConfig) so wording rules hot-update without republishing.
// A posting is flagged only if a NEGATIVE matches AND no AFFIRMATIVE does — conservative
// on purpose: a wrong red badge scares users off jobs that actually sponsor.
const DEFAULT_NO_SPONSOR_NEGATIVE = [
  /\b(do(es)?\s+not|will\s+not|cannot|are\s+not\s+able\s+to|unable\s+to)\b[^.]{0,40}\bsponsor/i,
  /\bno\b[^.]{0,20}\b(visa\s+)?sponsorship\b/i,
  /\bwithout\b[^.]{0,30}\bsponsorship\b/i,
  /\bnot\s+(eligible|available)\b[^.]{0,20}\bsponsorship\b/i,
  /\b(US|U\.S\.)\s+citizen(ship)?\s+(is\s+)?required\b/i,
  /\bcitizenship\s+(is\s+)?required\b/i,
  /\bcitizens?\s+only\b/i,
];
const DEFAULT_NO_SPONSOR_AFFIRMATIVE = [
  /will\s+sponsor/i,
  /\bdo(es)?\s+sponsor/i,
  /sponsorship\s+(is\s+)?(available|provided|offered)/i,
  /(visa\s+)?sponsorship\s+available/i,
  /\bable\s+to\s+sponsor/i,
];

let companySelectors = [...DEFAULT_COMPANY_SELECTORS];
let jobCardSelectors = [...DEFAULT_JOB_CARD_SELECTORS];
let noSponsorNegative = [...DEFAULT_NO_SPONSOR_NEGATIVE];
let noSponsorAffirmative = [...DEFAULT_NO_SPONSOR_AFFIRMATIVE];

const cache = new Map();
let apiHealthy = null;
let apiHealthCheckedAt = 0;
let apiDownUntil = 0;

let filteringEnabled = true;
let sessionJobsScanned = 0;
let sessionSponsorsFound = 0;

const reportedMissCards = new WeakSet();
const cardCompanyAnchors = new WeakMap();

/** Prefer session storage; fall back to local if unavailable. */
function getSessionStorage() {
  try {
    if (!chrome.runtime?.id) return null;
    return chrome.storage?.session ?? chrome.storage?.local;
  } catch {
    return null;
  }
}

function safeStorageGet(keys, callback) {
  try {
    const storage = getSessionStorage();
    if (!storage) {
      callback({});
      return;
    }
    storage.get(keys, (data) => {
      if (chrome.runtime.lastError) {
        callback({});
        return;
      }
      callback(data || {});
    });
  } catch {
    callback({});
  }
}

function safeStorageSet(values) {
  try {
    getSessionStorage()?.set(values);
  } catch {
    /* LinkedIn iframes may block storage */
  }
}

function applyFilteringEnabled(enabled) {
  filteringEnabled = enabled;
  if (!filteringEnabled) {
    document.querySelectorAll('.h1b-badge').forEach((el) => el.remove());
    document.querySelectorAll('[data-h1b-done], [data-h1b-name-attempts]').forEach((card) => {
      delete card.dataset.h1bDone;
      delete card.dataset.h1bNameAttempts;
    });
    return;
  }
  processJobs();
}

safeStorageGet({ filteringEnabled: true, jobsScanned: 0, sponsorsFound: 0 }, (data) => {
  filteringEnabled = data?.filteringEnabled !== false;
  sessionJobsScanned = data?.jobsScanned || 0;
  sessionSponsorsFound = data?.sponsorsFound || 0;
  scheduleInitialPasses();
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.action === 'setFilteringEnabled') {
    applyFilteringEnabled(message.enabled);
    getSessionStorage()?.set({ filteringEnabled: message.enabled });
  }
});

getSessionStorage()?.onChanged?.addListener((changes, areaName) => {
  if (areaName !== 'session' && areaName !== 'local') return;
  if (changes.filteringEnabled) {
    applyFilteringEnabled(changes.filteringEnabled.newValue !== false);
  }
});

const MAX_NAME_ATTEMPTS = 8;
const FETCH_TIMEOUT_MS = 8000;
const CONFIG_FETCH_TIMEOUT_MS = 5000;
const CONFIG_REFRESH_MS = 30 * 60 * 1000;

/** API calls via background service worker (no LinkedIn page CORS). */
function extensionApiFetch(path, options = {}) {
  return new Promise((resolve, reject) => {
    if (!chrome.runtime?.id) {
      reject(new Error('Extension context unavailable'));
      return;
    }
    chrome.runtime.sendMessage(
      {
        action: 'apiFetch',
        path,
        method: options.method || 'GET',
        body: options.body,
        timeoutMs: options.timeoutMs,
      },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!response?.ok) {
          reject(new Error(response?.error || `HTTP ${response?.status ?? 'error'}`));
          return;
        }
        resolve(response.data);
      }
    );
  });
}


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

// Compile config-delivered regex source strings into RegExp objects, skipping any
// that are malformed or absurdly long. Returns [] if nothing valid was provided.
function compileNoSponsorPatterns(list) {
  if (!Array.isArray(list)) return [];
  const out = [];
  for (const src of list) {
    if (typeof src !== 'string' || src.length === 0 || src.length > 300) continue;
    try {
      out.push(new RegExp(src, 'i'));
    } catch (_) {
      // ignore an invalid pattern rather than breaking the whole rule set
    }
  }
  return out;
}

function applyExtensionConfig(config) {
  if (!config?.selectors) return false;
  let updated = false;
  if (applySelectorList(config.selectors.company_name, DEFAULT_COMPANY_SELECTORS, (v) => {
    companySelectors = [...new Set([...LEFT_LIST_COMPANY_SELECTORS, ...v])];
    updated = true;
  })) {
    updated = true;
  }
  if (applySelectorList(config.selectors.job_card, DEFAULT_JOB_CARD_SELECTORS, (v) => {
    jobCardSelectors = v;
  })) {
    updated = true;
  }
  if (config.no_sponsor) {
    // Only replace negatives if config gives at least one valid pattern, so a bad
    // payload can't silently disable 🔴 detection. Affirmatives may legitimately be
    // emptied (a more aggressive ruleset), so honor an explicit array as-is.
    const neg = compileNoSponsorPatterns(config.no_sponsor.negative);
    if (neg.length) {
      noSponsorNegative = neg;
      updated = true;
    }
    if (Array.isArray(config.no_sponsor.affirmative)) {
      noSponsorAffirmative = compileNoSponsorPatterns(config.no_sponsor.affirmative);
      updated = true;
    }
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
  try {
    const config = await extensionApiFetch('/config', { timeoutMs: CONFIG_FETCH_TIMEOUT_MS });
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
  if (/^promoted$/i.test(t)) return null;
  if (/^in\s+easy apply$/i.test(t)) return null;
  if (/\$\d|\/yr|401\s*\(\s*k\s*\)/i.test(t)) return null;
  if (/^\d+\s+(day|week|month)s?\s+ago$/i.test(t)) return null;
  return t;
}

/** True when the whole string looks like a location line, not "Company · City". */
function looksLikeLocation(text) {
  if (!text) return true;
  if (/\(remote\)|\(on-site\)|\(hybrid\)/i.test(text)) return true;
  // "San Francisco, CA" or "New York, NY" — comma + state/country code
  if (/,\s*[A-Z]{2}(\s|$|\))/i.test(text) && !text.includes('·')) return true;
  return false;
}

/** Extract company from "Acme Corp · San Francisco, CA" or plain "Acme Corp". */
function parseCompanyFromLine(text) {
  const raw = _trimCompanyText(text);
  if (!raw) return null;

  if (raw.includes('·')) {
    for (const part of raw.split('·')) {
      const name = _trimCompanyText(part);
      if (name && !looksLikeLocation(name) && isValidCompanyName(name)) return name;
    }
    return null;
  }

  if (looksLikeLocation(raw)) return null;
  return isValidCompanyName(raw) ? raw : null;
}

function isValidCompanyName(name) {
  if (!name || name.length < 2 || name.length > 70) return false;
  if (/followers|premium|insights|show\s+/i.test(name)) return false;
  if (/\d{1,3}(,\d{3})+/.test(name)) return false;
  if (/^[\d,.\s]+$/.test(name)) return false;
  return true;
}

function isDetailPanelCard(card) {
  return DETAIL_PANEL_ROOT_SELECTORS.some((sel) => card.closest(sel));
}

function isJobLinkInDetailPanel(link) {
  return !!link.closest(
    '.jobs-search__job-details, .jobs-details, .jobs-unified-top-card, ' +
      '.job-details-jobs-unified-top-card, [class*="job-details"]'
  );
}

/** Left split-pane (job list column). */
function isInLeftJobsPane(el) {
  const rect = el.getBoundingClientRect?.();
  if (!rect || (rect.width === 0 && rect.height === 0)) return false;
  const centerX = rect.left + rect.width / 2;
  return centerX < window.innerWidth * 0.52;
}

/** List row: explicitly discovered job row (not the whole left sidebar). */
function isJobsListCard(card) {
  if (isDetailPanelCard(card)) return false;
  if (card.querySelector('.jobs-unified-top-card, .job-details-jobs-unified-top-card')) {
    return false;
  }
  return card.dataset.h1bListCard === '1';
}

function containsDetailPanel(card) {
  return !!card.querySelector(
    '.jobs-unified-top-card, .job-details-jobs-unified-top-card'
  );
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

function getCompanyFromEntityLockup(card) {
  const subtitles = card.querySelectorAll(
    '.artdeco-entity-lockup__subtitle span[dir="ltr"], ' +
      '.artdeco-entity-lockup__subtitle, ' +
      '[class*="entity-lockup__subtitle"]'
  );

  for (const subtitle of subtitles) {
    if (
      !isDetailPanelCard(card) &&
      subtitle.closest('.jobs-unified-top-card, .job-details-jobs-unified-top-card')
    ) {
      continue;
    }

    const company = parseCompanyFromLine(subtitle.textContent);
    if (company && isValidCompanyName(company)) {
      cardCompanyAnchors.set(card, subtitle);
      return company;
    }
  }

  return null;
}

const LIST_LOCATION_PATTERN =
  /\b(remote|hybrid|on[\s-]?site)\b|,\s*[A-Z]{2}\b|\([A-Za-z\s]+,\s*[A-Z]{2}\)/i;
const LIST_META_PATTERN = /^[•·\-–—]+$|^(be an early|easy apply|actively|get job alerts)|promoted jobs are ranked|how promoted|\d+\+?\s*results?\b|\bbe an early applicant\b|\d+\s*(benefit|connection|applicant|result|follower)s?\b|\b(ago|viewed|about|premium|easy apply|actively|posted|reposted)\b|^[\d,]+$|%|^(your feedback|show)/i;
const LIST_JOB_TITLE_PATTERN = /\b(engineer|developer|architect|manager|analyst|scientist|designer|consultant|specialist|director|lead|senior|junior|staff|principal|intern|associate|founder|officer|executive|full[\s-]?stack|front[\s-]?end|back[\s-]?end|machine[\s-]?learning|software|generative|agentic|emerging)\b/i;

function isJunkListLine(text) {
  if (!text || text.length < 2) return true;
  if (LIST_META_PATTERN.test(text)) return true;
  return false;
}

/** Visible text lines for one list row (<p> preferred; skip nested span fragments). */
function collectListCardLines(rowContainer) {
  const lines = [];
  const seen = new Set();

  rowContainer.querySelectorAll('p, span[dir="ltr"]').forEach((el) => {
    if (el.closest('.h1b-badge')) return;
    if (el.tagName === 'SPAN' && el.closest('p')) return;

    const text = _trimCompanyText(el.textContent);
    if (!text || seen.has(text) || isJunkListLine(text)) return;

    seen.add(text);
    lines.push({ el, text, len: text.length });
  });

  return lines;
}

/** Title is first longest line in DOM order; then drop job-title / location / metadata lines. */
function pickListCompanyLine(lines) {
  if (!lines.length) return null;

  const maxLen = Math.max(...lines.map((l) => l.len));
  const titleIdx = lines.findIndex((l) => l.len === maxLen);
  const withoutTitle = titleIdx >= 0 ? lines.filter((_, i) => i !== titleIdx) : lines;

  const withoutJobTitles = withoutTitle.filter((l) => !LIST_JOB_TITLE_PATTERN.test(l.text));
  const withoutLocation = withoutJobTitles.filter((l) => !LIST_LOCATION_PATTERN.test(l.text));
  const candidates = withoutLocation.filter(
    (l) => !LIST_META_PATTERN.test(l.text) && l.len >= 2 && l.len <= 60
  );

  if (!candidates.length) return { match: null, lines, candidates };

  return { match: candidates[0], lines, candidates };
}

/**
 * Left list row structure (DOM order):
 *   line 1 (longest) = job title
 *   line 2 (short)   = company name
 *   line 3           = location / metadata
 */
function getCompanyFromListCard(rowContainer, options = {}) {
  const lines = collectListCardLines(rowContainer);
  if (lines.length === 0) return null;

  const { match, candidates } = pickListCompanyLine(lines);

  if (DEBUG_PROCESS_JOBS && !options.quiet && isInLeftJobsPane(rowContainer)) {
    console.log(
      `[H1B] LIST lines found: ${lines.length}, after filter: ${candidates.length}, ` +
        `picked: ${match?.text ?? '(none)'}`
    );
  }

  if (!match) return null;

  const name = parseCompanyFromLine(match.text) || match.text;
  if (!name || !isValidCompanyName(name)) return null;

  return { name, el: match.el };
}

function countListCardParagraphs(el) {
  return el.querySelectorAll('p').length;
}

function isTightListJobRow(el) {
  const pCount = countListCardParagraphs(el);
  return pCount >= 2 && pCount <= 6;
}

function getListCompanyFromRow(rowContainer, quiet = true) {
  return getCompanyFromListCard(rowContainer, { quiet });
}

function resolveCompanyFromLink(link, card) {
  const text = parseCompanyFromLine(link.textContent);
  if (text && isValidCompanyName(text)) {
    cardCompanyAnchors.set(card, link);
    return text;
  }
  const fromSlug = slugToLookupName(parseLinkedInCompanySlug(getLinkHref(link)));
  if (fromSlug && isValidCompanyName(fromSlug)) {
    cardCompanyAnchors.set(card, link);
    return fromSlug;
  }
  return null;
}

/** Right detail panel — stable selectors only (no paragraph scanning). */
function getCompanyFromDetailPanel(card) {
  const scope =
    document.querySelector('.jobs-unified-top-card, .job-details-jobs-unified-top-card') ||
    card.querySelector('.jobs-unified-top-card, .job-details-jobs-unified-top-card') ||
    card;

  const companyNameEl = scope.querySelector(
    '.jobs-unified-top-card__company-name, .job-details-jobs-unified-top-card__company-name'
  );

  if (companyNameEl) {
    const linkInBlock = companyNameEl.querySelector('a[href*="/company/"]');
    if (linkInBlock) {
      const fromLink = resolveCompanyFromLink(linkInBlock, card);
      if (fromLink) return fromLink;
    }

    const fromBlock = parseCompanyFromLine(companyNameEl.textContent);
    if (fromBlock && isValidCompanyName(fromBlock)) {
      cardCompanyAnchors.set(card, companyNameEl);
      return fromBlock;
    }
  }

  for (const link of scope.querySelectorAll('a[href*="/company/"]')) {
    const fromLink = resolveCompanyFromLink(link, card);
    if (fromLink) return fromLink;
  }

  return null;
}

/**
 * Resolve company name for API lookup. Prefers visible link text, then URL slug.
 */
function getCompanyLookup(card) {
  const inList = isJobsListCard(card);

  if (!inList && (isDetailPanelCard(card) || card.querySelector('.jobs-unified-top-card'))) {
    const fromDetail = getCompanyFromDetailPanel(card);
    if (fromDetail) return fromDetail;
  }

  // Left list: position-based line structure inside the row.
  if (inList) {
    let picked = getListCompanyFromRow(card);
    if (!picked) {
      let el = card.parentElement;
      for (let depth = 0; depth < 4 && el; depth++) {
        if (!isInLeftJobsPane(el) || isDetailPanelCard(el)) break;
        if (isTightListJobRow(el)) {
          picked = getListCompanyFromRow(el);
          if (picked) break;
        }
        el = el.parentElement;
      }
    }
    if (picked) {
      cardCompanyAnchors.set(card, picked.el);
      return picked.name;
    }

    for (const selector of LEFT_LIST_COMPANY_SELECTORS) {
      const el = card.querySelector(selector);
      const name = parseCompanyFromLine(el?.textContent);
      if (name) {
        cardCompanyAnchors.set(card, el);
        return name;
      }
    }

    const fromLockup = getCompanyFromEntityLockup(card);
    if (fromLockup) return fromLockup;

    const primary = card.querySelector(
      '.job-card-container__primary-description, [class*="primary-description"]'
    );
    if (primary) {
      const name = parseCompanyFromLine(primary.textContent.split('\n')[0]);
      if (name) {
        cardCompanyAnchors.set(card, primary);
        return name;
      }
    }
  }

  const candidates = getCompanyLinkCandidates(card);

  for (const c of candidates) {
    if (c.text && isValidCompanyName(c.text)) {
      cardCompanyAnchors.set(card, c.node);
      return c.text;
    }
  }

  for (const c of candidates) {
    const fromSlug = slugToLookupName(c.slug);
    if (fromSlug && isValidCompanyName(fromSlug)) {
      cardCompanyAnchors.set(card, c.node);
      return fromSlug;
    }
  }

  if (!inList) {
    const fromLockup = getCompanyFromEntityLockup(card);
    if (fromLockup && isValidCompanyName(fromLockup)) return fromLockup;
  }

  for (const selector of companySelectors) {
    const el = card.querySelector(selector);
    const name = parseCompanyFromLine(el?.textContent);
    if (name && isValidCompanyName(name)) {
      cardCompanyAnchors.set(card, el);
      return name;
    }
  }

  return null;
}


// ─────────────────────────────────────────────
// Job card discovery
// ─────────────────────────────────────────────

/** Job card leaf: has data-job-id but no nested data-job-id (list + detail rows). */
function leafDataJobIdCard(el) {
  const card = el.matches?.('[data-job-id]') ? el : el.closest?.('[data-job-id]');
  if (!card) return null;
  if (card.querySelector('[data-job-id]')) return null;
  return card;
}

function resetRemountedJobCards() {
  document.querySelectorAll('[data-h1b-done="1"]').forEach((card) => {
    if (!card.querySelector('.h1b-badge')) {
      delete card.dataset.h1bDone;
      delete card.dataset.h1bNameAttempts;
    }
  });
}

function normalizeListCard(el) {
  if (el.matches?.('.job-card-list__entity-lockup')) {
    return (
      el.closest(
        '[data-occludable-job-id], [data-job-id], li.jobs-search-results__list-item, .jobs-search-results-list__list-item, li.scaffold-layout__list-item'
      ) || el
    );
  }
  return el;
}

function findAllLeafDataJobIdCards() {
  return [...document.querySelectorAll('[data-job-id]')].filter(
    (el) => !el.querySelector('[data-job-id]')
  );
}

function findLeftListJobCards() {
  const seen = new Set();
  const cards = [];

  for (const selector of LEFT_LIST_CARD_SELECTORS) {
    let nodes;
    try {
      nodes = document.querySelectorAll(selector);
    } catch {
      continue;
    }

    for (const el of nodes) {
      let card = normalizeListCard(el);
      if (!card || isDetailPanelCard(card) || seen.has(card)) continue;
      seen.add(card);
      cards.push(card);
    }
  }

  return cards;
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
        card = leafDataJobIdCard(el);
        if (!card) continue;
      } else if (LEFT_LIST_CARD_SELECTORS.includes(selector)) {
        card = normalizeListCard(el);
        if (isDetailPanelCard(card)) continue;
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

    const byId = leafDataJobIdCard(el);
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

function findListCardContainerForJobLink(jobLink) {
  let el = jobLink;
  let best = null;
  let bestPCount = Infinity;

  for (let depth = 0; depth < 18; depth++) {
    if (!el.parentElement) break;
    el = el.parentElement;
    if (!isInLeftJobsPane(el) || isDetailPanelCard(el)) break;

    const rect = el.getBoundingClientRect?.();
    const h = rect?.height ?? 0;
    if (h < 32 || h > 480) continue;

    const pCount = countListCardParagraphs(el);
    if (pCount >= 2 && pCount <= 6 && getListCompanyFromRow(el) && pCount < bestPCount) {
      best = el;
      bestPCount = pCount;
    }
  }

  return best || jobLink.closest('li, [role="listitem"]') || jobLink.parentElement?.parentElement;
}

function getJobsListRoot() {
  return (
    document.querySelector('.jobs-search-results-list') ||
    document.querySelector('[class*="jobs-search-results-list"]') ||
    document.querySelector('.scaffold-layout__list') ||
    null
  );
}

/** Smallest row wrapper around a company <p> (one title + one company + location). */
function findTightJobRowForCompanyP(pEl) {
  let best = null;
  let el = pEl;

  for (let depth = 0; depth < 14; depth++) {
    if (!el.parentElement) break;
    el = el.parentElement;
    if (!isInLeftJobsPane(el) || isDetailPanelCard(el)) break;

    const rect = el.getBoundingClientRect?.();
    const h = rect?.height ?? 0;
    if (h < 36 || h > 420) continue;

    if (isTightListJobRow(el)) {
      best = el;
      const parent = el.parentElement;
      if (parent && isInLeftJobsPane(parent) && countListCardParagraphs(parent) > 8) {
        return el;
      }
    }
  }

  return best;
}

function findJobCardsFromListCompanyLines() {
  const seen = new Set();
  const cards = [];
  const root = getJobsListRoot();
  const scope = root || document.body;

  for (const p of scope.querySelectorAll('p')) {
    if (root && !root.contains(p)) continue;
    if (!isInLeftJobsPane(p)) continue;
    if (p.closest('.jobs-unified-top-card, .job-details-jobs-unified-top-card, .h1b-badge')) {
      continue;
    }
    const card = findTightJobRowForCompanyP(p);
    if (!card || seen.has(card)) continue;

    if (containsDetailPanel(card)) continue;

    const picked = getListCompanyFromRow(card);
    if (!picked) continue;

    cardCompanyAnchors.set(card, picked.el);
    card.dataset.h1bListCard = '1';
    seen.add(card);
    cards.push(card);
  }

  return cards;
}

/** Primary list discovery when data-job-id / data-occludable-job-id are absent. */
function findJobCardsFromJobViewLinks() {
  const seen = new Set();
  const cards = [];

  for (const link of document.querySelectorAll('a[href*="/jobs/view/"]')) {
    if (isJobLinkInDetailPanel(link)) continue;

    const card = findListCardContainerForJobLink(link);
    if (!card || isDetailPanelCard(card) || containsDetailPanel(card) || seen.has(card)) {
      continue;
    }
    const picked = getListCompanyFromRow(card);
    if (!picked) continue;

    cardCompanyAnchors.set(card, picked.el);
    card.dataset.h1bListCard = '1';
    seen.add(card);
    cards.push(card);
  }

  return cards;
}

function findDetailPanelCards() {
  const top = document.querySelector(
    '.jobs-unified-top-card, .job-details-jobs-unified-top-card'
  );
  return top ? [top] : [];
}

function dedupeToInnermostCards(cards) {
  return cards.filter((card) => {
    if (isDetailPanelCard(card)) return true;
    return !cards.some((other) => other !== card && other.contains(card));
  });
}

/**
 * A genuine job card always carries one of: a /jobs/view/ link, a job id, or
 * the detail top-card root. Page chrome the loose scanners can pick up — the
 * left nav sidebar ("Groups", "Newsletters"), the "Jobs based on your
 * preferences" header, people cards — has none of these, so requiring a job
 * signal keeps badges on real listings only.
 */
function hasJobSignal(card) {
  if (!card) return false;

  // The job title link / job id usually lives on the enclosing list item, not
  // on the inner wrapper the loose scanners return — so look up to it. The nav
  // sidebar and the page header are not inside a job <li>, so they stay out.
  const scope =
    card.closest?.('[data-job-id], [data-occludable-job-id], li, [role="listitem"]') || card;

  if (scope.matches?.('[data-job-id], [data-occludable-job-id]')) return true;
  if (scope.querySelector?.('[data-job-id], [data-occludable-job-id]')) return true;
  if (scope.querySelector?.('a[href*="/jobs/view/"]')) return true;

  if (card.matches?.('.jobs-unified-top-card, .job-details-jobs-unified-top-card')) return true;
  if (card.closest?.('.jobs-unified-top-card, .job-details-jobs-unified-top-card')) return true;
  if (card.querySelector?.('.jobs-unified-top-card, .job-details-jobs-unified-top-card')) return true;

  return false;
}

/**
 * People/network suggestion cards ("People you may know", connections, search
 * people results) look like job rows — name + "Title at Company" — but are not
 * jobs. They carry a member profile link (/in/) or a Connect/Follow button and
 * never a job link, so badges must not be added to them.
 */
function isPeopleCard(card) {
  if (!card) return false;

  // A real job card always has a job link or a job id; people cards never do.
  const hasJobSignal =
    card.matches?.('[data-job-id], [data-occludable-job-id]') ||
    card.closest?.('[data-job-id], [data-occludable-job-id]') ||
    card.querySelector?.('a[href*="/jobs/view/"]') ||
    card.querySelector?.('.jobs-unified-top-card, .job-details-jobs-unified-top-card');
  if (hasJobSignal) return false;

  // Member profile link or a people-recommendation container ⇒ a person, not a job.
  if (card.querySelector?.('a[href*="/in/"]')) return true;
  if (
    card.closest?.(
      '.discover-entity-type-card, .pymk-card, [class*="discover"], ' +
        '[componentkey*="PEOPLE"], [data-view-name*="people"]'
    )
  ) {
    return true;
  }

  // Fallback: a Connect/Follow action with no job signal is a people card.
  const actionText = card.textContent || '';
  if (/\b(Connect|Follow|Message)\b/.test(actionText) && card.querySelector('button, a[role="button"]')) {
    return true;
  }

  return false;
}

function findJobCards() {
  const seen = new Set();
  const cards = [];

  const merge = (found) => {
    for (const card of found) {
      if (!card || seen.has(card)) continue;
      seen.add(card);
      cards.push(card);
    }
  };

  merge(findJobCardsFromListCompanyLines());
  merge(findDetailPanelCards());
  merge(findJobCardsFromJobViewLinks());
  merge(findLeftListJobCards());
  merge(findAllLeafDataJobIdCards());
  merge(findJobCardsFromSelectors());

  return dedupeToInnermostCards(cards).filter(
    (card) => hasJobSignal(card) && !isPeopleCard(card)
  );
}


// ─────────────────────────────────────────────
// processJobs / API / badge
// ─────────────────────────────────────────────

async function ensureApiHealthy() {
  const now = Date.now();
  if (now < apiDownUntil) return false;
  if (apiHealthy === true && now - apiHealthCheckedAt < 60_000) return true;
  if (apiHealthy === false && now - apiHealthCheckedAt < 30_000) return false;

  try {
    await extensionApiFetch('/health', { timeoutMs: 5000 });
    apiHealthy = true;
    apiDownUntil = 0;
  } catch (error) {
    apiHealthy = false;
    apiDownUntil = now + 120_000;
    console.warn(
      '[H1B] API offline — badges need a live server at /check. ' +
        'Redeploy Railway (h1bchecker-production).',
      error?.message || error
    );
  }
  apiHealthCheckedAt = now;
  return apiHealthy;
}

async function mapWithConcurrency(items, limit, fn) {
  const results = new Array(items.length);
  let index = 0;

  async function worker() {
    while (index < items.length) {
      const i = index++;
      results[i] = await fn(items[i], i);
    }
  }

  const workers = Math.min(limit, items.length);
  await Promise.all(Array.from({ length: workers }, () => worker()));
  return results;
}

async function processJobs() {
  if (!filteringEnabled) return;

  // 🔴 detail-panel scan runs every cycle, independent of the list-card flow below.
  processDetailNoSponsor().catch(() => {});

  resetRemountedJobCards();

  let jobCards = findJobCards();
  if (FOCUS_LIST_BADGES_ONLY) {
    const listCards = jobCards.filter(isJobsListCard);
    if (listCards.length) jobCards = listCards;
  }

  if (jobCards.length === 0) return;

  const pending = [];

  if (DEBUG_PROCESS_JOBS) {
    const listCount = jobCards.filter(isJobsListCard).length;
    const viewLinks = document.querySelectorAll('a[href*="/jobs/view/"]').length;
    console.log(
      `[H1B] processJobs: ${jobCards.length} card(s) ` +
        `(${listCount} list), job-view-links=${viewLinks}`
    );
  }

  for (const card of jobCards) {
    const status = card.dataset.h1bDone;
    if (status === '1' || status === 'abandon' || status === 'pending') continue;

    const companyName = getCompanyLookup(card);

    if (DEBUG_PROCESS_JOBS) {
      const jobId =
        card.getAttribute('data-job-id') ||
        card.getAttribute('data-occludable-job-id') ||
        '(no id)';
      console.log(
        `[H1B] ${isJobsListCard(card) ? 'LIST' : 'DETAIL'} card ${String(jobId).slice(0, 14)}… company:`,
        companyName ?? '(null)'
      );
    }

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

  if (!(await ensureApiHealthy())) {
    for (const { card } of pending) {
      if (card.dataset.h1bDone === 'pending') delete card.dataset.h1bDone;
    }
    return;
  }

  await mapWithConcurrency(pending, API_CHECK_CONCURRENCY, async ({ card, companyName }) => {
    const sponsorInfo = await getH1BInfo(companyName);
    if (!sponsorInfo) {
      if (card.dataset.h1bDone === 'pending') delete card.dataset.h1bDone;
      return;
    }
    addBadge(card, sponsorInfo);
    card.dataset.h1bDone = '1';
  });
}

function reportSelectorMiss(card) {
  if (reportedMissCards.has(card)) return;
  reportedMissCards.add(card);

  extensionApiFetch('/report-selector-miss', {
    method: 'POST',
    body: {
      html: card.innerHTML.slice(0, 300),
      url: window.location.href,
      selectors_tried: [...companySelectors, ...jobCardSelectors],
    },
  }).catch(() => {});
}

function getH1BInfo(companyName) {
  const key = companyName.toLowerCase();

  if (Date.now() < apiDownUntil) {
    return Promise.resolve(null);
  }

  if (cache.has(key)) {
    return cache.get(key);
  }

  const promise = (async () => {
    try {
      const data = await extensionApiFetch(
        `/check?company=${encodeURIComponent(companyName)}`,
        { timeoutMs: FETCH_TIMEOUT_MS }
      );
      return {
        sponsors: data.sponsors_h1b,
        count: data.h1b_count,
        // "strong" | "weak" | "none" — drives the 3-tier badge. Falls back to
        // null for older backends, in which case addBadge derives it from `sponsors`.
        tier: data.tier ?? null,
      };
    } catch (error) {
      const msg = String(error?.message || error);
      if (msg.includes('Application not found') || msg.includes('HTTP 404')) {
        apiHealthy = false;
        apiDownUntil = Date.now() + 120_000;
      }
      if (DEBUG_PROCESS_JOBS) console.error('H1B API error:', error);
      cache.delete(key);
      return null;
    }
  })();

  cache.set(key, promise);
  return promise;
}

// 3-tier badge styling, keyed by the backend's `tier` field.
const TIER_BADGE = {
  strong: { cls: 'h1b-strong', text: '🟢 Strong H1B sponsor' },
  weak: { cls: 'h1b-weak', text: '🟡 Has sponsored before' },
  none: { cls: 'h1b-none', text: '⚪ No H1B record' },
};

// Resolve a tier from the API payload. Prefer the backend's `tier`; fall back to
// the legacy boolean so an old backend still renders something sensible.
function resolveTier(info) {
  if (info.tier && TIER_BADGE[info.tier]) return info.tier;
  return info.sponsors ? 'weak' : 'none';
}

function addBadge(card, info) {
  if (card.querySelector('.h1b-badge')) return;

  const tier = resolveTier(info);
  const spec = TIER_BADGE[tier];

  const badge = document.createElement('div');
  badge.className = `h1b-badge ${spec.cls}`;
  badge.textContent = spec.text;

  const anchor = cardCompanyAnchors.get(card);
  const listMode = isJobsListCard(card);

  const detailCompany = card.querySelector(
    '.job-details-jobs-unified-top-card__company-name, ' +
      '.jobs-unified-top-card__company-name, ' +
      '[class*="top-card__company-name"], ' +
      'a[href*="/company/"]'
  );

  if (listMode) {
    badge.classList.add('h1b-badge--list');

    const listAnchor =
      (anchor && card.contains(anchor) ? anchor : null) ||
      (detailCompany && card.contains(detailCompany) ? detailCompany : null);

    if (listAnchor) {
      listAnchor.insertAdjacentElement('afterend', badge);
      recordBadgeStats(info);
      return;
    }

    const picked = getListCompanyFromRow(card);
    if (picked?.el && card.contains(picked.el)) {
      picked.el.insertAdjacentElement('afterend', badge);
      recordBadgeStats(info);
      return;
    }

    const lockup = card.querySelector('.job-card-list__entity-lockup, .job-card-container');
    (lockup || card).appendChild(badge);
    recordBadgeStats(info);
    return;
  }

  if (detailCompany) {
    detailCompany.insertAdjacentElement('afterend', badge);
  } else if (anchor?.parentElement) {
    anchor.parentElement.appendChild(badge);
  } else {
    card.insertBefore(badge, card.firstChild);
  }

  recordBadgeStats(info);
}

function recordBadgeStats(info) {
  sessionJobsScanned++;
  if (info.sponsors) sessionSponsorsFound++;

  safeStorageSet({
    jobsScanned: sessionJobsScanned,
    sponsorsFound: sessionSponsorsFound,
  });

  chrome.runtime.sendMessage({
    action: 'statsUpdate',
    data: {
      jobsScanned: sessionJobsScanned,
      sponsorsFound: sessionSponsorsFound,
    },
  }).catch(() => {});
}


// ─────────────────────────────────────────────
// Per-posting 🔴 "no sponsorship" (detail panel only — needs the JD)
// ─────────────────────────────────────────────

const TIER_HISTORY_TEXT = {
  strong: '🟢 History: strong sponsor',
  weak: '🟡 History: has sponsored before',
  none: '⚪ History: no H1B record',
};

function getDetailPanelRoot() {
  return document.querySelector(
    '.job-details-jobs-unified-top-card, .jobs-unified-top-card'
  );
}

function getDetailJdText() {
  for (const sel of DETAIL_JD_SELECTORS) {
    const text = document.querySelector(sel)?.textContent?.trim();
    if (text) return text;
  }
  return '';
}

// Conservative: flag only when the JD denies sponsorship AND never affirms it.
function detectNoSponsor(text) {
  if (!text) return false;
  if (!noSponsorNegative.some((re) => re.test(text))) return false;
  return !noSponsorAffirmative.some((re) => re.test(text));
}

// Stable per-posting key so we evaluate each opened job once. We fold the JD length
// into the key (not just currentJobId) on purpose: the URL's currentJobId flips
// synchronously on a job switch, but #job-details keeps the PREVIOUS posting's text
// for a few hundred ms while LinkedIn fetches the new one. Keying on the id alone
// would stamp the new posting using the old JD and then lock that verdict in (the
// dedup check short-circuits before the real JD ever loads). Including jd.length
// makes that stale reading a different key, so it self-corrects once the JD updates.
function currentPostingKey(jd) {
  let id = '';
  try {
    id = new URL(window.location.href).searchParams.get('currentJobId') || '';
  } catch (_) {
    /* malformed URL — fall through to length-only key */
  }
  return `${id}:len:${jd.length}`;
}

function renderDetailRedBadge(root, tier) {
  if (root.querySelector('.h1b-nosponsor-wrap')) return;

  const wrap = document.createElement('div');
  wrap.className = 'h1b-nosponsor-wrap';

  const red = document.createElement('div');
  red.className = 'h1b-badge h1b-nosponsor';
  red.textContent = '🔴 This posting: no sponsorship';
  wrap.appendChild(red);

  const histText = TIER_HISTORY_TEXT[tier];
  if (histText) {
    const sub = document.createElement('div');
    sub.className = 'h1b-nosponsor-sub';
    sub.textContent = histText;
    wrap.appendChild(sub);
  }

  const company = root.querySelector(
    '.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name'
  );
  if (company) company.insertAdjacentElement('afterend', wrap);
  else root.insertBefore(wrap, root.firstChild);
}

// Scan the open detail panel's JD and, if it denies sponsorship, stamp a 🔴 badge with
// the employer's historical tier as subtext. Runs independently of the list-card flow
// (which FOCUS_LIST_BADGES_ONLY restricts to the left list), so 🔴 works even there.
async function processDetailNoSponsor() {
  if (!filteringEnabled) return;
  const root = getDetailPanelRoot();
  if (!root) return;

  const jd = getDetailJdText();
  if (!jd) return; // description not mounted yet — a later pass will retry

  const key = currentPostingKey(jd);
  if (root.dataset.h1bNosponsorKey === key) return; // already evaluated this posting
  root.dataset.h1bNosponsorKey = key;

  // Drop any stale badge left over from a previously-viewed posting in this root.
  root.querySelectorAll('.h1b-nosponsor-wrap').forEach((el) => el.remove());

  if (!detectNoSponsor(jd)) return;

  // Best-effort historical tier subtext — the 🔴 badge itself never depends on the API.
  let tier = null;
  try {
    const company = getCompanyFromDetailPanel(root);
    if (company) {
      const info = await getH1BInfo(company);
      if (info) tier = info.tier ?? (info.sponsors ? 'weak' : 'none');
    }
  } catch (_) {
    /* ignore — show the red badge regardless */
  }

  // The user may have switched postings during the await; bail if so.
  if (root.dataset.h1bNosponsorKey !== key) return;
  renderDetailRedBadge(root, tier);
}


// ─────────────────────────────────────────────
// Startup & DOM observer
// ─────────────────────────────────────────────

function scheduleInitialPasses() {
  const delays = [0, 300, 800, 1800, 3500, 6000];
  for (const ms of delays) {
    setTimeout(processJobs, ms);
  }
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
      if (node.matches?.('[data-job-id], [data-occludable-job-id]')) return true;
      if (node.querySelector?.('[data-job-id], [data-occludable-job-id]')) return true;
      if (node.matches?.('.jobs-unified-top-card, .job-details-jobs-unified-top-card')) return true;
      if (node.querySelector?.('.jobs-unified-top-card, .job-details-jobs-unified-top-card')) {
        return true;
      }
      if (node.matches?.('.jobs-search-results-list__list-item, .job-card-list__entity-lockup')) {
        return true;
      }
      if (node.querySelector?.('.jobs-search-results-list__list-item, .job-card-list__entity-lockup')) {
        return true;
      }
      if (node.matches?.("a[href*='/jobs/view/'], a[href*='/company/']")) return true;
      if (node.querySelector?.("a[href*='/jobs/view/'], a[href*='/company/']")) return true;
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
