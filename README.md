# H1B Checker for LinkedIn Jobs

A free, open-source Chrome extension that adds an **H1B sponsorship badge** to every job card on LinkedIn, so you can tell at a glance whether a company has a history of sponsoring H1B visas — before you spend time tailoring an application.

The badge is one of two:

- **Green** `✓ Sponsors H1B` — the company has certified U.S. Department of Labor (DOL) H-1B LCA filings in our database.
- **Red** `✗ No Sponsor` — we did not find recent LCA filings for that company.

> All data comes from **public DOL H-1B LCA disclosure datasets**. The extension does **not** read your LinkedIn account, messages, cookies, or password.

---

## Install

### Option A — Chrome Web Store (recommended)

Install with one click from the Chrome Web Store:

👉 **[H1B Checker for LinkedIn Jobs](https://chromewebstore.google.com/detail/h1b-checker-for-linkedin/fjlefpeahmeahjbadnnogdnailahdafe)**

Updates from the store are automatic after you install.

### Option B — Install manually from GitHub

You only need to do this once. It takes about 1 minute.

1. **Download the extension package.**  
   Click this link to download the latest build:  
   👉 **<https://github.com/ggggzj/H1B_Checker/releases/latest/download/h1b-checker-extension.zip>**

2. **Unzip the file.**  
   Double-click `h1b-checker-extension.zip` to extract it. You should now have a folder named something like `h1b-checker-extension` that contains `manifest.json`, `content.js`, an `icons/` folder, etc.

   > Chrome cannot load a `.zip` file directly — it needs the **unzipped folder**.

3. **Open Chrome's extensions page.**  
   Paste this into Chrome's address bar and press Enter:

   ```
   chrome://extensions
   ```

4. **Turn on Developer mode.**  
   Toggle the **Developer mode** switch in the top-right corner of the page to **ON**.

5. **Load the extension.**  
   Click **Load unpacked** (top-left) and select the **unzipped folder** from step 2.

6. **You're done.**  
   You should see a new entry **H1B Checker for LinkedIn Jobs** in your extensions list. Make sure its toggle is **ON** (blue).

### Also works on (Chromium-based browsers)

The same steps work on browsers built on Chromium:

- **Microsoft Edge** — go to `edge://extensions` and enable developer mode.
- **Brave** — go to `brave://extensions`.
- **Arc / Opera / Vivaldi** — open the extensions page in the menu and enable developer mode.

---

## How to use

1. Open any LinkedIn jobs page, for example:  
   <https://www.linkedin.com/jobs/>  
   or any search result URL like  
   <https://www.linkedin.com/jobs/search/?keywords=software%20engineer>

2. Browse jobs as you normally would. Each job card will be tagged with a colored badge:

   | Badge | Meaning |
   |---|---|
   | 🟢 `✓ Sponsors H1B` | The company has certified DOL LCA filings (likely sponsors H1B). |
   | 🔴 `✗ No Sponsor` | We did not find recent LCA filings for that company. |

3. Scroll down — as LinkedIn loads more jobs, badges appear automatically. No clicks or login required.

> **Tip:** The badge is a **signal, not a guarantee.** Some companies sponsor occasionally and may not appear in our snapshot of the data. Always cross-check on the company's careers page before applying.

---

## Updating the extension

**If you installed from the Chrome Web Store:** Chrome updates the extension automatically (you can also open `chrome://extensions` and click **Update**).

**If you installed manually (Option B):**

1. Download the new `h1b-checker-extension.zip` from <https://github.com/ggggzj/H1B_Checker/releases/latest>.
2. Unzip it (you can overwrite the old folder).
3. Open `chrome://extensions`, find **H1B Checker for LinkedIn Jobs**, and click the small **circular arrow** (refresh) icon on that card.

---

## Uninstall

Open `chrome://extensions`, find **H1B Checker for LinkedIn Jobs**, and click **Remove**.

---

## Privacy

- The content script only runs on `https://www.linkedin.com/jobs/*`.
- The extension only sends **company names that are already visible on the page** to our API for lookup — over HTTPS.
- The extension does **not** read your LinkedIn profile, messages, cookies, password, résumé, or browsing history.
- The extension does **not** sell data and does **not** show ads.

Full policy: <https://ggggzj.github.io/H1B_Checker/privacy-policy.html>

---

## Troubleshooting

### I don't see any badges on LinkedIn

1. Make sure you are on a URL that starts with `https://www.linkedin.com/jobs/` (the badges only render on job pages, not on the LinkedIn feed or profile).
2. Open `chrome://extensions` and make sure **H1B Checker for LinkedIn Jobs** is **enabled** (toggle is blue).
3. Refresh the LinkedIn jobs tab.
4. Wait a couple of seconds after scrolling — the extension waits briefly to avoid hammering the API.

### Chrome shows "Manifest file is missing or unreadable"

You selected the `.zip` file instead of the unzipped folder. Unzip it first, then re-select the folder that contains `manifest.json` at its top level.

### A company I know sponsors H1B shows "No Sponsor"

LinkedIn job cards sometimes display a parent company, subsidiary, or marketing brand whose legal name doesn't match the entity that files LCAs. Please open a GitHub issue with the LinkedIn company name and we'll add a mapping:  
<https://github.com/ggggzj/H1B_Checker/issues>

### A company shows "Sponsors H1B" but I want to verify

You can confirm directly on the U.S. Department of Labor's public site:  
<https://www.dol.gov/agencies/eta/foreign-labor/performance>

---

## FAQ

**Is this affiliated with LinkedIn or the U.S. government?**  
No. This is an independent open-source project. LinkedIn is a registered trademark of LinkedIn Corporation. We use the official DOL LCA dataset, which is public.

**Is it free?**  
Yes. The extension is free and open source under the MIT License.

**Does it work on Safari / Firefox?**  
Not yet. It's a Chromium extension; works on Chrome, Edge, Brave, Arc, Opera, and Vivaldi.

**Will it slow down LinkedIn?**  
No noticeable impact. The extension caches results in memory so it queries the API at most once per company per page load.

---

## For developers

The repo also contains the backend (FastAPI + PostgreSQL) that powers the API:

- **Backend API docs and setup** → [`docs/BACKEND.md`](docs/BACKEND.md)
- **Extension source and packaging** → [`extension/README.md`](extension/README.md)
- **Build the zip locally** → run `./scripts/package-extension.sh` from the repo root.

Pull requests and issues are welcome.

---

## License

MIT License. See repository for details.
