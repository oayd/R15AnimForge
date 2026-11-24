# R15 AnimForge

**Convert JoJo's Bizarre Adventure: All-Star Battle R (and other CyberConnect2 game) animations into ready-to-use Roblox R15 `.fbx` files — completely offline on Windows.**

- [User Guide (PDF)](docs/R15_AnimForge_User_Guide.pdf)
- [Latest Release & Downloads](https://github.com/oayd/R15AnimForge/releases/latest)

## Features

- **100% offline & keeps original files safe**: No internet connection, login, or tracking. Never changes or overwrites your original game files — everything exports into a separate output folder.
- **Finds characters automatically**: Point it at an extracted game folder and it detects every character (including DLCs, Stands, horses, and split body/animation files), displays readable character names instead of internal codes, and hides costume-only skins with no animations.
- **One-step conversion to Roblox R15**: Unpacks compressed `.xfbin` game archives automatically and converts animations onto an upright Roblox R15 rig through Blender — no manual bone mapping or fixing twisted T-pose arms.
- **Creates missing Roblox loops**: Automatically builds smooth **Idle**, **Alt Idle**, **Jump**, and **Fall** animations when a character does not have clean loops for them.
- **Checks & auto-fixes Roblox upload errors**: Tests your exported clips against Roblox's Avatar Item limits (movement speed, sinking through the floor, extreme joint angles, and choppy loop seams) and saves repaired copies to a separate `*_fixed` folder.
- **Roblox Studio helper & Animation Pack checker**: Includes a copy-paste Roblox Studio script to fix imported rotation curves and checks that all 9 slots of a Roblox Animation Pack are filled and ready to upload.

## Requirements & Setup

- **OS**: Windows 10 / 11 x64
- **Blender**: Blender 5.3+ installed (auto-detected under `C:\Program Files\Blender Foundation\`, bundled under `<app>\blender\blender.exe`, or set via `--blender` / `ANIMFORGE_BLENDER`).
- **CC2 XFBIN Addon (`Blender-XFBIN-Importer`)**: Due to the upstream addon's custom distribution license, `Blender-XFBIN-Importer` is **not** bundled in this public repository. Install [Al-Hydra/Blender-XFBIN-Importer](https://github.com/Al-Hydra/Blender-XFBIN-Importer) in Blender's addons folder or point R15 AnimForge to it via `--addon-dir` or `ANIMFORGE_ADDON_DIR`. See [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

## CLI Usage

```powershell
animforge scan "D:\MyGame\data\data\spc" --charcode 4rhn01 --json
animforge convert "D:\MyGame\data\data\spc" "D:\Exported_R15_FBX" --charcode 4rhn01 --build-procedural
animforge validate "D:\Exported_R15_FBX"
animforge fix "D:\Exported_R15_FBX" --apply strip_translation,floor_lift,clamps,smoothing
animforge build-pack "D:\Exported_R15_FBX\pack_manifest.json" --write-template --studio-script-out "D:\Exported_R15_FBX\animforge_studio_postprocess.lua"
animforge selftest --json
```

Double-click `AnimForge.exe` (or run `animforge` with no arguments) to launch the 3-tab dark-mode GUI (`Convert`, `Validate / Fix`, `Pack & Studio`).

## Building From Source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install numpy pytest pyinstaller -e .
.\.venv\Scripts\pytest.exe -v
powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
```

---

## Code Signing Policy & Privacy Statement

Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

- **Team Roles**: Committers, Reviewers, and Approvers are the repository maintainers (with mandatory Multi-Factor Authentication enabled on GitHub and SignPath). Release binaries are built exclusively on GitHub-hosted runners (`.github/workflows/release.yml`) from tagged commits before signing.
- **Privacy Policy**: This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. R15 AnimForge operates 100% offline with zero telemetry.
- **Verification & Security**: See [`SECURITY.md`](SECURITY.md) for Authenticode, SHA-256, GitHub Build Provenance Attestation, and VirusTotal verification instructions.
- **VirusTotal Reports (`v1.0.0`)**:
  - [`AnimForge.exe`](https://www.virustotal.com/gui/file/7d0e0ba50b36f129481c89d01b85948046e10f7e9ce44b82f908301435bad576)
  - [`R15AnimForge-v1.0.0-win64-portable.zip`](https://www.virustotal.com/gui/file/182db0177d1e01826d2310c9f7c51b50095e0d26ffb7326736ad466754cc273c)
