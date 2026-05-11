# H1B Checker for LinkedIn Jobs — Extension

Chrome extension (Manifest V3) that shows a simple H1B sponsorship indicator on LinkedIn **job** pages by calling the H1B Checker API.

## Scope

- **Content scripts** run only on `https://www.linkedin.com/jobs/*`.
- **Network access** is limited to that origin plus the configured API host (`https://h1bchecker-production.up.railway.app/*`).

## Files

| File | Role |
|------|------|
| `manifest.json` | MV3 manifest, minimal permissions |
| `content.js` | DOM helpers, `fetch` to `/check` |
| `style.css` | Badge styles |
| `icons/` | `16`, `32`, `48`, `128` PNG assets |
| `privacy-policy.html` | Local copy of the privacy policy (host a public copy for the Web Store) |

## Pack for Chrome Web Store

1. Zip **only** the contents of the `extension/` folder (not the parent repo). Exclude `.DS_Store` and `__MACOSX`.
2. In the Developer Dashboard, set **Privacy policy URL** to a **public HTTPS** page (for example GitHub Pages). You can publish `docs/privacy.md` / `extension/privacy-policy.html` after hosting.
3. **Single purpose:** Show H1B sponsorship status on LinkedIn job listings using DOL LCA data.
4. **Data usage:** The extension sends **company names from visible job listings** to your backend; it does not collect LinkedIn credentials or sell data.

## Install from GitHub (zip)

1. Download **`h1b-checker-extension.zip`** from the latest release:  
   `https://github.com/ggggzj/H1B_Checker/releases/latest/download/h1b-checker-extension.zip`  
   (The file is built automatically when you **publish** a GitHub Release.)
2. Unzip it. Chrome expects a **folder** with `manifest.json` at the top level inside that folder.
3. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose that **unzipped folder** (not the `.zip` file).

To build the same zip locally: `./scripts/package-extension.sh` from the repo root.

## Local development

1. Open `chrome://extensions`, enable **Developer mode**, **Load unpacked**, select this `extension/` directory.
2. If the API runs locally, update `content.js` (`API_URL`) and add a matching entry under `host_permissions` in `manifest.json` (for example `http://localhost:8000/*`).

## Customize

- Replace `author` and `homepage_url` in `manifest.json` if your GitHub account or repo URL differs.
- Point `API_URL` in `content.js` to your deployment.
