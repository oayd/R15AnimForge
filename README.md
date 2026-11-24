# R15 AnimForge

**Offline Windows desktop app & CLI for CyberConnect2 `.xfbin` to Roblox R15 `.fbx` animation conversion, procedural builders, opt-in upload validation, auto-fix, and Animation Pack assembly.**

Full operator manual: [`docs/R15_AnimForge_User_Guide.pdf`](docs/R15_AnimForge_User_Guide.pdf)

## Features

- **100% Offline & Non-Destructive**: Zero telemetry, zero runtime network calls, never modifies source `.xfbin` or original `.fbx` files, and isolates Blender runtime state inside `%LOCALAPPDATA%\AnimForge`.
- **Universal JoJo ASBR / CC2 Support**: Auto-detects any CC2 character code (`0bao01`–`8wou01`, including DLCs, Stands, horses, and split body/motion pairs like `6pci01` + `6pci02`), decodes **139+ ASBR character names** (plus optional `messageInfo.bin.xfbin`), filters out costume-only skins with no animations, and learns stance naming formulas (`bas_r_*`, `bas_s_*`).
- **XFBIN Discovery & CRI LAYLA Decompression**: Recursively discovers `*<charcode>*.xfbin`, sniffs `NUCC` / `CRILAYLA` headers, decodes CRI LAYLA payloads, locates `bod1` + motion archives (`mot`/`anm`/`viewer`), and excludes VFX/offset archives (`eff1`/`ghh1`/`anmofs`).
- **Exact World-Delta Retargeting (Blender 5.3+)**: Reconstructs upright Roblox R15 `I-pose` target frames (`R15_Rig`), resolves T-pose-to-I-pose rest offsets (`±90°`), computes hemisphere-continuous quaternions at 30 FPS, supports Blender 5.x slotted/layered actions, and scales pelvis translation.
- **Procedural Builders**: Synthesizes seamless cyclic `Idle Loop (91f)`, `Alt Idle Look (121f)`, `Jump (36f, Loop=false)`, and `Fall Loop (36f, Loop=true)`.
- **Opt-In Upload Validation (`V02`–`V09`, `V11`–`V12`) & Explicit Auto-Fix**: Evaluates Roblox Avatar Item rules at 30 & 70 FPS with Roxie reference calibration; auto-fix repairs `V03` speed, `V04` angular spikes, `V05` floor depth, `V06` non-LowerTorso translation, `V08` joint angles, `V09` low tips, and `V11` loop seams into a separate `*_fixed` folder and automatically re-validates on disk.
- **Studio Post-Processor (`animforge_studio_postprocess.lua`) & Pack Manifest (`V10`)**: Converts Studio quaternion `RotationCurve` instances to `EulerRotationCurve (XYZ)` and validates the 9-slot Roblox pack schema.

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

- **Team Roles**: Committers, Reviewers, and Approvers: `@oayd` (with mandatory Multi-Factor Authentication enabled on GitHub and SignPath). Release binaries are built exclusively on GitHub-hosted runners (`.github/workflows/release.yml`) from tagged commits before signing.
- **Privacy Policy**: This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. R15 AnimForge operates 100% offline with zero telemetry.
- **Verification & Security**: See [`SECURITY.md`](SECURITY.md) for Authenticode, SHA-256, and GitHub Build Provenance Attestation verification instructions.
