"""Unit tests for pure-function R15 cleanup operators."""

from __future__ import annotations

import math

from animforge.cleanup import (
    apply_cleanup_pipeline,
    apply_elbow_cap,
    apply_feet_level,
    apply_head_forward_yaw,
    apply_upper_torso_align,
    apply_wrist_clamp,
)
from animforge.constants import MAPPED_R15_BONE_NAMES
from animforge.math_retarget import euler_to_quat, q_angle_rad, q_identity, q_mul, quat_to_euler


def _identity_pose() -> dict[str, tuple[float, float, float, float]]:
    return {b: q_identity() for b in MAPPED_R15_BONE_NAMES}


def test_wrist_clamp_and_elbow_cap() -> None:
    pose = _identity_pose()
    pose["RightHand"] = euler_to_quat(math.radians(40.0), math.radians(35.0), math.radians(-25.0), "YXZ")
    pose["LeftLowerArm"] = euler_to_quat(math.radians(-145.0), math.radians(20.0), 0.0, "YXZ")

    p1 = apply_wrist_clamp(pose, right_xz_deg=3.0, right_y_deg=10.0)
    rx, ry, rz = [math.degrees(v) for v in quat_to_euler(p1["RightHand"], "YXZ")]
    assert abs(rx - 3.0) < 1e-4 and abs(ry - 10.0) < 1e-4 and abs(rz - (-3.0)) < 1e-4

    p2 = apply_elbow_cap(p1, min_x_deg=-105.0, max_x_deg=10.0, yz_deg=5.0)
    expected_capped = euler_to_quat(math.radians(-105.0), math.radians(5.0), 0.0, "YXZ")
    assert math.degrees(q_angle_rad(p2["LeftLowerArm"], expected_capped)) < 1e-4


def test_feet_level_zeroes_world_pitch_roll() -> None:
    pose = _identity_pose()
    pose["LowerTorso"] = euler_to_quat(math.radians(15.0), math.radians(-45.0), math.radians(8.0), "YXZ")
    pose["LeftUpperLeg"] = euler_to_quat(math.radians(-25.0), math.radians(10.0), 0.0, "YXZ")
    pose["LeftLowerLeg"] = euler_to_quat(math.radians(35.0), 0.0, 0.0, "YXZ")

    leveled = apply_feet_level(pose)
    foot_world = q_mul(leveled["LowerTorso"], q_mul(leveled["LeftUpperLeg"], q_mul(leveled["LeftLowerLeg"], leveled["LeftFoot"])))
    wx, wy, wz = [math.degrees(v) for v in quat_to_euler(foot_world, "YXZ")]
    assert abs(wx) < 1e-4 and abs(wz) < 1e-4


def test_head_forward_yaw_sign() -> None:
    pose = _identity_pose()
    out = apply_head_forward_yaw(pose, yaw_deg=36.0)
    _, hy, _ = [math.degrees(v) for v in quat_to_euler(out["Head"], "YXZ")]
    assert abs(hy - 36.0) < 1e-4


def test_upper_torso_align_preserves_world_children_and_default_off() -> None:
    pose = _identity_pose()
    pose["LowerTorso"] = euler_to_quat(math.radians(10.0), math.radians(-30.0), 0.0, "YXZ")
    pose["UpperTorso"] = euler_to_quat(math.radians(-8.0), math.radians(-20.0), math.radians(12.0), "YXZ")
    pose["Head"] = euler_to_quat(math.radians(5.0), math.radians(40.0), math.radians(-3.0), "YXZ")
    pose["LeftUpperArm"] = euler_to_quat(math.radians(-15.0), math.radians(25.0), math.radians(10.0), "YXZ")

    # Pipeline with empty toggles leaves pose unchanged
    unchanged, log = apply_cleanup_pipeline(pose, {})
    assert log == []
    assert math.degrees(q_angle_rad(unchanged["UpperTorso"], pose["UpperTorso"])) < 1e-6

    # Aligned keeps world orientation of Head and arms intact!
    aligned = apply_upper_torso_align(pose)
    old_head_world = q_mul(pose["LowerTorso"], q_mul(pose["UpperTorso"], pose["Head"]))
    new_head_world = q_mul(aligned["LowerTorso"], q_mul(aligned["UpperTorso"], aligned["Head"]))
    assert math.degrees(q_angle_rad(old_head_world, new_head_world)) < 1e-4
