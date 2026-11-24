"""Pure-Python / NumPy quaternion, Euler, retargeting math, and strict bone mapping."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from .config import CharacterProfile
from .constants import MAPPED_R15_BONE_NAMES, R15_BONE_NAMES

Quat = tuple[float, float, float, float]  # (w, x, y, z)
Vec3 = tuple[float, float, float]


class BoneMappingError(RuntimeError):
    """Raised when CC2 skeleton bone mapping is incomplete or ambiguous."""


def q_identity() -> Quat:
    return (1.0, 0.0, 0.0, 0.0)


def q_norm(q: Sequence[float]) -> Quat:
    w, x, y, z = float(q[0]), float(q[1]), float(q[2]), float(q[3])
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n <= 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    inv = 1.0 / n
    return (w * inv, x * inv, y * inv, z * inv)


def q_inv(q: Sequence[float]) -> Quat:
    w, x, y, z = q_norm(q)
    return (w, -x, -y, -z)


def q_mul(a: Sequence[float], b: Sequence[float]) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def q_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])


def q_neg(q: Sequence[float]) -> Quat:
    return (-float(q[0]), -float(q[1]), -float(q[2]), -float(q[3]))


def q_from_axis_angle(axis: Sequence[float], angle_rad: float) -> Quat:
    ax, ay, az = float(axis[0]), float(axis[1]), float(axis[2])
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n <= 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    half = 0.5 * angle_rad
    s = math.sin(half) / n
    return q_norm((math.cos(half), ax * s, ay * s, az * s))


def q_angle_rad(a: Sequence[float], b: Sequence[float]) -> float:
    d = abs(q_dot(q_norm(a), q_norm(b)))
    d = min(1.0, max(-1.0, d))
    return 2.0 * math.acos(d)


def q_slerp(a: Sequence[float], b: Sequence[float], t: float) -> Quat:
    qa = q_norm(a)
    qb = q_norm(b)
    dot = q_dot(qa, qb)
    if dot < 0.0:
        qb = q_neg(qb)
        dot = -dot
    if dot > 0.999995:
        res = (
            qa[0] + t * (qb[0] - qa[0]),
            qa[1] + t * (qb[1] - qa[1]),
            qa[2] + t * (qb[2] - qa[2]),
            qa[3] + t * (qb[3] - qa[3]),
        )
        return q_norm(res)
    theta_0 = math.acos(min(1.0, max(-1.0, dot)))
    sin_0 = math.sin(theta_0)
    theta = theta_0 * t
    sin_t = math.sin(theta)
    s0 = math.cos(theta) - dot * sin_t / sin_0
    s1 = sin_t / sin_0
    return q_norm((
        s0 * qa[0] + s1 * qb[0],
        s0 * qa[1] + s1 * qb[1],
        s0 * qa[2] + s1 * qb[2],
        s0 * qa[3] + s1 * qb[3],
    ))


def q_rotate_vec(q: Sequence[float], v: Sequence[float]) -> Vec3:
    qv = (0.0, float(v[0]), float(v[1]), float(v[2]))
    r = q_mul(q_mul(q, qv), q_inv(q))
    return (r[1], r[2], r[3])


def q_to_matrix3(q: Sequence[float]) -> np.ndarray:
    w, x, y, z = q_norm(q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array([
        [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
        [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
        [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
    ], dtype=np.float64)


def matrix3_to_quat(m: np.ndarray) -> Quat:
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return q_norm((0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s))
    if m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        return q_norm(((m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s))
    if m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        return q_norm(((m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s))
    s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
    return q_norm(((m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s))


def euler_to_quat(rx: float, ry: float, rz: float, order: str = "YXZ") -> Quat:
    """Convert Euler angles (rad) using Blender's axis-order convention (e.g. 'YXZ' -> Rz @ Rx @ Ry)."""
    axis_q = {
        "X": q_from_axis_angle((1.0, 0.0, 0.0), rx),
        "Y": q_from_axis_angle((0.0, 1.0, 0.0), ry),
        "Z": q_from_axis_angle((0.0, 0.0, 1.0), rz),
    }
    if len(order) != 3 or set(order) != {"X", "Y", "Z"}:
        raise ValueError(f"Unsupported Euler order: {order}")
    # Blender Euler 'ABC' means intrinsic C -> B -> A, i.e., q = q_C @ q_B @ q_A.
    return q_norm(q_mul(axis_q[order[2]], q_mul(axis_q[order[1]], axis_q[order[0]])))


