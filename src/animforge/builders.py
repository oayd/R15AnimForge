"""Pure-Python procedural R15 animation builders (Idle loop, Alt idle look, Jump, Fall)."""

from __future__ import annotations

import math
from typing import Any, Sequence

from .cleanup import (
    apply_feet_level,
    apply_head_forward_yaw,
    apply_upper_torso_align,
    apply_wrist_clamp,
    clamp_euler_yxz,
    level_foot_to_ground,
)
from .config import CharacterProfile
from .constants import MAPPED_R15_BONE_NAMES
from .math_retarget import (
    Quat,
    Vec3,
    enforce_hemisphere_continuity,
    q_from_axis_angle,
    q_identity,
    q_inv,
    q_mul,
    q_neg,
    q_norm,
    q_slerp,
    quat_to_euler,
)
from .validation import ClipData


def _pose_at_index(clip: ClipData, idx: int) -> tuple[dict[str, Quat], Vec3]:
    i = max(0, min(len(clip.frames) - 1, idx))
    rots = {b: clip.rot_tracks[b][i] for b in MAPPED_R15_BONE_NAMES}
    return rots, clip.lt_locs[i]


def _interp_pose(p0: dict[str, Quat], p1: dict[str, Quat], alpha: float) -> dict[str, Quat]:
    return {b: q_slerp(p0[b], p1[b], alpha) for b in MAPPED_R15_BONE_NAMES}


def _eval_keyframes(t: float, keys: Sequence[tuple[float, float]]) -> float:
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for i in range(len(keys) - 1):
        t0, v0 = keys[i]
        t1, v1 = keys[i + 1]
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0)
            s = u * u * (3.0 - 2.0 * u)
            return v0 + (v1 - v0) * s
    return keys[-1][1]


def _prepare_idle_base_pose(
    bas_nut_st_clip: ClipData,
    profile: CharacterProfile,
    *,
    apply_upper_torso: bool = False,
) -> tuple[dict[str, Quat], Vec3]:
    """Build relaxed idle base pose from bas_nut_st frame 0 with exact profile clamps."""
    p = profile.cleanup_params
    base_rots, base_loc = _pose_at_index(bas_nut_st_clip, int(profile.procedural_builders.get("idle_loop", {}).get("base_frame", 0)))
    base_rots = apply_wrist_clamp(
        base_rots,
        right_xz_deg=float(p.get("right_hand_xz_deg", 3.0)),
        right_y_deg=float(p.get("right_hand_y_deg", 10.0)),
        left_xz_deg=float(p.get("left_hand_xz_deg", 8.0)),
        left_y_deg=float(p.get("left_hand_y_deg", 10.0)),
    )
    base_rots = apply_head_forward_yaw(base_rots, yaw_deg=float(p.get("idle_head_yaw_offset_deg", 36.0)))
    lul_deg = float(p.get("idle_left_upper_leg_pitch_deg", -10.0))
    base_rots["LeftUpperLeg"] = q_norm(q_mul(base_rots["LeftUpperLeg"], q_from_axis_angle((1.0, 0.0, 0.0), math.radians(lul_deg))))
    if apply_upper_torso:
        base_rots = apply_upper_torso_align(base_rots)
    base_rots = apply_feet_level(base_rots)
    return base_rots, base_loc


