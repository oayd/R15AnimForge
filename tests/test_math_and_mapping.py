"""Unit tests for quaternion/Euler math, delta retargeting, and strict bone mapping."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from animforge.config import load_profile, load_r15_reference
from animforge.constants import MAPPED_R15_BONE_NAMES
from animforge.math_retarget import (
    BoneMappingError,
    blender_quat_to_roblox_quat,
    build_rest_offset_quats,
    enforce_hemisphere_continuity,
    euler_to_quat,
    map_skeleton_bones,
    normalize_bone_name,
    q_angle_rad,
    q_from_axis_angle,
    q_identity,
    q_mul,
    quat_to_euler,
    retarget_frame_poses,
    unwrap_euler_sequence,
)


def test_euler_roundtrip_all_orders() -> None:
    angles = (0.31, -0.57, 0.22)
    for order in ("XYZ", "YXZ", "ZXY"):
        q = euler_to_quat(*angles, order=order)
        back = quat_to_euler(q, order=order)
        for a, b in zip(angles, back):
            assert abs(a - b) < 1e-6


def test_hemisphere_continuity_and_euler_unwrap() -> None:
    q0 = q_from_axis_angle((0.0, 1.0, 0.0), 0.1)
    q1 = tuple(-x for x in q_from_axis_angle((0.0, 1.0, 0.0), 0.2))
    seq = enforce_hemisphere_continuity([q0, q1])
    assert sum(a * b for a, b in zip(seq[0], seq[1])) > 0.0

    e_seq = unwrap_euler_sequence([(3.10, 0.0, 0.0), (-3.12, 0.0, 0.0)])
    assert abs(e_seq[1][0] - e_seq[0][0]) < 0.1


def test_synthetic_delta_retarget_identity_and_pitch() -> None:
    profile = load_profile()
    r15_ref = load_r15_reference()
    parent_map = {b["name"]: b["parent"] for b in r15_ref["bones"] if b["parent"] is not None}
    offsets = build_rest_offset_quats(profile)

    # Give CC2 bones arbitrary non-identity rest rotations (e.g. (-180,-90,0), (-92,86,88))
    src_rest = {b: euler_to_quat(0.4 * (i + 1), -0.3 * (i % 5), 0.7 * (i % 3), "YXZ") for i, b in enumerate(MAPPED_R15_BONE_NAMES)}
    # Rotate UpperTorso by +30 deg forward pitch in CC2 world space around Left/Right X (+1,0,0)
    q_pitch = q_from_axis_angle((1.0, 0.0, 0.0), math.radians(30.0))
    src_pose = dict(src_rest)
    src_pose["UpperTorso"] = q_mul(q_pitch, src_rest["UpperTorso"])

    locals_q = retarget_frame_poses(src_pose, src_rest, offsets, parent_map)
    # UpperTorso local rotation must equal +30 deg around +X; Head inherits parent change only, so local Head compensates -30 deg
    assert math.degrees(q_angle_rad(locals_q["UpperTorso"], q_pitch)) < 1e-4
    # LowerTorso had zero delta -> identity
    assert math.degrees(q_angle_rad(locals_q["LowerTorso"], q_identity())) < 1e-4
    # Roblox mapping keeps +X pitch magnitude
    q_rbx_ut = blender_quat_to_roblox_quat(locals_q["UpperTorso"])
    assert abs(math.degrees(quat_to_euler(q_rbx_ut, "ZXY")[0]) - (-30.0)) < 1e-3


def test_normalize_and_bone_mapping_ambiguity_and_missing(tmp_path: Path) -> None:
    profile = load_profile()
    assert normalize_bone_name("4rhn01t0 l_upperarm", "4rhn01") == "l upperarm"

    # Full valid set (including distractor finger/helper bones that substring matching used to trip over!)
    valid_names = [
        "4rhn01t0 pelvis", "4rhn01t0 spine", "4rhn01t0 spine2", "4rhn01t0 head",
        "4rhn01t0 l upperarm", "4rhn01t0 l forearm", "4rhn01t0 l hand",
        "4rhn01t0 l hand thumb1", "4rhn01t0 l hand index1",
        "4rhn01t0 r upperarm", "4rhn01t0 r forearm", "4rhn01t0 r hand",
        "4rhn01t0 r hand pinky3",
        "4rhn01t0 l thigh", "4rhn01t0 l thigh sup01", "4rhn01t0 l calf", "4rhn01t0 l foot",
        "4rhn01t0 r thigh", "4rhn01t0 r calf", "4rhn01t0 r foot",
    ]
    report_path = tmp_path / "bone_map_report.json"
    mapped = map_skeleton_bones(valid_names, profile, report_out_path=report_path)
    assert mapped["LeftHand"] == "4rhn01t0 l hand"
    assert mapped["LeftUpperLeg"] == "4rhn01t0 l thigh"

    # Ambiguous duplicate name triggers loud BoneMappingError
    with pytest.raises(BoneMappingError, match="ambiguous"):
        map_skeleton_bones(valid_names + ["4rhn01t0 l_hand"], profile, report_out_path=report_path)
    assert report_path.exists()

    # Missing bone triggers loud BoneMappingError
    with pytest.raises(BoneMappingError, match="unmapped"):
        map_skeleton_bones([n for n in valid_names if "spine2" not in n], profile, report_out_path=report_path)
