"""Real-data integration tests (run ONLY when ANIMFORGE_TESTDATA points to user XFBIN folder)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from animforge.blender_runner import (
    export_clips_to_fbx,
    extract_clips_from_fbx,
    run_build_procedural_job,
    run_convert_job,
)
from animforge.config import load_profile, load_r15_reference
from animforge.constants import MAPPED_R15_BONE_NAMES
from animforge.fixer import fix_clip, write_fix_log
from animforge.math_retarget import (
    blender_quat_to_roblox_quat,
    euler_to_quat,
    q_angle_rad,
    q_mul,
    q_rotate_vec,
    quat_to_euler,
)
from animforge.validation import ClipData, validate_clip, write_validation_reports

TESTDATA_ENV = os.environ.get("ANIMFORGE_TESTDATA", "").strip()
pytestmark = pytest.mark.skipif(not TESTDATA_ENV, reason="ANIMFORGE_TESTDATA not set; skipping real-asset integration tests")


def _clip_dict_to_clip(d: dict) -> ClipData:
    name = str(d["name"])
    low = name.lower()
    is_loop = False if ("jump" in low and "lp" not in low) else True
    return ClipData(
        name=name,
        fps=float(d.get("fps", 30.0)),
        frames=[float(x) for x in d["frames"]],
        rot_tracks={k: [tuple(q) for q in v] for k, v in d["rot_tracks"].items()},
        lt_locs=[tuple(v) for v in d["lt_locs"]],
        loop=is_loop,
        source_path=d.get("source_path"),
    )


def _assert_quat_matches_rbx_yxz(q_bl: tuple[float, float, float, float], target_deg: tuple[float, float, float], tol_deg: float = 1.0) -> None:
    qr = blender_quat_to_roblox_quat(q_bl)
    qt = euler_to_quat(math.radians(target_deg[0]), math.radians(target_deg[1]), math.radians(target_deg[2]), order="ZXY")
    err = math.degrees(q_angle_rad(qr, qt))
    assert err <= tol_deg, f"Expected {target_deg}, got {[round(math.degrees(v), 1) for v in quat_to_euler(qr, 'ZXY')]} (err={err:.2f} deg)"


def test_real_rohan_full_batch_builders_and_validation(tmp_path: Path) -> None:
    data_dir = Path(TESTDATA_ENV)
    profile = load_profile()
    out_dir = tmp_path / "exported_fbx"

    # 1. Plain batch convert (must export all 178 actions, including face-only clips per EDIT C/F)
    rep = run_convert_job(data_dir, out_dir, profile, workers=2, use_cache=False)
    assert rep["exported_count"] == 178, f"Expected 178 exported FBXs, got {rep['exported_count']}"
    assert rep["verified_for_roblox_upload"] is False
    assert len(rep["face_only_informational"]) >= 2

    # Cross-check exact filenames against existing Exported_R15_FBX if sibling exists
    ref_fbx_dir = data_dir.parent / "Exported_R15_FBX"
    if ref_fbx_dir.is_dir():
        expected_178 = sorted(
            p.name for p in ref_fbx_dir.glob("Rohan_R15_4rhn01_*.fbx")
            if p.name not in ("Rohan_R15_4rhn01_bas_nut_st_idle_loop.fbx", "Rohan_R15_4rhn01_alt_idle_look.fbx")
        )
        actual_178 = sorted(p.name for p in out_dir.glob("*.fbx"))
        assert actual_178 == expected_178

    # 2. Procedural builders (idle loop, alt idle look, jump, fall)
    proc_dir = tmp_path / "procedural_fbx"
    run_build_procedural_job(data_dir, proc_dir, profile, upper_torso_align=False, smooth_jump_launch=True)

    # 3. Extract key clips via Blender and check Section 9 numerical invariants
    key_files = [
        out_dir / "Rohan_R15_4rhn01_atk_2h.fbx",
        out_dir / "Rohan_R15_4rhn01_bas_nut.fbx",
        out_dir / "Rohan_R15_4rhn01_bas_nut_st.fbx",
        out_dir / "Rohan_R15_4rhn01_bas_walk_front.fbx",
        out_dir / "Rohan_R15_4rhn01_bas_dash_front.fbx",
        proc_dir / "Rohan_R15_4rhn01_bas_nut_st_idle_loop.fbx",
        proc_dir / "Rohan_R15_4rhn01_alt_idle_look.fbx",
        proc_dir / "Rohan_R15_Jump.fbx",
        proc_dir / "Rohan_R15_Fall.fbx",
    ]
    extracted = {d["name"]: _clip_dict_to_clip(d) for d in extract_clips_from_fbx(key_files)}

    # atk_2h checks (frame range 1.0..30.0, all 6 joints within 1 deg)
    atk = extracted["Rohan_R15_4rhn01_atk_2h"]
    assert (atk.frames[0], atk.frames[-1]) == (1.0, 30.0)
    _assert_quat_matches_rbx_yxz(atk.rot_tracks["LowerTorso"][0], (19.8, -10.6, 8.1))
    _assert_quat_matches_rbx_yxz(atk.rot_tracks["UpperTorso"][0], (-62.9, -25.9, 19.4))
    _assert_quat_matches_rbx_yxz(atk.rot_tracks["LeftLowerArm"][0], (13.1, 0.0, 0.0))
    _assert_quat_matches_rbx_yxz(atk.rot_tracks["RightLowerArm"][0], (67.4, 0.0, 0.0))
    _assert_quat_matches_rbx_yxz(atk.rot_tracks["LeftLowerLeg"][0], (-124.4, -0.3, 0.4))
    _assert_quat_matches_rbx_yxz(atk.rot_tracks["RightLowerLeg"][0], (-120.4, 0.3, -0.4))

    # bas_nut frame 1 checks
    nut = extracted["Rohan_R15_4rhn01_bas_nut"]
    _assert_quat_matches_rbx_yxz(nut.rot_tracks["LeftLowerArm"][0], (130.6, -0.2, -0.5))
    _assert_quat_matches_rbx_yxz(nut.rot_tracks["RightLowerArm"][0], (89.6, 0.0, 0.0))
    _assert_quat_matches_rbx_yxz(nut.rot_tracks["LeftLowerLeg"][0], (-56.1, 0.2, -0.2))
    _assert_quat_matches_rbx_yxz(nut.rot_tracks["RightLowerLeg"][0], (-11.8, 0.0, 0.0))

    # bas_nut_st frame 1 -> procedural idle frame 1 checks (pre-alignment torso & elbows)
    idle = extracted["Rohan_R15_4rhn01_bas_nut_st_idle_loop"]
    assert len(idle.frames) == 91
    _assert_quat_matches_rbx_yxz(idle.rot_tracks["LowerTorso"][0], (11.9, -46.9, 0.8))
    _assert_quat_matches_rbx_yxz(idle.rot_tracks["UpperTorso"][0], (-6.6, -15.6, 2.0))
    _assert_quat_matches_rbx_yxz(idle.rot_tracks["LeftLowerArm"][0], (38.8, 0.0, 0.0))
    _assert_quat_matches_rbx_yxz(idle.rot_tracks["RightLowerArm"][0], (80.4, 0.0, 0.0))
    for b in MAPPED_R15_BONE_NAMES:
        assert math.degrees(q_angle_rad(idle.rot_tracks[b][0], idle.rot_tracks[b][-1])) < 0.25

    # Upright check (+Y up, -Z forward in Roblox space: LowerTorso local +Y points generally +Y, not sideways +-X)
    up_vec = q_rotate_vec(blender_quat_to_roblox_quat(idle.rot_tracks["LowerTorso"][0]), (0.0, 1.0, 0.0))
    assert up_vec[1] > 0.85

    # Jump f36 == Fall f1; Fall f1 == Fall f36
    jump = extracted["Rohan_R15_Jump"]
    fall = extracted["Rohan_R15_Fall"]
    assert len(jump.frames) == 36 and len(fall.frames) == 36
    for b in MAPPED_R15_BONE_NAMES:
        assert math.degrees(q_angle_rad(jump.rot_tracks[b][-1], fall.rot_tracks[b][0])) < 0.5
        assert math.degrees(q_angle_rad(fall.rot_tracks[b][0], fall.rot_tracks[b][-1])) < 0.5

    # Opt-in validation & fix check on Run (bas_dash_front), Walk, Jump, Idle, Fall
    val_results = [validate_clip(extracted[k]) for k in (
        "Rohan_R15_4rhn01_bas_walk_front",
        "Rohan_R15_4rhn01_bas_dash_front",
        "Rohan_R15_4rhn01_bas_nut_st_idle_loop",
        "Rohan_R15_4rhn01_alt_idle_look",
        "Rohan_R15_Jump",
        "Rohan_R15_Fall",
    )]
    rep_dir = tmp_path / "reports"
    write_validation_reports(val_results, rep_dir / "validation_report.json", rep_dir / "validation_report.html")
    assert (rep_dir / "validation_report.html").is_file()

    # Run (bas_dash_front, 0.5s) omits floor_lift -> refuses rather than shipping invalid clip
    run_clip = extracted["Rohan_R15_4rhn01_bas_dash_front"]
    with pytest.raises(Exception):
        fix_clip(run_clip, ["smoothing", "time_stretch"], profile=profile)

    # Explicit floor_lift + smoothing + time_stretch fixes Run and Jump cleanly
    fixed_run = fix_clip(run_clip, ["floor_lift", "smoothing", "time_stretch"], profile=profile)
    assert fixed_run.after.peak_speed_studs_s <= 45.0
    assert fixed_run.after.status in ("PASS", "WARN")

    fixed_jump = fix_clip(jump, ["floor_lift", "clamps", "smoothing", "time_stretch"], profile=profile)
    assert fixed_jump.after.peak_speed_studs_s <= 45.0
    assert fixed_jump.after.status in ("PASS", "WARN")