def _polish_jump_pose(
    rots: dict[str, Quat],
    profile: CharacterProfile,
    *,
    align_upper_torso: bool = False,
) -> dict[str, Quat]:
    """Polish airborne/crouch source frames cleanly for R15 blocky silhouette."""
    p = profile.cleanup_params
    r = dict(rots)
    target_lt_yaw = float(p.get("target_lt_yaw_deg", -15.0))
    _, lt_yaw, _ = quat_to_euler(r["LowerTorso"], order="YXZ")
    yaw_shift = math.radians(target_lt_yaw) - lt_yaw
    r["LowerTorso"] = q_norm(q_mul(q_from_axis_angle((0.0, 1.0, 0.0), yaw_shift), r["LowerTorso"]))

    if align_upper_torso:
        r = apply_upper_torso_align(r)

    # Counter-rotate head toward world forward and clamp yaw
    r["Head"] = q_norm(q_mul(q_from_axis_angle((0.0, 1.0, 0.0), -yaw_shift), r["Head"]))
    hy_min, hy_max = p.get("head_yaw_clamp_deg", [8.0, 22.0])
    r["Head"] = clamp_euler_yxz(r["Head"], min_y=float(hy_min), max_y=float(hy_max))

    for arm in ("LeftLowerArm", "RightLowerArm"):
        r[arm] = clamp_euler_yxz(
            r[arm],
            min_x=float(p.get("elbow_min_x_deg", -105.0)),
            max_x=float(p.get("elbow_max_x_deg", 10.0)),
            min_y=-float(p.get("elbow_yz_deg", 5.0)),
            max_y=float(p.get("elbow_yz_deg", 5.0)),
            min_z=-float(p.get("elbow_yz_deg", 5.0)),
            max_z=float(p.get("elbow_yz_deg", 5.0)),
        )
    hand_xz = float(p.get("polish_hand_xz_deg", 6.0))
    for h in ("LeftHand", "RightHand"):
        r[h] = clamp_euler_yxz(r[h], min_x=-hand_xz, max_x=hand_xz, min_y=-10.0, max_y=10.0, min_z=-hand_xz, max_z=hand_xz)
    for ft in ("LeftFoot", "RightFoot"):
        r[ft] = clamp_euler_yxz(
            r[ft],
            min_x=float(p.get("foot_min_x_deg", -10.0)),
            max_x=float(p.get("foot_max_x_deg", 20.0)),
            min_y=-float(p.get("foot_y_deg", 6.0)),
            max_y=float(p.get("foot_y_deg", 6.0)),
            min_z=-float(p.get("foot_z_deg", 5.0)),
            max_z=float(p.get("foot_z_deg", 5.0)),
        )
    return r


def _collect_periodic_nut_cycle(bas_nut_clip: ClipData, n_src_frames: int = 61) -> tuple[list[dict[str, Quat]], list[Vec3]]:
    rots_list: list[dict[str, Quat]] = []
    locs_list: list[Vec3] = []
    max_i = min(n_src_frames, len(bas_nut_clip.frames))
    for i in range(max_i):
        r, l = _pose_at_index(bas_nut_clip, i)
        rots_list.append(r)
        locs_list.append(l)
    while len(rots_list) < n_src_frames:
        rots_list.append(dict(rots_list[0]))
        locs_list.append(locs_list[0])
    # Exact periodic seam closure
    rots_list[-1] = dict(rots_list[0])
    locs_list[-1] = locs_list[0]
    return rots_list, locs_list


def _finalize_tracks(
    name: str,
    frames: list[float],
    pose_list: list[dict[str, Quat]],
    lt_locs: list[Vec3],
    *,
    loop: bool,
    fps: float = 30.0,
) -> ClipData:
    rot_tracks: dict[str, list[Quat]] = {}
    for b in MAPPED_R15_BONE_NAMES:
        rot_tracks[b] = enforce_hemisphere_continuity([p[b] for p in pose_list])
    return ClipData(name=name, fps=fps, frames=frames, rot_tracks=rot_tracks, lt_locs=lt_locs, loop=loop)


def build_idle_loop(
    bas_nut_st_clip: ClipData,
    bas_nut_clip: ClipData,
    profile: CharacterProfile,
    *,
    apply_upper_torso: bool = False,
) -> ClipData:
    """Build the 91-frame (3.0s @ 30fps) seamless cyclic breathing idle loop."""
    cfg = profile.procedural_builders["idle_loop"]
    base_rots, base_loc = _prepare_idle_base_pose(bas_nut_st_clip, profile, apply_upper_torso=apply_upper_torso)
    nut_rots, nut_locs = _collect_periodic_nut_cycle(bas_nut_clip, int(cfg.get("breathing_frames", 61)))
    nut0_r, nut0_l = nut_rots[0], nut_locs[0]
    weights = cfg["weights"]
    bob_scale = float(cfg.get("pelvis_bob_scale", 0.55))

    total_frames = int(cfg.get("total_frames", 91))
    total_intervals = total_frames - 1
    frames = [float(i) for i in range(total_frames)]
    pose_list: list[dict[str, Quat]] = []
    lt_locs: list[Vec3] = []
    ident = q_identity()
    n_int_src = len(nut_rots) - 1

    for t in range(total_frames):
        s = (t / total_intervals) * n_int_src
        i0 = min(int(math.floor(s)), n_int_src - 1)
        alpha = s - i0
        i1 = i0 + 1
        frame_pose: dict[str, Quat] = {}
        for b in MAPPED_R15_BONE_NAMES:
            q_nut = q_slerp(nut_rots[i0][b], nut_rots[i1][b], alpha)
            q_delta = q_mul(q_inv(nut0_r[b]), q_nut)
            if q_delta[0] < 0.0:
                q_delta = q_neg(q_delta)
            q_scaled = q_slerp(ident, q_delta, float(weights[b]))
            frame_pose[b] = q_norm(q_mul(base_rots[b], q_scaled))
        pose_list.append(frame_pose)
        ly = nut_locs[i0][1] + (nut_locs[i1][1] - nut_locs[i0][1]) * alpha
        d_y = (ly - nut0_l[1]) * bob_scale
        lt_locs.append((base_loc[0], base_loc[1] + d_y, base_loc[2]))

    return _finalize_tracks(str(cfg["output_name"]), frames, pose_list, lt_locs, loop=True)