def quat_to_euler(q: Sequence[float], order: str = "YXZ") -> Vec3:
    """Extract Euler angles (rx, ry, rz) in radians matching Blender Quaternion.to_euler(order)."""
    m = q_to_matrix3(q)
    eps = 1.0 - 1e-6

    if order == "XYZ":
        # M = Rz(z) * Ry(y) * Rx(x): m[2,0] = -sin(y)
        sy = max(-1.0, min(1.0, -m[2, 0]))
        y = math.asin(sy)
        if abs(sy) < eps:
            x = math.atan2(m[2, 1], m[2, 2])
            z = math.atan2(m[1, 0], m[0, 0])
        else:
            x = math.atan2(-m[1, 2], m[1, 1])
            z = 0.0
        return (x, y, z)

    if order == "YXZ":
        # M = Rz(z) * Rx(x) * Ry(y): m[2,1] = sin(x)
        sx = max(-1.0, min(1.0, m[2, 1]))
        x = math.asin(sx)
        if abs(sx) < eps:
            y = math.atan2(-m[2, 0], m[2, 2])
            z = math.atan2(-m[0, 1], m[1, 1])
        else:
            y = math.atan2(m[0, 2], m[0, 0])
            z = 0.0
        return (x, y, z)

    if order == "ZXY":
        # M = Ry(y) * Rx(x) * Rz(z): m[1,2] = -sin(x) (matches Roblox CFrame:ToEulerAnglesYXZ())
        sx = max(-1.0, min(1.0, -m[1, 2]))
        x = math.asin(sx)
        if abs(sx) < eps:
            y = math.atan2(m[0, 2], m[2, 2])
            z = math.atan2(m[1, 0], m[1, 1])
        else:
            y = math.atan2(-m[2, 0], m[0, 0])
            z = 0.0
        return (x, y, z)

    raise ValueError(f"Unsupported Euler order: {order}")


def blender_quat_to_roblox_quat(q_bl: Sequence[float]) -> Quat:
    """Map Blender R15_Rig local bone quaternion to Roblox Pose.CFrame quaternion."""
    w, x, y, z = q_norm(q_bl)
    return q_norm((w, -x, y, -z))


def roblox_quat_to_blender_quat(q_rbx: Sequence[float]) -> Quat:
    """Inverse of blender_quat_to_roblox_quat."""
    w, x, y, z = q_norm(q_rbx)
    return q_norm((w, -x, y, -z))


def enforce_hemisphere_continuity(quats: Sequence[Sequence[float]]) -> list[Quat]:
    """Ensure consecutive quaternions have positive dot product to prevent 360-deg SLERP flips."""
    out: list[Quat] = []
    prev: Quat | None = None
    for raw in quats:
        q = q_norm(raw)
        if prev is not None and q_dot(prev, q) < 0.0:
            q = q_neg(q)
        out.append(q)
        prev = q
    return out


def unwrap_euler_sequence(angles_list: Sequence[Vec3]) -> list[Vec3]:
    """Continuously unwrap Euler (x, y, z) sequences by 2*pi multiples."""
    if not angles_list:
        return []
    two_pi = 2.0 * math.pi
    out: list[Vec3] = [(float(angles_list[0][0]), float(angles_list[0][1]), float(angles_list[0][2]))]
    for cur in angles_list[1:]:
        px, py, pz = out[-1]
        cx = float(cur[0]) + round((px - float(cur[0])) / two_pi) * two_pi
        cy = float(cur[1]) + round((py - float(cur[1])) / two_pi) * two_pi
        cz = float(cur[2]) + round((pz - float(cur[2])) / two_pi) * two_pi
        out.append((cx, cy, cz))
    return out


