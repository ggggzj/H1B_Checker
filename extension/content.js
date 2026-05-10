/**
 * content.js — H1B Checker Chrome Extension Content Script
 *
 * This file runs automatically on LinkedIn job pages (see manifest matches).
 * It is the bridge between the LinkedIn UI and the H1B Checker API.
 *
 * What it does:
 * 1. Finds all job cards on the current LinkedIn jobs page
 * 2. Extracts the company name from each card
 * 3. Calls the Railway API to check if that company sponsors H1B
 * 4. Injects a colored badge ("✓ Sponsors H1B" or "✗ No Sponsor") onto each card
 * 5. Watches for new cards that LinkedIn loads dynamically (infinite scroll)
 *
 * Language: JavaScript (runs in Chrome browser, not Node.js or Python)
 * Triggered by: manifest.json → content_scripts → matches LinkedIn URLs
 */

// Base URL of the deployed FastAPI backend on Railway
// All API calls will be made to this domain
const API_URL = 'https://h1bchecker-production.up.railway.app';

// In-memory cache(localStorage): stores {companyName (lowercase): {sponsors, count}} results
// Prevents making duplicate API calls for the same company on one page load
const cache = new Map();

const MAX_NAME_ATTEMPTS = 8;


// ─────────────────────────────────────────────
// FUNCTION 1: processJobs
// Main orchestrator — finds cards, gets company name, fetches API, adds badge
// ─────────────────────────────────────────────
async function processJobs() {
  // Find all elements with data-job-id on the page
  // Then keep only the OUTERMOST ones — filter out any element whose
  // parent also has data-job-id, which would mean it is a nested duplicate
  // of the same logical card. This prevents adding multiple badges to the
  // same visible card when LinkedIn nests [data-job-id] elements inside each other.
  const allCards = document.querySelectorAll('[data-job-id]');
  const jobCards = [...allCards].filter(
    card => !card.parentElement?.closest('[data-job-id]')
  );

  // Loop over every job card found on the page
  for (const card of jobCards) {
    if (card.dataset.h1bDone === '1' || card.dataset.h1bDone === 'abandon') continue;

    const companyName = getCompanyName(card);

    if (!companyName) {
      const n = parseInt(card.dataset.h1bNameAttempts || '0', 10) + 1;
      card.dataset.h1bNameAttempts = String(n);
      if (n >= MAX_NAME_ATTEMPTS) {
        card.dataset.h1bDone = 'abandon';
      }
      continue;
    }

    card.dataset.h1bNameAttempts = '0';

    const sponsorInfo = await getH1BInfo(companyName);

    if (!sponsorInfo) continue;

    addBadge(card, sponsorInfo);
    card.dataset.h1bDone = '1';
  }
}


// ─────────────────────────────────────────────
// FUNCTION 2: getCompanyName
// Reads the company name text from a single job card element
// ─────────────────────────────────────────────
function _trimCompanyText(s) {
  if (!s) return null;
  const t = String(s).replace(/\s+/g, ' ').trim();
  if (!t || t.length > 200) return null;
  return t;
}

function getCompanyName(card) {
  const pick = (el) => _trimCompanyText(el?.textContent);

  // Job detail / some list layouts — entity lockup subtitle
  let el = card.querySelector('.artdeco-entity-lockup__subtitle div[dir="ltr"]');
  if (el) return pick(el);

  el = card.querySelector('.artdeco-entity-lockup__subtitle');
  if (el) return pick(el);

  // Jobs search-results list (common 2025–2026)
  el = card.querySelector('.job-card-container__company-name');
  if (el) {
    const name = pick(el);
    if (name) return name;
  }

  el = card.querySelector('.base-search-card__subtitle');
  if (el) return pick(el);

  // Secondary line under job title in compact cards
  el = card.querySelector('.base-card__subtitle, .base-main-card__subtitle');
  if (el) return pick(el);

  // Company profile link text inside the card
  const link = card.querySelector('a[href*="/company/"]');
  if (link) {
    const name = pick(link);
    if (name) return name;
  }

  return null;
}