def build_alt_idle_look(
    bas_nut_st_clip: ClipData,
    bas_nut_clip: ClipData,
    profile: CharacterProfile,
    *,
    apply_upper_torso: bool = False,
) -> ClipData:
    """Build the 121-frame (4.0s @ 30fps) look-around alt idle loop."""
    cfg = profile.procedural_builders["alt_idle_look"]
    base_rots, base_loc = _prepare_idle_base_pose(bas_nut_st_clip, profile, apply_upper_torso=apply_upper_torso)
    nut_rots, nut_locs = _collect_periodic_nut_cycle(bas_nut_clip, int(cfg.get("breathing_frames", 61)))
    nut0_r, nut0_l = nut_rots[0], nut_locs[0]
    weights = cfg["weights"]
    bob_scale = float(cfg.get("pelvis_bob_scale", 0.45))

    HEAD_YAW_KEYS   = [(0, 0.0), (14, 0.0), (40, 28.0), (50, 28.0), (80, -24.0), (90, -24.0), (114, 0.0), (120, 0.0)]
    HEAD_PITCH_KEYS = [(0, 0.0), (14, 0.0), (40, 5.0),  (50, 5.0),  (65, -2.0),  (80, 4.0),   (90, 4.0),   (114, 0.0), (120, 0.0)]
    HEAD_ROLL_KEYS  = [(0, 0.0), (14, 0.0), (40, -4.0), (50, -4.0), (80, 5.0),   (90, 5.0),   (114, 0.0), (120, 0.0)]
    TORSO_YAW_KEYS  = [(0, 0.0), (16, 0.0), (42, 4.5),  (50, 4.5),  (82, -4.5),  (90, -4.5),  (114, 0.0), (120, 0.0)]
    ARM_SWAY_KEYS   = [(0, 0.0), (16, 0.0), (42, 5.0),  (50, 5.0),  (82, -4.0),  (90, -4.0),  (114, 0.0), (120, 0.0)]

    total_frames = int(cfg.get("total_frames", 121))
    frames = [float(i) for i in range(total_frames)]
    pose_list: list[dict[str, Quat]] = []
    lt_locs: list[Vec3] = []
    ident = q_identity()
    n_int_src = len(nut_rots) - 1

    for t in range(total_frames):
        s = ((t % 60) / 60.0) * n_int_src
        i0 = min(int(math.floor(s)), n_int_src - 1)
        alpha = s - i0
        i1 = i0 + 1

        frame_pose: dict[str, Quat] = {}
        for b in MAPPED_R15_BONE_NAMES:
            q_nut = q_slerp(nut_rots[i0][b], nut_rots[i1][b], alpha)
            q_delta = q_mul(q_inv(nut0_r[b]), q_nut)
            if q_delta[0] < 0.0:
                q_delta = q_neg(q_delta)
            frame_pose[b] = q_norm(q_mul(base_rots[b], q_slerp(ident, q_delta, float(weights[b]))))

        hy = _eval_keyframes(t, HEAD_YAW_KEYS)
        hp = _eval_keyframes(t, HEAD_PITCH_KEYS)
        hr = _eval_keyframes(t, HEAD_ROLL_KEYS)
        ty = _eval_keyframes(t, TORSO_YAW_KEYS)
        asw = _eval_keyframes(t, ARM_SWAY_KEYS)

        frame_pose["LowerTorso"] = q_norm(q_mul(q_from_axis_angle((0.0, 1.0, 0.0), math.radians(ty)), frame_pose["LowerTorso"]))
        frame_pose["Head"] = q_norm(
            q_mul(
                q_from_axis_angle((0.0, 1.0, 0.0), math.radians(hy)),
                q_mul(
                    frame_pose["Head"],
                    q_mul(
                        q_from_axis_angle((1.0, 0.0, 0.0), math.radians(hp)),
                        q_from_axis_angle((0.0, 0.0, 1.0), math.radians(hr)),
                    ),
                ),
            )
        )
        frame_pose["LeftUpperArm"] = q_norm(q_mul(frame_pose["LeftUpperArm"], q_from_axis_angle((0.0, 0.0, 1.0), math.radians(asw))))
        frame_pose["LeftLowerArm"] = q_norm(q_mul(frame_pose["LeftLowerArm"], q_from_axis_angle((1.0, 0.0, 0.0), math.radians(asw * 0.8))))

        lt_w_t = frame_pose["LowerTorso"]
        frame_pose["LeftFoot"] = level_foot_to_ground(q_mul(lt_w_t, q_mul(frame_pose["LeftUpperLeg"], frame_pose["LeftLowerLeg"])))
        frame_pose["RightFoot"] = level_foot_to_ground(q_mul(lt_w_t, q_mul(frame_pose["RightUpperLeg"], frame_pose["RightLowerLeg"])))

        pose_list.append(frame_pose)
        ly = nut_locs[i0][1] + (nut_locs[i1][1] - nut_locs[i0][1]) * alpha
        d_y = (ly - nut0_l[1]) * bob_scale
        lt_locs.append((base_loc[0], base_loc[1] + d_y, base_loc[2]))

    return _finalize_tracks(str(cfg["output_name"]), frames, pose_list, lt_locs, loop=True)