def retarget_frame_poses(
    src_pose_world_quats: dict[str, Quat],
    src_rest_world_quats: dict[str, Quat],
    rest_offset_world_quats: dict[str, Quat],
    parent_map: dict[str, str],
    ordered_bones: Sequence[str] = MAPPED_R15_BONE_NAMES,
    r15_root_quat: Quat = (1.0, 0.0, 0.0, 0.0),
) -> dict[str, Quat]:
    """Compute parent-relative local R15 quaternions via world-space delta retargeting."""
    world_target: dict[str, Quat] = {"HumanoidRootPart": q_norm(r15_root_quat)}
    for r15_b in ordered_bones:
        q_pose = src_pose_world_quats[r15_b]
        q_rest_inv = q_inv(src_rest_world_quats[r15_b])
        q_delta_world = q_mul(q_pose, q_rest_inv)
        offset = rest_offset_world_quats.get(r15_b, q_identity())
        world_target[r15_b] = q_norm(q_mul(q_delta_world, q_mul(offset, world_target["HumanoidRootPart"])))

    locals_out: dict[str, Quat] = {}
    for r15_b in ordered_bones:
        p_name = parent_map[r15_b]
        locals_out[r15_b] = q_norm(q_mul(q_inv(world_target[p_name]), world_target[r15_b]))
    return locals_out


def normalize_bone_name(name: str, charcode: str = "") -> str:
    """Normalize a CC2 bone name by stripping character prefix and unifying separators."""
    s = name.strip().lower().replace("_", " ")
    if charcode and charcode.lower() not in ("auto", "*"):
        cc = charcode.lower()
        # Strip prefixes like '4rhn01t0 ', '4rhn01t0_' or '4rhn01 '
        s = re.sub(rf"^{re.escape(cc)}(?:t\d+)?\s+", "", s)
    # Also strip generic CC2 prefixes (e.g. '4jtr01t0 ', '4dio01 ') so universal mode works on any character
    s = re.sub(r"^[a-z0-9]{4,}t\d+\s+", "", s)
    s = re.sub(r"^\d[a-z0-9]{3,}\s+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def map_skeleton_bones(
    source_bone_names: Iterable[str],
    profile: CharacterProfile,
    *,
    report_out_path: str | Path | None = None,
) -> dict[str, str]:
    """Deterministically map the 15 target R15 joints to CC2 bone names via exact normalized matches."""
    src_list = list(source_bone_names)
    norm_to_originals: dict[str, list[str]] = {}
    for orig in src_list:
        nrm = normalize_bone_name(orig, profile.charcode)
        norm_to_originals.setdefault(nrm, []).append(orig)

    mapped: dict[str, str] = {}
    issues: list[dict[str, Any]] = []

    for r15_bone in MAPPED_R15_BONE_NAMES:
        candidates_cfg = [normalize_bone_name(c, profile.charcode) for c in profile.bone_map_normalized.get(r15_bone, [])]
        matched_originals: list[str] = []
        matched_key: str | None = None
        for key in candidates_cfg:
            if key in norm_to_originals:
                matched_originals = norm_to_originals[key]
                matched_key = key
                break

        if len(matched_originals) == 1:
            mapped[r15_bone] = matched_originals[0]
        elif len(matched_originals) > 1:
            issues.append({"r15_bone": r15_bone, "status": "ambiguous", "key": matched_key, "matches": matched_originals})
        else:
            # Check substring fallback only to give helpful diagnostics in the failure report
            near = [o for o in src_list if any(k in normalize_bone_name(o, profile.charcode) for k in candidates_cfg)]
            issues.append({"r15_bone": r15_bone, "status": "unmapped", "candidates_tried": candidates_cfg, "near_matches": near})

    report = {
        "charcode": profile.charcode,
        "source_bone_count": len(src_list),
        "mapped": mapped,
        "issues": issues,
    }
    if report_out_path is not None:
        p = Path(report_out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if issues:
        summary = "; ".join(f"{it['r15_bone']}={it['status']}" for it in issues)
        raise BoneMappingError(f"Skeleton bone mapping failed for '{profile.charcode}': {summary}")

    return mapped


def build_rest_offset_quats(profile: CharacterProfile) -> dict[str, Quat]:
    """Convert profile rest_pose_correction_deg into world quaternions (YXZ Euler in degrees)."""
    offsets: dict[str, Quat] = {}
    for r15_b in MAPPED_R15_BONE_NAMES:
        deg = profile.rest_pose_correction_deg.get(r15_b)
        if deg:
            offsets[r15_b] = euler_to_quat(
                math.radians(deg[0]),
                math.radians(deg[1]),
                math.radians(deg[2]),
                order="YXZ",
            )
        else:
            offsets[r15_b] = q_identity()
    return offsets
