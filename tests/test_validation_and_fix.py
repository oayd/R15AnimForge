"""Unit tests for Roblox validation rules V02-V12, pack manifest V10, and explicit-only fixer."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from animforge.config import load_profile
from animforge.constants import MAPPED_R15_BONE_NAMES
from animforge.fixer import (
    FixConfigurationError,
    FixFailedError,
    fix_clip,
    next_fixed_output_dir,
    resolve_apply_categories,
)
from animforge.math_retarget import euler_to_quat, q_identity
from animforge.pack_manifest import build_default_pack_manifest, validate_pack_manifest
from animforge.validation import ClipData, infer_clip_is_loop, validate_clip, write_validation_reports


def _make_base_clip(*, n_frames: int = 31, fps: float = 30.0, loop: bool = True, amp_deg: float = 15.0) -> ClipData:
    frames = [float(i + 1) for i in range(n_frames)]
    rots = {b: [q_identity() for _ in range(n_frames)] for b in MAPPED_R15_BONE_NAMES}
    lt_locs = [(0.0, 0.0, 0.0) for _ in range(n_frames)]
    for i in range(n_frames):
        ang = math.radians(amp_deg) * math.sin(2.0 * math.pi * i / (n_frames - 1))
        rots["LowerTorso"][i] = euler_to_quat(0.0, ang, 0.0, "YXZ")
        rots["LeftUpperArm"][i] = euler_to_quat(ang, 0.0, 0.0, "YXZ")
    return ClipData(name="test_clip", fps=fps, frames=frames, rot_tracks=rots, lt_locs=lt_locs, loop=loop)


def test_valid_clip_passes_and_writes_reports(tmp_path: Path) -> None:
    clip = _make_base_clip()
    res = validate_clip(clip)
    assert res.status == "PASS", [f.message for f in res.findings]
    jout = tmp_path / "validation_report.json"
    hout = tmp_path / "validation_report.html"
    payload = write_validation_reports([res], jout, hout)
    assert payload["summary"]["PASS"] == 1
    assert jout.exists() and hout.exists()
    assert "PASS" in hout.read_text(encoding="utf-8")


def test_v02_static_face_only_fails() -> None:
    clip = _make_base_clip(amp_deg=0.0)
    res = validate_clip(clip)
    assert res.status == "FAIL"
    assert any(f.rule_id == "V02" for f in res.findings)


def test_v03_v04_fast_whip_fails_and_fixed_by_smoothing_or_stretch() -> None:
    # Fast 150 deg whip of LeftUpperArm in 1 frame (1/30s = 4500 deg/s, tip > 100 studs/s!)
    clip = _make_base_clip(n_frames=31, loop=False, amp_deg=5.0)
    for i in range(10, 31):
        clip.rot_tracks["LeftUpperArm"][i] = euler_to_quat(math.radians(-155.0), 0.0, 0.0, "YXZ")
    res = validate_clip(clip)
    assert res.status == "FAIL"
    assert any(f.rule_id == "V03" for f in res.findings)
    assert any(f.rule_id == "V04" for f in res.findings)

    # Without explicit categories -> FixConfigurationError
    with pytest.raises(FixConfigurationError):
        resolve_apply_categories([])

    # Applying only floor_lift should NOT fix speed -> FixFailedError (does not fake pass!)
    with pytest.raises(FixFailedError):
        fix_clip(clip, ["floor_lift"])

    # Applying smoothing + time_stretch fixes V03 safely <= 45 studs/s without mutating input clip
    orig_dur = clip.duration_sec
    fr = fix_clip(clip, ["smoothing", "time_stretch"], max_stretch=3.5)
    assert clip.duration_sec == orig_dur
    assert fr.after.status in ("PASS", "WARN")
    assert fr.after.peak_speed_studs_s <= 45.0


def test_v05_floor_depth_and_v06_translation_fix() -> None:
    clip = _make_base_clip()
    # Drop LowerTorso Y by -0.10m (-0.386 studs -> >0.10 below floor) and add non-LT translation (>0.30 studs)
    clip.lt_locs = [(0.0, -0.12, 0.0) for _ in clip.frames]
    clip.non_lt_locs = {"LeftUpperArm": [(0.15, 0.0, 0.0) for _ in clip.frames]}  # 0.15*3.858 = 0.58 studs

    res = validate_clip(clip)
    ids = {f.rule_id for f in res.findings}
    assert {"V05", "V06"}.issubset(ids)

    fr = fix_clip(clip, ["strip_translation", "floor_lift"])
    assert fr.after.status == "PASS"
    assert fr.after.max_non_lt_trans_studs == 0.0
    assert fr.after.max_foot_depth_studs <= 0.09
    assert clip.non_lt_locs  # original preserved untouched!


def test_v07_v08_v11_v12_rules() -> None:
    # V07 key spacing < 1/70s
    fast = _make_base_clip(fps=100.0)
    assert any(f.rule_id == "V07" for f in validate_clip(fast).findings)

    # V08 joint angle > 180 deg? Quaternion angle from identity is max 180, so test V12 non-unit & NaN
    bad = _make_base_clip()
    bad.rot_tracks["Head"][3] = (2.0, 0.0, 0.0, 0.0)
    assert any(f.rule_id == "V12" for f in validate_clip(bad).findings)

    bad_nan = _make_base_clip()
    bad_nan.lt_locs[2] = (0.0, float("nan"), 0.0)
    assert any(f.rule_id == "V12" for f in validate_clip(bad_nan).findings)

    # V11 loop seam mismatch
    seam = _make_base_clip(loop=True)
    seam.rot_tracks["Head"][-1] = euler_to_quat(0.5, 0.0, 0.0, "YXZ")
    assert any(f.rule_id == "V11" for f in validate_clip(seam).findings)


def test_v10_pack_manifest_catches_duplicates_and_passes_valid() -> None:
    profile = load_profile()
    ids = {
        "Idle1": "100001", "Idle2": "100002", "Walk": "100003", "Run": "100004",
        "Jump": "100005", "Fall": "100006", "Climb": "100007", "Swim": "100008", "SwimIdle": "100009",
    }
    manifest = build_default_pack_manifest(profile, ids)
    ok = validate_pack_manifest(manifest)
    assert ok.valid, ok.errors
    assert len(ok.warnings) == 3  # Climb, Swim, SwimIdle flagged as unconfirmed source!

    # Inject duplicate FallAnim like user's session error (3 FallAnims!)
    manifest["animations"]["FallAnimation"]["fall"].append({"name": "FallAnim", "animation_id": "100099"})
    manifest["animations"]["FallAnimation"]["fall"].append({"name": "FallAnim", "animation_id": "100100"})
    bad = validate_pack_manifest(manifest)
    assert not bad.valid
    assert any("duplicate children" in e.lower() for e in bad.errors)


def test_infer_loop_and_stretch_preserves_loop_seam() -> None:
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_atk_3h") is False
    assert infer_clip_is_loop("Rohan_R15_Jump") is False
    assert infer_clip_is_loop("Rohan_R15_Fall") is True
    assert infer_clip_is_loop("Rohan_R15_4rhn01_bas_walk_front_fixed") is True
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_bas_dash_front") is True
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_bas_nut_st") is False
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_bas_nut_crouch_fixed") is False
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_bas_nut_turn_fixed") is False
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_bas_nut") is True
    assert infer_clip_is_loop("7val01_R15_7val01_bas_r_nut") is True
    assert infer_clip_is_loop("4ykk01_R15_4ykk01_dmg_spin_lp_fixed") is True

    # Fast looping whip stretched uniformly preserves exact loop seam (zero V11 seam regression)
    clip = _make_base_clip(n_frames=16, loop=True, amp_deg=90.0)
    fr = fix_clip(clip, ["smoothing", "time_stretch"])
    assert fr.after.status == "PASS", [f.message for f in fr.after.findings]
    assert not any(f.rule_id == "V11" for f in fr.after.findings)
    assert not any(f.severity == "FAIL" for f in fr.after.findings)


def test_auto_fix_clears_v04_v09_v11_warns_to_pass(tmp_path: Path) -> None:
    # 1) V04 angular velocity spike + V11 loop seam mismatch -> PASS after smoothing
    clip = _make_base_clip(n_frames=24, loop=True, amp_deg=15.0)
    clip.rot_tracks["Head"][10] = euler_to_quat(math.radians(110.0), 0.0, 0.0, "YXZ")
    clip.rot_tracks["UpperTorso"][-1] = euler_to_quat(0.0, math.radians(25.0), 0.0, "YXZ")
    clip.lt_locs[-1] = (0.04, 0.0, 0.0)
    before = validate_clip(clip)
    before_ids = {f.rule_id for f in before.findings}
    assert "V04" in before_ids
    assert "V11" in before_ids

    fr = fix_clip(clip, ["strip_translation", "floor_lift", "clamps", "smoothing"])
    assert fr.after.status == "PASS", [f.message for f in fr.after.findings]
    assert fr.after.peak_ang_vel_deg_s <= 1750.0

    # 2) V09 lowest body point (hand reaching below Y=-3.1 studs) -> PASS after floor_lift
    low_clip = _make_base_clip(n_frames=20, loop=False, amp_deg=10.0)
    low_clip.lt_locs = [(0.0, -0.35, 0.0) for _ in low_clip.frames]
    before_low = validate_clip(low_clip)
    assert any(f.rule_id == "V09" for f in before_low.findings)
    fr_low = fix_clip(low_clip, ["floor_lift"])
    assert fr_low.after.status == "PASS", [f.message for f in fr_low.after.findings]
    assert fr_low.after.lowest_body_y_studs >= -3.1

    # 3) Clean non-chaining _fixed folder naming
    assert next_fixed_output_dir(tmp_path / "Anims").name == "Anims_fixed"
    assert next_fixed_output_dir(tmp_path / "Anims_fixed").name == "Anims_fixed_2"
    assert next_fixed_output_dir(tmp_path / "Anims_fixed_2").name == "Anims_fixed_3"
