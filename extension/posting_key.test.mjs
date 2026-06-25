/**
 * Regression test for the per-posting dedup key (currentPostingKey in content.js).
 *
 * Run: `node extension/posting_key.test.mjs`
 *
 * Bug this guards against: on a job switch, the URL's `currentJobId` updates
 * synchronously, but #job-details keeps the PREVIOUS posting's text for a few
 * hundred ms while LinkedIn fetches the new description. A key built from the id
 * ALONE would (a) stamp the new posting using the stale JD, then (b) lock that
 * verdict in — the dedup check `dataset.key === key` short-circuits before the
 * real JD ever loads. Folding jd.length into the key makes the stale reading a
 * DIFFERENT key from the real one, so processDetailNoSponsor re-evaluates and
 * self-corrects once the JD updates.
 *
 * This mirrors currentPostingKey from content.js (content.js is a content script,
 * not a module, so we can't import it). The URL is injected instead of read from
 * window.location. Keep this in sync with content.js.
 */

function currentPostingKey(href, jd) {
  let id = '';
  try {
    id = new URL(href).searchParams.get('currentJobId') || '';
  } catch (_) {
    /* malformed URL — fall through to length-only key */
  }
  return `${id}:len:${jd.length}`;
}

let pass = 0;
let fail = 0;
function check(name, cond) {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}`);
}

const SEARCH = 'https://www.linkedin.com/jobs/search/?currentJobId=';
const JD_X = 'We are unable to sponsor visas for this position.'; // no-sponsor
const JD_Y = 'We will sponsor qualified candidates for this great role.'; // sponsors

// --- The core regression: the stale-JD window during a job switch ---

// 1. Same id + same JD => same key (idempotent: one posting is evaluated once).
check(
  'same id + same JD is idempotent',
  currentPostingKey(SEARCH + '111', JD_Y) === currentPostingKey(SEARCH + '111', JD_Y),
);

// 2. THE BUG: switched to posting Y (url id=222) but #job-details still shows X's
//    stale JD. That stale reading MUST NOT share a key with Y's real, loaded JD —
//    otherwise the wrong verdict locks in and never re-evaluates.
const staleKey = currentPostingKey(SEARCH + '222', JD_X); // id=222, but old JD text
const realKey = currentPostingKey(SEARCH + '222', JD_Y); // id=222, real JD loaded
check('stale-JD key differs from real-JD key (same id) — self-corrects', staleKey !== realKey);

// 3. Switching postings (different id) always yields a different key.
check(
  'different currentJobId => different key',
  currentPostingKey(SEARCH + '111', JD_X) !== currentPostingKey(SEARCH + '222', JD_X),
);

// --- Fallback behavior when the URL carries no currentJobId ---

// 4. Standalone /jobs/view page (no currentJobId): still keyed, by JD length.
check(
  'no currentJobId still produces a key',
  currentPostingKey('https://www.linkedin.com/jobs/view/123/', JD_X) === `:len:${JD_X.length}`,
);

// 5. Malformed URL doesn't throw; degrades to a length-only key.
let threw = false;
let malformedKey = '';
try {
  malformedKey = currentPostingKey('not a url', JD_X);
} catch (_) {
  threw = true;
}
check('malformed URL does not throw', !threw);
check('malformed URL falls back to length-only key', malformedKey === `:len:${JD_X.length}`);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
