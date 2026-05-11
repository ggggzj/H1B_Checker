# Chrome Web Store — Listing Copy

Paste these strings into the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole) when publishing **H1B Checker for LinkedIn Jobs**.

---

## Name (max 75 chars)

```
H1B Checker for LinkedIn Jobs
```

## Summary (max 132 chars, single line)

```
See if a company sponsors H1B visas right on LinkedIn job listings, powered by official US DOL LCA disclosure data.
```

## Category

`Productivity`

## Language

`English (United States)`

---

## Description (max 16,000 chars)

```
H1B Checker for LinkedIn Jobs adds a small sponsorship indicator next to every company on LinkedIn job pages, so you can tell at a glance whether an employer has a history of sponsoring H1B visas before you spend time tailoring an application.

WHAT IT DOES
• Reads the company name from each visible job card on linkedin.com/jobs.
• Looks the company up in our database of official U.S. Department of Labor (DOL) H-1B LCA disclosure filings.
• Shows a green “✓ Sponsors H1B” badge if certified LCA filings exist, or a red “✗ No Sponsor” badge otherwise.
• Updates automatically as you scroll through job results — no clicks required.

WHO IT IS FOR
• International students and graduates on F-1 / OPT looking for sponsorship-friendly employers.
• H-1B visa holders considering a job switch.
• Anyone who wants quick, evidence-based signal from public DOL data instead of guessing.

DATA SOURCE
All sponsorship signals come from public U.S. Department of Labor LCA disclosure datasets. We aggregate the public filings into a database and the extension queries it through an HTTPS API.

PRIVACY (SHORT VERSION)
• The extension only runs on https://www.linkedin.com/jobs/*.
• It only sends company names that are already visible on the page to our API for lookup.
• It does NOT read your LinkedIn profile, messages, cookies, password, résumé, or browsing history.
• It does NOT sell data and does NOT show ads.
Full policy: https://ggggzj.github.io/H1B_Checker/privacy-policy.html

OPEN SOURCE
Source code, data pipeline, and API are open source on GitHub:
https://github.com/ggggzj/H1B_Checker

LIMITATIONS
• A “No Sponsor” badge means we did not find recent LCA filings — it does not guarantee the company will never sponsor. Use it as one signal among many.
• Company names on LinkedIn don’t always match the legal entity on LCA filings. We do our best to match common variants, and we welcome corrections via GitHub issues.

FEEDBACK
Bugs, requests, or data corrections: please open an issue at
https://github.com/ggggzj/H1B_Checker/issues
```

---

## Single Purpose statement

```
Display H1B sponsorship status, based on official U.S. Department of Labor LCA disclosure data, on LinkedIn job listing pages.
```

---

## Permission justifications (Privacy practices tab)

### Host permission: `https://www.linkedin.com/jobs/*`

```
Required to inject the sponsorship badge into LinkedIn job listing pages. The content script only runs on URLs under https://www.linkedin.com/jobs/* and only reads the company name text from visible job cards in the DOM.
```

### Host permission: `https://h1bchecker-production.up.railway.app/*`

```
Required to send each visible company name to the extension's own backend API and receive a yes/no H1B sponsorship result derived from public DOL LCA disclosure data. The API host is owned by the extension developer.
```

### `activeTab` / `tabs` / `storage` / `scripting` / `<all_urls>`

```
Not requested. The extension declares no broad permissions and runs only on the two host patterns listed above.
```

### Remote code

```
No. All JavaScript and CSS are bundled in the extension package. The extension only fetches JSON data (not executable code) from the API host.
```

### Data collected (check the smallest set possible)

Tick **only**: **Website content → other site content (company names)**. Leave everything else **unchecked**.

Justification text:

```
The extension transmits only the company name text from job cards the user is currently viewing on linkedin.com/jobs. It does not collect authentication info, personal identifiers, location, financial info, health info, personal communications, web history, user activity beyond job-card visibility, or website content other than company names.
```

### Disclosures to confirm

- [x] I do not sell or transfer user data to third parties outside the approved use cases.
- [x] I do not use or transfer user data for purposes unrelated to my item's single purpose.
- [x] I do not use or transfer user data to determine creditworthiness or for lending purposes.

---

## Privacy policy URL

Host `docs/privacy-policy.html` on GitHub Pages, then use:

```
https://ggggzj.github.io/H1B_Checker/privacy-policy.html
```

(If you change the repo name or owner, update the URL accordingly.)

---

## Suggested screenshots (1280 × 800 PNG)

1. LinkedIn jobs search results with several **green ✓ Sponsors H1B** badges visible.
2. A single LinkedIn job detail page showing the badge next to the company name.
3. A close-up of one card with a **red ✗ No Sponsor** badge to illustrate the negative state.

Tip: take the screenshots at default Chrome zoom on a 1280×800 viewport, or scale down a larger screenshot with an image editor before uploading.
