"""Pure-function R15 pose cleanup operators (defaults OFF for batch convert)."""

from __future__ import annotations

import math
from typing import Any

from .math_retarget import Quat, euler_to_quat, q_from_axis_angle, q_inv, q_mul, q_norm, quat_to_euler


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def clamp_euler_yxz(
    q: Quat,
    *,
    min_x: float = -180.0,
    max_x: float = 180.0,
    min_y: float = -180.0,
    max_y: float = 180.0,
    min_z: float = -180.0,
    max_z: float = 180.0,
) -> Quat:
    """Clamp quaternion rotation componentwise in Blender YXZ Euler degrees (handles |x| > 90 deg elbows)."""
    rx, ry, rz = quat_to_euler(q, order="YXZ")
    # Choose the Euler branch that minimizes twist/roll magnitude so bends > 90 deg (like elbows) don't flip to z=180
    rx2 = _wrap_pi(math.pi - rx)
    ry2 = _wrap_pi(ry + math.pi)
    rz2 = _wrap_pi(rz + math.pi)
    if (abs(ry2) + abs(rz2)) < (abs(ry) + abs(rz)):
        rx, ry, rz = rx2, ry2, rz2
    cx = max(math.radians(min_x), min(math.radians(max_x), rx))
    cy = max(math.radians(min_y), min(math.radians(max_y), ry))
    cz = max(math.radians(min_z), min(math.radians(max_z), rz))
    return euler_to_quat(cx, cy, cz, order="YXZ")


def apply_wrist_clamp(
    pose: dict[str, Quat],
    *,
    right_xz_deg: float = 3.0,
    right_y_deg: float = 10.0,
    left_xz_deg: float = 8.0,
    left_y_deg: float = 10.0,
) -> dict[str, Quat]:
    out = dict(pose)
    if "RightHand" in out:
        out["RightHand"] = clamp_euler_yxz(
            out["RightHand"],
            min_x=-right_xz_deg, max_x=right_xz_deg,
            min_y=-right_y_deg, max_y=right_y_deg,
            min_z=-right_xz_deg, max_z=right_xz_deg,
        )
    if "LeftHand" in out:
        out["LeftHand"] = clamp_euler_yxz(
            out["LeftHand"],
            min_x=-left_xz_deg, max_x=left_xz_deg,
            min_y=-left_y_deg, max_y=left_y_deg,
            min_z=-left_xz_deg, max_z=left_xz_deg,
        )
    return out


def apply_elbow_cap(
    pose: dict[str, Quat],
    *,
    min_x_deg: float = -105.0,
    max_x_deg: float = 10.0,
    yz_deg: float = 5.0,
) -> dict[str, Quat]:
    out = dict(pose)
    for b in ("LeftLowerArm", "RightLowerArm"):
        if b in out:
            out[b] = clamp_euler_yxz(
                out[b],
                min_x=min_x_deg, max_x=max_x_deg,
                min_y=-yz_deg, max_y=yz_deg,
                min_z=-yz_deg, max_z=yz_deg,
            )
    return out


def level_foot_to_ground(lower_leg_world_q: Quat) -> Quat:
    """Compute local foot quaternion flat on ground (0 deg world pitch/roll) aligned to shin yaw."""
    _, leg_yaw, _ = quat_to_euler(lower_leg_world_q, order="YXZ")
    q_foot_world = q_from_axis_angle((0.0, 1.0, 0.0), leg_yaw)
    return q_norm(q_mul(q_inv(lower_leg_world_q), q_foot_world))


def apply_feet_level(pose: dict[str, Quat]) -> dict[str, Quat]:
    out = dict(pose)
    lt_w = out["LowerTorso"]
    if "LeftUpperLeg" in out and "LeftLowerLeg" in out and "LeftFoot" in out:
        l_leg_w = q_mul(lt_w, q_mul(out["LeftUpperLeg"], out["LeftLowerLeg"]))
        out["LeftFoot"] = level_foot_to_ground(l_leg_w)
    if "RightUpperLeg" in out and "RightLowerLeg" in out and "RightFoot" in out:
        r_leg_w = q_mul(lt_w, q_mul(out["RightUpperLeg"], out["RightLowerLeg"]))
        out["RightFoot"] = level_foot_to_ground(r_leg_w)
    return out


def apply_head_forward_yaw(pose: dict[str, Quat], *, yaw_deg: float = 36.0) -> dict[str, Quat]:
    out = dict(pose)
    if "Head" in out:
        q_yaw = q_from_axis_angle((0.0, 1.0, 0.0), math.radians(yaw_deg))
        out["Head"] = q_norm(q_mul(q_yaw, out["Head"]))
    return out


def apply_upper_torso_align(
    pose: dict[str, Quat],
    *,
    roll_scale: float = 0.25,
    yaw_zero: bool = True,
) -> dict[str, Quat]:
    """Align UpperTorso yaw/roll with LowerTorso while inversely compensating Head and UpperArms (default OFF)."""
    out = dict(pose)
    if "UpperTorso" not in out:
        return out
    q_ut_old = out["UpperTorso"]
    rx, ry, rz = quat_to_euler(q_ut_old, order="YXZ")
    new_ry = 0.0 if yaw_zero else ry
    new_rz = rz * roll_scale
    q_ut_new = euler_to_quat(rx, new_ry, new_rz, order="YXZ")
    q_comp = q_norm(q_mul(q_inv(q_ut_new), q_ut_old))
    out["UpperTorso"] = q_ut_new
    for child in ("Head", "LeftUpperArm", "RightUpperArm"):
        if child in out:
            out[child] = q_norm(q_mul(q_comp, out[child]))
    return out


def apply_cleanup_pipeline(
    pose: dict[str, Quat],
    toggles: dict[str, bool],
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Quat], list[str]]:
    """Apply enabled pure cleanup operators in deterministic order and return an audit log."""
    p = params or {}
    cur = dict(pose)
    applied: list[str] = []

    if toggles.get("upper_torso_align", False):
        cur = apply_upper_torso_align(cur)
        applied.append("upper_torso_align")

    if toggles.get("right_hand_clamp", False) or toggles.get("left_hand_clamp", False):
        cur = apply_wrist_clamp(
            cur,
            right_xz_deg=float(p.get("right_hand_xz_deg", 3.0)) if toggles.get("right_hand_clamp", False) else 180.0,
            right_y_deg=float(p.get("right_hand_y_deg", 10.0)) if toggles.get("right_hand_clamp", False) else 180.0,
            left_xz_deg=float(p.get("left_hand_xz_deg", 8.0)) if toggles.get("left_hand_clamp", False) else 180.0,
            left_y_deg=float(p.get("left_hand_y_deg", 10.0)) if toggles.get("left_hand_clamp", False) else 180.0,
        )
        applied.append("wrist_clamp")

    if toggles.get("elbow_cap", False):
        cur = apply_elbow_cap(
            cur,
            min_x_deg=float(p.get("elbow_min_x_deg", -105.0)),
            max_x_deg=float(p.get("elbow_max_x_deg", 10.0)),
            yz_deg=float(p.get("elbow_yz_deg", 5.0)),
        )
        applied.append("elbow_cap")

    if toggles.get("head_forward_yaw", False):
        cur = apply_head_forward_yaw(cur, yaw_deg=float(p.get("idle_head_yaw_offset_deg", 36.0)))
        applied.append("head_forward_yaw")

    if toggles.get("feet_level", False):
        cur = apply_feet_level(cur)
        applied.append("feet_level")

    return cur, applied
