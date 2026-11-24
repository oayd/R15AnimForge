# Security & Verification Policy

## Privacy & Offline Guarantee

R15 AnimForge runs **100% offline**. It makes zero runtime network calls, collects zero telemetry, and will never transfer any information to other networked systems unless specifically initiated by the user or system administrator. Application settings and Blender worker sandboxes are stored locally under `%LOCALAPPDATA%\AnimForge`.

## Verifying Official Releases

1. **Windows Authenticode Signature (SignPath Foundation):**
   Right-click `AnimForge.exe` → **Properties** → **Digital Signatures** and verify a valid timestamped signature issued via **SignPath Foundation**, or run in PowerShell:
   ```powershell
   Get-AuthenticodeSignature .\AnimForge.exe | Format-List Status, SignerCertificate, TimeStamperCertificate
   ```
   `Status` must be `Valid`.

2. **SHA-256 Checksum Verification:**
   Compare the downloaded release archive/executable against `SHA256SUMS.txt` attached to each GitHub Release:
   ```powershell
   Get-FileHash .\AnimForge.exe -Algorithm SHA256
   ```

3. **GitHub Build Provenance Attestation:**
   Verify that release artifacts were built directly from this repository on GitHub-hosted runners:
   ```powershell
   gh attestation verify R15AnimForge-v1.0.0-win64-portable.zip --repo oayd/R15AnimForge
   ```

## Reporting a Vulnerability

Please report security issues privately using GitHub **Security Advisories** (*Security → Advisories → Report a vulnerability*) or open a confidential report with exact reproduction steps. Do not publicly disclose unpatched vulnerabilities in third-party dependencies or shipped binaries.
