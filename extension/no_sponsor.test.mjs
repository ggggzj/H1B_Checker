/**
 * Regression test for the per-posting 🔴 "no sponsorship" detector.
 *
 * Run: `node extension/no_sponsor.test.mjs`
 *
 * The patterns below MUST mirror DEFAULT_NO_SPONSOR_{NEGATIVE,AFFIRMATIVE} in
 * content.js (and EXTENSION_NO_SPONSOR_* in main.py, which test_config.py pins).
 * content.js is a content script, not a module, so the rules are duplicated here
 * rather than imported. Keep the three copies in sync.
 *
 * Behavior: conservative — flag only when a NEGATIVE matches AND no AFFIRMATIVE does.
 */

const NEGATIVE = [
  /\b(do(es)?\s+not|will\s+not|cannot|are\s+not\s+able\s+to|unable\s+to)\b[^.]{0,40}\bsponsor/i,
  /\bno\b[^.]{0,20}\b(visa\s+)?sponsorship\b/i,
  /\bwithout\b[^.]{0,30}\bsponsorship\b/i,
  /\bnot\s+(eligible|available)\b[^.]{0,20}\bsponsorship\b/i,
  /\b(US|U\.S\.)\s+citizen(ship)?\s+(is\s+)?required\b/i,
  /\bcitizenship\s+(is\s+)?required\b/i,
  /\bcitizens?\s+only\b/i,
];
const AFFIRMATIVE = [
  /will\s+sponsor/i,
  /\bdo(es)?\s+sponsor/i,
  /sponsorship\s+(is\s+)?(available|provided|offered)/i,
  /(visa\s+)?sponsorship\s+available/i,
  /\bable\s+to\s+sponsor/i,
];

function detectNoSponsor(text) {
  if (!text) return false;
  if (!NEGATIVE.some((re) => re.test(text))) return false;
  return !AFFIRMATIVE.some((re) => re.test(text));
}

const CASES = [
  // [text, expectedRed]
  ['We are unable to sponsor visas for this position.', true],
  ['This role does not offer visa sponsorship.', true],
  ['Must be a US citizen. Citizenship is required.', true],
  ['U.S. Citizenship is required for this role.', true],
  ['Candidates must be authorized to work without sponsorship.', true],
  ['Citizens only.', true],
  ['Not eligible for sponsorship.', true],
  ['Cannot provide visa sponsorship at this time.', true],
  ['We will sponsor qualified candidates.', false],
  ['Visa sponsorship available for the right candidate.', false],
  ['We do sponsor H1B for exceptional engineers.', false],
  ['We are able to sponsor work visas.', false],
  ['Great team, competitive salary, remote friendly.', false],
  ['We do not sponsor for this role, but we will sponsor for senior roles.', false],
  // Known conservative miss: affirmative "sponsorship ... available" suppresses it.
  ['No sponsorship is available for this position.', false],
  ['', false],
];

let pass = 0;
let fail = 0;
for (const [text, expected] of CASES) {
  const got = detectNoSponsor(text);
  const ok = got === expected;
  if (ok) pass++;
  else fail++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  expect=${expected} got=${got}  "${text.slice(0, 55)}"`);
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