def build_jump_and_fall(
    bas_nut_st_clip: ClipData,
    bas_nut_jump_clip: ClipData,
    bas_jump_clip: ClipData,
    bas_jump_lp_clip: ClipData,
    profile: CharacterProfile,
    *,
    apply_upper_torso_on_jump: bool = False,
    smooth_jump_launch: bool = True,
) -> tuple[ClipData, ClipData]:
    """Build 36-frame Jump (Loop=False, starts at idle, ends at Fall f1) and 36-frame Fall loop."""
    jcfg = profile.procedural_builders["jump"]
    fcfg = profile.procedural_builders["fall"]

    idle_start_pose, idle_start_loc = _prepare_idle_base_pose(bas_nut_st_clip, profile, apply_upper_torso=apply_upper_torso_on_jump)

    crouch_raw, _ = _pose_at_index(bas_nut_jump_clip, int(jcfg.get("crouch_frame", 2)))
    nut_jump_crouch = _polish_jump_pose(crouch_raw, profile, align_upper_torso=apply_upper_torso_on_jump)

    bas_jump_poses = [
        _polish_jump_pose(_pose_at_index(bas_jump_clip, i)[0], profile, align_upper_torso=apply_upper_torso_on_jump)
        for i in range(min(22, len(bas_jump_clip.frames)))
    ]
    jump_lp_poses = [
        _polish_jump_pose(_pose_at_index(bas_jump_lp_clip, i)[0], profile, align_upper_torso=apply_upper_torso_on_jump)
        for i in range(min(12, len(bas_jump_lp_clip.frames)))
    ]
    jump_lp_poses[-1] = dict(jump_lp_poses[0])

    total_j = int(jcfg.get("total_frames", 36))
    j_frames = [float(i) for i in range(total_j)]
    j_poses: list[dict[str, Quat]] = []
    j_locs: list[Vec3] = []

    spring_end_f = 22 if smooth_jump_launch else 17
    for f in range(total_j):
        if f <= 5:
            u = f / 5.0
            u_ease = math.sin(u * math.pi * 0.5)
            p = _interp_pose(idle_start_pose, nut_jump_crouch, u_ease)
            lx = idle_start_loc[0] * (1.0 - u_ease)
            ly = idle_start_loc[1] * (1.0 - u_ease) + (-0.15) * u_ease
            lz = idle_start_loc[2] * (1.0 - u_ease)
        elif f <= spring_end_f:
            u = (f - 5) / float(spring_end_f - 5)
            u_eased = (u * u * (3.0 - 2.0 * u)) if smooth_jump_launch else (1.0 - (1.0 - u) ** 1.45)
            max_idx = len(bas_jump_poses) - 2
            s = 2.0 + u_eased * float(max(1, len(bas_jump_poses) - 3))
            i0 = min(int(math.floor(s)), max_idx)
            p_spring = _interp_pose(bas_jump_poses[i0], bas_jump_poses[i0 + 1], s - i0)
            if smooth_jump_launch and f < 11:
                b = (f - 5) / 6.0
                p = _interp_pose(nut_jump_crouch, p_spring, b * b * (3.0 - 2.0 * b))
            elif not smooth_jump_launch and f < 8:
                p = _interp_pose(nut_jump_crouch, p_spring, (f - 5) / 3.0)
            else:
                p = p_spring
            lx, ly, lz = 0.0, -0.15 + u_eased * 0.40, 0.0
        else:
            p = dict(jump_lp_poses[0])
            denom = max(1, (total_j - 1) - spring_end_f)
            lx, ly, lz = 0.0, 0.25 - 0.01 * math.sin(math.pi * (f - spring_end_f) / denom), 0.0
        j_poses.append(p)
        j_locs.append((lx, ly, lz))

    # Ensure final jump frame equals frame 0 of fall cleanly
    j_poses[-1] = dict(jump_lp_poses[0])
    j_locs[-1] = (0.0, 0.25, 0.0)

    jump_clip = _finalize_tracks(str(jcfg["output_name"]), j_frames, j_poses, j_locs, loop=False)

    # Fall loop (36 frames, f=0 == f=35 == jump apex)
    total_f = int(fcfg.get("total_frames", 36))
    f_frames = [float(i) for i in range(total_f)]
    f_poses: list[dict[str, Quat]] = []
    f_locs: list[Vec3] = []
    denom_f = max(1, total_f - 1)
    n_lp_int = len(jump_lp_poses) - 1

    for f in range(total_f):
        phase = 2.0 * math.pi * f / denom_f
        s = (f / denom_f) * n_lp_int
        i0 = min(int(math.floor(s)), n_lp_int - 1)
        p = _interp_pose(jump_lp_poses[i0], jump_lp_poses[i0 + 1], s - i0)
        sway_a = math.sin(phase)
        sway_b = math.sin(phase)  # vanishes at phase=0 and 2pi so Jump f36 == Fall f1 == Fall f36 identically

        p["UpperTorso"] = q_norm(q_mul(p["UpperTorso"], q_from_axis_angle((1.0, 0.0, 0.0), math.radians(2.5 * sway_a))))
        p["Head"] = q_norm(q_mul(p["Head"], q_from_axis_angle((1.0, 0.0, 0.0), math.radians(-2.0 * sway_a))))
        p["LeftUpperArm"] = q_norm(q_mul(p["LeftUpperArm"], q_from_axis_angle((0.0, 0.0, 1.0), math.radians(4.0 * sway_b))))
        p["RightUpperArm"] = q_norm(q_mul(p["RightUpperArm"], q_from_axis_angle((0.0, 0.0, 1.0), math.radians(-3.5 * sway_b))))
        p["LeftUpperLeg"] = q_norm(q_mul(p["LeftUpperLeg"], q_from_axis_angle((1.0, 0.0, 0.0), math.radians(3.5 * sway_b))))
        p["RightUpperLeg"] = q_norm(q_mul(p["RightUpperLeg"], q_from_axis_angle((1.0, 0.0, 0.0), math.radians(-3.5 * sway_b))))
        f_poses.append(p)
        f_locs.append((0.0, 0.25 + 0.025 * sway_a, 0.0))

    f_poses[-1] = dict(f_poses[0])
    f_locs[-1] = f_locs[0]
    fall_clip = _finalize_tracks(str(fcfg["output_name"]), f_frames, f_poses, f_locs, loop=True)

    return jump_clip, fall_clip