// ─────────────────────────────────────────────
// FUNCTION 3: getH1BInfo
// Calls the Railway API to look up a company's H1B sponsorship history
// ─────────────────────────────────────────────
async function getH1BInfo(companyName) {
  // Normalize the key to lowercase so "Google" and "google" hit the same cache entry
  const key = companyName.toLowerCase();

  // Return cached result immediately if we already looked up this company
  if (cache.has(key)) {
    return cache.get(key);
  }

  try {
    // Make a GET request to the /check endpoint
    // encodeURIComponent handles special characters like "&" or "+" in company names
    // e.g. "Ernst & Young" → "Ernst%20%26%20Young"
    const response = await fetch(`${API_URL}/check?company=${encodeURIComponent(companyName)}`);

    if (!response.ok) {
      console.error('H1B API HTTP error:', response.status, await response.text());
      return null;
    }

    // Parse the JSON response body
    // Expected shape: { sponsors_h1b: true/false, h1b_count: 8810, ... }
    const data = await response.json();

    // Extract only the fields we need and store in a clean object
    const result = {
      sponsors: data.sponsors_h1b, // boolean: does the company sponsor H1B?
      count: data.h1b_count        // number: how many certified LCA filings
    };

    // Save result to cache so we don't call the API again for this company
    cache.set(key, result);
    return result;

  } catch (error) {
    // If the fetch fails (network error, API down, etc.), log and return null
    // Returning null tells processJobs() to skip this card silently
    console.error('H1B API error:', error);
    return null;
  }
}


// ─────────────────────────────────────────────
// FUNCTION 4: addBadge
// Creates and inserts the H1B status badge into a job card
// ─────────────────────────────────────────────
function addBadge(card, info) {
  // Do nothing if a badge already exists on this card (safety check)
  if (card.querySelector('.h1b-badge')) return;

  // Create a new <div> element that will become the visible badge
  const badge = document.createElement('div');

  // Assign CSS classes: "h1b-badge" always, plus either "sponsor-yes" or "sponsor-no"
  // These classes are defined in style.css and control the green/red colors
  badge.className = `h1b-badge ${info.sponsors ? 'sponsor-yes' : 'sponsor-no'}`;

  // Set the badge text based on sponsorship status
  // ✓ green badge for sponsors, ✗ red badge for non-sponsors
  badge.innerHTML = info.sponsors
    ? '✓ Sponsors H1B'
    : '✗ No Sponsor';

  // Try to insert the badge next to the company name element inside the card
  const companyElem = card.querySelector('[class*="company"]');
  if (companyElem && companyElem.parentElement) {
    // Append badge as the last child of the company element's parent
    companyElem.parentElement.appendChild(badge);
  } else {
    // Fallback: insert badge at the very top of the card if no company element found
    card.insertBefore(badge, card.firstChild);
  }
}


// ─────────────────────────────────────────────
// STARTUP: run processJobs when the page is ready
// ─────────────────────────────────────────────

// If the page HTML is still being parsed, wait for it to finish before running
// If the page is already loaded (e.g. extension was just installed), run immediately
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', processJobs);
} else {
  processJobs();
}


// ─────────────────────────────────────────────
// MUTATION OBSERVER: watch for new job cards loaded by infinite scroll
// ─────────────────────────────────────────────

// LinkedIn is a single-page app — when the user scrolls, new job cards are
// added to the DOM without a full page reload. We need to detect this and
// process the new cards automatically.

// debounceTimer holds the ID of the pending setTimeout so we can cancel it
let debounceTimer = null;

// MutationObserver fires a callback every time child elements are added/removed
const observer = new MutationObserver(() => {
  // Cancel any previously scheduled call — we only want to run once after
  // the DOM has settled, not on every single individual DOM mutation
  clearTimeout(debounceTimer);

  // Schedule processJobs to run 800ms after the last DOM change
  // This prevents hammering the API when LinkedIn updates many elements at once
  debounceTimer = setTimeout(processJobs, 800);
});

// Start observing the entire page body for structural changes
// childList: true  — watch for elements being added or removed
// subtree: true    — watch all descendants, not just direct children
observer.observe(document.body, {
  childList: true,
  subtree: true
});
