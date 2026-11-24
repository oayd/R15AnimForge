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

4. **VirusTotal Scan Reports:**
   Inspect public multi-engine antivirus scan results for official release binaries:
   - `v1.0.0` `AnimForge.exe`: <https://www.virustotal.com/gui/file/7d0e0ba50b36f129481c89d01b85948046e10f7e9ce44b82f908301435bad576>
   - `v1.0.0` `R15AnimForge-v1.0.0-win64-portable.zip`: <https://www.virustotal.com/gui/file/182db0177d1e01826d2310c9f7c51b50095e0d26ffb7326736ad466754cc273c>

   Future GitHub Releases automatically link their SHA-256 VirusTotal pages in the release description. Note that unsigned PyInstaller executables can occasionally receive generic false-positive heuristics on minor AV engines prior to code-signing activation; always cross-check `SHA256SUMS.txt` and GitHub Build Provenance Attestation.

## Reporting a Vulnerability

Please report security issues privately using GitHub **Security Advisories** (*Security → Advisories → Report a vulnerability*) or open a confidential report with exact reproduction steps. Do not publicly disclose unpatched vulnerabilities in third-party dependencies or shipped binaries.
