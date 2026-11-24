# Third-Party Licenses & Attribution — R15 AnimForge

## 1. Blender 5.3.0 (GPL v3.0 or later)
- **Website**: https://www.blender.org/
- **License**: GNU General Public License, Version 3 or later (`blender/license/` inside portable build).
- **Source Offer**: Blender's complete corresponding source code is available at https://download.blender.org/source/ and https://projects.blender.org/blender/blender.
- R15 AnimForge invokes Blender 5.3.0 as an independent headless subprocess (`blender.exe --background ...`); R15 AnimForge Python code only interacts via documented CLI arguments and stdio JSON events (`@@EVT`), while `worker_blender.py` runs inside Blender's Python runtime. Do **not** re-sign Blender's own binaries when running `sign.ps1`.

## 2. xfbin_lib (MIT License)
- **Upstream URLs**:
  - Original: https://github.com/SutandoTsukai181/xfbin_lib
  - Fork referenced in `.gitmodules`: https://github.com/superuser590/xfbin_lib
- **Copyright**: Copyright (c) 2021 SutandoTsukai181
- **License**: MIT License (full text preserved at `vendor/Blender-XFBIN-Importer/xfbin_lib/LICENSE`).

## 3. Blender-XFBIN-Importer (Custom License v1.1 — RESTRICTED DISTRIBUTION)
- **Upstream URL**: https://github.com/Al-Hydra/Blender-XFBIN-Importer
- **Authors**: SutandoTsukai181, HydraBladeZ, Dei, TheLeonX
- **Wiki / Docs**: https://github.com/SutandoTsukai181/cc2_xfbin_blender/wiki
- **Local Patches Applied** (Blender 5.3 compatibility):
  1. Slotted/layered actions support (`layers[0].strips[0].channelbag(slot).fcurves` and explicit `action_slot` assignment).
  2. Explicit 4x4 matrix reconstruction for Blender 5.x `IDPropertyArray` (`arm_bone.get('matrix')` length-16 transposed).
- **IMPORTANT LICENSE NOTICE (`vendor/Blender-XFBIN-Importer/LICENSE` v1.1)**:
  > *"The Software is only permitted to be used for modifying CyberConnect2 (CC2) published game titles... You are not permitted to distribute the Software or any modified versions of the Software outside of the Discord Servers without explicit permission from the creator... Derivative works cannot be used for commercial purposes."*
  - Because of this restriction, **public or commercial distribution of an installer bundling `vendor/Blender-XFBIN-Importer` requires removing `vendor/Blender-XFBIN-Importer` (switching users to `--addon-dir` / user-supplied addon) or obtaining explicit permission from the addon creator.** See `DEVIATIONS.md`.

## 4. Python & Bundled Runtime Dependencies
- **Python 3.13**: PSF License (https://docs.python.org/3/license.html)
- **NumPy**: BSD 3-Clause License (https://numpy.org/doc/stable/license.html)
- **PyInstaller** (build-time bundler): GPL v2 with Bootloader Exception permitting standalone executables without forcing GPL onto user scripts (https://pyinstaller.org/en/stable/license.html).
