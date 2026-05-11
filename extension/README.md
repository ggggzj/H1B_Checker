# H1B Checker for LinkedIn Jobs — Extension

Chrome extension (Manifest V3) that shows a simple H1B sponsorship indicator on LinkedIn **job** pages by calling the H1B Checker API.

## Install

### Option A — Chrome Web Store (recommended for end users)

> Replace the link below once the listing is approved.

[Install from the Chrome Web Store](https://chromewebstore.google.com/detail/REPLACE_WITH_EXTENSION_ID)

### Option B — Manual install from GitHub release

For users who prefer not to wait for Web Store review, or who want to inspect the code first:

1. Download **`h1b-checker-extension.zip`** from the latest release:
   <https://github.com/ggggzj/H1B_Checker/releases/latest/download/h1b-checker-extension.zip>
2. **Unzip it.** Chrome needs a folder that contains `manifest.json` at the top level — not the `.zip` itself.
3. Open `chrome://extensions`, turn on **Developer mode** (top right), click **Load unpacked**, and select the unzipped folder.
4. Open any LinkedIn jobs page (e.g. <https://www.linkedin.com/jobs/>) — you should see a green “✓ Sponsors H1B” or red “✗ No Sponsor” badge on each card.

## Scope

- **Content scripts** run only on `https://www.linkedin.com/jobs/*`.
- **Network access** is limited to that origin plus the configured API host (`https://h1bchecker-production.up.railway.app/*`).
- The extension reads **company names from visible job cards** and sends them over HTTPS to the API for lookup. It does **not** read your LinkedIn profile, messages, cookies, or credentials.

## Files

| File | Role |
|------|------|
| `manifest.json` | MV3 manifest, minimal permissions |
| `content.js` | DOM helpers, `fetch` to `/check` |
| `style.css` | Badge styles |
| `icons/` | `16`, `32`, `48`, `128` PNG assets |
| `privacy-policy.html` | Local copy of the privacy policy (host a public copy for the Web Store) |

## Pack for Chrome Web Store

1. Build the zip — either download it from the latest GitHub release, or run `./scripts/package-extension.sh` from the repo root. The resulting `h1b-checker-extension.zip` already has `manifest.json` at the root, which is what the Web Store requires.
2. In the Developer Dashboard, set **Privacy policy URL** to a **public HTTPS** page (e.g. host `docs/privacy-policy.html` via GitHub Pages).
3. **Single purpose:** Show H1B sponsorship status on LinkedIn job listings using DOL LCA data.
4. **Data usage:** The extension sends **company names from visible job listings** to the backend; it does not collect LinkedIn credentials or sell data.

## Local development

1. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select this `extension/` directory.
2. If the API runs locally, update `content.js` (`API_URL`) and add a matching entry under `host_permissions` in `manifest.json` (for example `http://localhost:8000/*`).

## Customize

- Replace `author` and `homepage_url` in `manifest.json` if your GitHub account or repo URL differs.
- Point `API_URL` in `content.js` to your deployment.
