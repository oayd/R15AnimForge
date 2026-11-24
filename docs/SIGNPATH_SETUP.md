# Uploading to GitHub & Enabling Free SignPath Code Signing

Follow these steps to push **R15 AnimForge** to a public GitHub repository and activate free Windows Authenticode code signing through **SignPath Foundation**.

---

## Step 1 — Push to a Public GitHub Repository

1. Ensure [Multi-Factor Authentication (2FA)](https://github.com/settings/security) is enabled on your GitHub account (required by SignPath Foundation).
2. Create a **new empty public repository** on GitHub (for example `R15AnimForge`) — leave "Add a README" and "Add .gitignore" unchecked so history matches.
3. From your local `R15AnimForge` repository folder in PowerShell, configure your Git author identity (if not already set), commit the staged files, and push:

```powershell
cd path\to\R15AnimForge
git config user.name "Your Name"
git config user.email "you@users.noreply.github.com"
git commit -m "Initial public release of R15 AnimForge v1.0.0"
git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git
git push -u origin main
```

4. In your GitHub repository **Settings → Branches**, add a branch protection rule for `main` (e.g., restrict force pushes / deletions). SignPath checks that release artifacts come from protected branches on GitHub-hosted runners.

---

## Step 2 — Apply for Free Signing at SignPath Foundation

1. Go to [https://signpath.org/](https://signpath.org/) (or [about.signpath.io/open-source](https://about.signpath.io/open-source)) and submit the Open Source application for your public GitHub repository.
2. Reference these exact repository details if asked:
   - **License**: `MIT` ([`LICENSE`](../LICENSE))
   - **Code Signing Policy & Privacy Disclosure**: Included in [`README.md`](../README.md) and [`SECURITY.md`](../SECURITY.md)
   - **CI System**: GitHub Actions on `windows-latest` ([`.github/workflows/release.yml`](../.github/workflows/release.yml))
   - **Artifact**: `unsigned-exe` (`AnimForge.exe`)
   - **Signing Policy Slug**: `release-signing`
3. Install the **SignPath** GitHub App on your repository when prompted during onboarding.

---

## Step 3 — Configure GitHub Repository Secrets & Variables

Once your SignPath organization & `release-signing` policy are approved, go to your GitHub repository **Settings → Secrets and variables → Actions** and add:

### Repository Secret
- `SIGNPATH_API_TOKEN` — API token generated for the GitHub Actions CI user in SignPath.

### Repository Variables
- `SIGNPATH_ORGANIZATION_ID` — Your SignPath Organization ID (UUID).
- `SIGNPATH_PROJECT_SLUG` — Project slug configured in SignPath (default: `r15-animforge`).
- `SIGNPATH_SIGNING_POLICY_SLUG` — Policy slug configured in SignPath (default: `release-signing`).
- `SIGNPATH_ENABLED` — Set to `true` to turn on live signing in `release.yml`. *(While unset or not `true`, tagged releases still build, generate SHA-256 checksums, and attach GitHub Build Provenance Attestations cleanly without failing.)*

---

## Step 4 — Cut a Signed Release

1. Push a version tag:
   ```powershell
   git tag v1.0.0
   git push origin v1.0.0
   ```
2. GitHub Actions builds `AnimForge.exe`, runs all unit tests + `selftest`, and submits `unsigned-exe` to SignPath.
3. Open your **SignPath dashboard → Signing Requests** and **Approve** the request (if manual maintainer approval is enabled on your `release-signing` policy).
4. Once approved, GitHub Actions downloads the Authenticode-signed `AnimForge.exe`, verifies `Get-AuthenticodeSignature` (`Status == Valid`), packages `R15AnimForge-v1.0.0-win64-portable.zip`, generates `SHA256SUMS.txt` + Sigstore Build Provenance Attestations, and publishes the GitHub Release automatically.
