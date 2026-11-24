"""EDIT F isolation tests: `convert` never touches validation/fix; `fix` requires --apply & preserves originals."""

from __future__ import annotations

import builtins
import json
import math
import sys
from pathlib import Path
from unittest import mock

import pytest

from animforge.cli import main as cli_main
from animforge.config import load_profile
from animforge.constants import MAPPED_R15_BONE_NAMES
from animforge.fixer import FixConfigurationError
from animforge.math_retarget import euler_to_quat, q_identity


def test_convert_makes_zero_calls_to_validation_or_fixer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()

    # Block importing animforge.validation and animforge.fixer to prove convert doesn't touch them
    for mod in ("animforge.validation", "animforge.fixer"):
        sys.modules.pop(mod, None)

    real_import = builtins.__import__

    def _guard_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if "validation" in name or "fixer" in name:
            raise AssertionError(f"convert illegally imported {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _guard_import)

    fake_rep = {
        "output_dir": str(out),
        "exported_count": 1,
        "files": [{"filename": "clip.fbx", "frames": 10, "face_only": False}],
        "verified_for_roblox_upload": False,
    }
    with mock.patch("animforge.blender_runner.find_blender_executable", return_value=Path("blender.exe")), \
         mock.patch("animforge.blender_runner.find_xfbin_addon_dir", return_value=None), \
         mock.patch("animforge.blender_runner.run_convert_job", return_value=fake_rep) as m_conv:
        rc = cli_main(["convert", str(inp), str(out), "--json"])
        assert rc == 0
        m_conv.assert_called_once()


def test_fix_cli_refuses_without_apply_and_never_touches_originals(tmp_path: Path) -> None:
    orig_dir = tmp_path / "clips"
    orig_dir.mkdir()
    dummy_fbx = orig_dir / "sample.fbx"
    dummy_fbx.write_bytes(b"ORIGINAL_FBX_BYTES")

    # Refuses without --apply (returns non-zero)
    rc = cli_main(["fix", str(orig_dir)])
    assert rc != 0
    assert dummy_fbx.read_bytes() == b"ORIGINAL_FBX_BYTES"

    # Even with --apply, attempting to set --out-dir == orig_dir is rejected
    rc2 = cli_main(["fix", str(orig_dir), "--apply", "clamps", "--out-dir", str(orig_dir), "--blender", "blender.exe"])
    assert rc2 != 0
    assert dummy_fbx.read_bytes() == b"ORIGINAL_FBX_BYTES"
