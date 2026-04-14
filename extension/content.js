const API_URL = 'https://h1bchecker-production.up.railway.app';
const cache = new Map();
// Main function
async function processJobs() {
// Find all job cards
const jobCards = document.querySelectorAll(
'[data-job-id], [class*="job-card"], [class*="base-card"]'
);
for (const card of jobCards) {
if (card.dataset.h1bProcessed) continue;
card.dataset.h1bProcessed = 'true';
// Extract company name
const companyName = getCompanyName(card);
if (!companyName) continue;

// Get sponsor info
const sponsorInfo = await getH1BInfo(companyName);
if (!sponsorInfo) continue;

// Add badge
addBadge(card, sponsorInfo);
}
}
// Extract company name from job card
// LinkedIn currently puts the company name in artdeco-entity-lockup__subtitle > div
function getCompanyName(card) {
  const subtitle = card.querySelector('.artdeco-entity-lockup__subtitle div[dir="ltr"]');
  if (subtitle) return subtitle.textContent.trim();
  // Fallback selectors in case LinkedIn changes the structure again
  const subtitleAlt = card.querySelector('.artdeco-entity-lockup__subtitle');
  if (subtitleAlt) return subtitleAlt.textContent.trim();
  return null;
}
// Call your API
async function getH1BInfo(companyName) {
const key = companyName.toLowerCase();
// Check cache
if (cache.has(key)) {
return cache.get(key);
}
try {
const response = await fetch(`${API_URL}/check?company=${encodeURIComponent(companyName)}`);
const data = await response.json();
const result = {
  sponsors: data.sponsors_h1b,
  count: data.h1b_count
};

cache.set(key, result);
return result;
} catch (error) {
console.error('H1B API error:', error);
return null;
}
}
// Add sponsor badge to card
function addBadge(card, info) {
// Check if badge already exists
if (card.querySelector('.h1b-badge')) return;
const badge = document.createElement('div');
badge.className = `h1b-badge ${info.sponsors ? 'sponsor-yes' : 'sponsor-no'}`;
badge.innerHTML = info.sponsors
? '✓ Sponsors H1B'
: '✗ No Sponsor';
// Insert badge
const companyElem = card.querySelector('[class*="company"]');
if (companyElem && companyElem.parentElement) {
companyElem.parentElement.appendChild(badge);
} else {
card.insertBefore(badge, card.firstChild);
}
}
// Run on page load and when new jobs load
if (document.readyState === 'loading') {
document.addEventListener('DOMContentLoaded', processJobs);
} else {
processJobs();
}
// Monitor for new jobs (LinkedIn lazy loads)
// Debounce so we don't fire hundreds of times per second
let debounceTimer = null;
const observer = new MutationObserver(() => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(processJobs, 800);
});
observer.observe(document.body, {
childList: true,
subtree: true
});