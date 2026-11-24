"""Explicit-only auto-fix engine writing strictly to a separate _fixed output folder."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .cleanup import apply_cleanup_pipeline
from .config import CharacterProfile, load_r15_reference, load_roblox_rules
from .constants import MAPPED_R15_BONE_NAMES
from .math_retarget import Quat, Vec3, enforce_hemisphere_continuity, q_angle_rad, q_identity, q_slerp
from .validation import ClipData, ClipValidationResult, validate_clip

FIX_CATEGORIES: tuple[str, ...] = (
    "strip_translation",
    "floor_lift",
    "clamps",
    "smoothing",
    "time_stretch",
)

_FIXED_DIR_RE = re.compile(r"^(?P<base>.*?)_fixed(?:_(?P<num>\d+))?$")


def next_fixed_output_dir(base_parent: Path) -> Path:
    """Return a clean non-overwriting *_fixed sibling folder without chaining _fixed_2_fixed."""
    m = _FIXED_DIR_RE.match(base_parent.name)
    if m:
        base = m.group("base")
        num = int(m.group("num") or "1") + 1
        cand = base_parent.with_name(f"{base}_fixed_{num}")
        while cand.exists() and any(cand.iterdir()):
            num += 1
            cand = base_parent.with_name(f"{base}_fixed_{num}")
        return cand
    return base_parent.with_name(f"{base_parent.name}_fixed")


class FixConfigurationError(ValueError):
    """Raised when fix is invoked without explicit --apply categories or with unknown categories."""


class FixFailedError(RuntimeError):
    """Raised when iterative auto-fix cannot satisfy validation constraints."""


@dataclass
class FixResult:
    clip_name: str
    categories_applied: list[str]
    iterations: int
    stretch_factor: float
    floor_lift_studs: float
    before: ClipValidationResult
    after: ClipValidationResult
    fixed_clip: ClipData
    actions_log: list[str] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "clip_name": self.clip_name,
            "categories_applied": self.categories_applied,
            "iterations": self.iterations,
            "stretch_factor": round(self.stretch_factor, 4),
            "floor_lift_studs": round(self.floor_lift_studs, 4),
            "actions_log": self.actions_log,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


def resolve_apply_categories(categories: Iterable[str] | None) -> list[str]:
    if not categories:
        raise FixConfigurationError(
            f"Refusing to run fix without explicit --apply categories. Valid categories: {', '.join(FIX_CATEGORIES)}"
        )
    cleaned: list[str] = []
    for c in categories:
        name = c.strip().lower()
        if not name:
            continue
        if name not in FIX_CATEGORIES:
            raise FixConfigurationError(f"Unknown fix category '{name}'. Valid: {', '.join(FIX_CATEGORIES)}")
        if name not in cleaned:
            cleaned.append(name)
    if not cleaned:
        raise FixConfigurationError("At least one valid --apply category is required.")
    return cleaned


def _build_bone_chains(r15_ref: dict[str, Any]) -> dict[str, list[str]]:
    """Map each R15 bone or virtual tip to its ancestor chain (excluding HumanoidRootPart)."""
    parent_map: dict[str, str | None] = {}
    tip_owner: dict[str, str] = {}
    for meta in r15_ref.get("bones", []):
        name = meta["name"]
        parent_map[name] = meta.get("parent")
        for tip_name in meta.get("virtual_tips_rbx_local", {}):
            tip_owner[tip_name] = name

    chains: dict[str, list[str]] = {}
    for bone in MAPPED_R15_BONE_NAMES:
        chain: list[str] = []
        cur: str | None = bone
        while cur and cur != "HumanoidRootPart":
            if cur in MAPPED_R15_BONE_NAMES:
                chain.append(cur)
            cur = parent_map.get(cur)
        chains[bone] = chain
    for tip_name, owner in tip_owner.items():
        chains[tip_name] = list(chains.get(owner, []))
    return chains


def _close_loop_seam(clip: ClipData, eps: float = 0.015) -> tuple[ClipData, bool]:
    """Smoothly blend the tail of a looping clip onto frame 0 so frame[-1] == frame[0] exactly."""
    n = len(clip.frames)
    if not clip.loop or n < 4:
        return clip, False

    max_seam_rad = max(
        (q_angle_rad(clip.rot_tracks[b][0], clip.rot_tracks[b][-1]) for b in MAPPED_R15_BONE_NAMES if b in clip.rot_tracks),
        default=0.0,
    )
    loc_diff = math.sqrt(sum((clip.lt_locs[-1][i] - clip.lt_locs[0][i]) ** 2 for i in range(3)))
    if max_seam_rad <= eps * 0.25 and loc_diff <= eps * 0.25:
        return clip, False

    out = copy.deepcopy(clip)
    # Use a wider blend window when seam delta is larger so closing does not create a velocity spike
    if max_seam_rad > 0.20 or loc_diff > 0.05:
        blend_n = min(max(6, n // 4), 16, n - 2)
    else:
        blend_n = min(max(4, n // 6), 10, n - 2)

    start_idx = n - blend_n
    for b in MAPPED_R15_BONE_NAMES:
        tr = out.rot_tracks.get(b)
        if not tr:
            continue
        q_target = tr[0]
        new_t = list(tr)
        for k in range(blend_n):
            idx = start_idx + k
            u = float(k + 1) / float(blend_n)
            w = u * u * (3.0 - 2.0 * u)
            new_t[idx] = q_target if idx == n - 1 else q_slerp(tr[idx], q_target, w)
        out.rot_tracks[b] = enforce_hemisphere_continuity(new_t)

    p_target = out.lt_locs[0]
    new_locs = list(out.lt_locs)
    for k in range(blend_n):
        idx = start_idx + k
        u = float(k + 1) / float(blend_n)
        w = u * u * (3.0 - 2.0 * u)
        if idx == n - 1:
            new_locs[idx] = p_target
        else:
            p = out.lt_locs[idx]
            new_locs[idx] = (
                p[0] + (p_target[0] - p[0]) * w,
                p[1] + (p_target[1] - p[1]) * w,
                p[2] + (p_target[2] - p[2]) * w,
            )
    out.lt_locs = new_locs
    return out, True


def _smooth_window(
    clip: ClipData,
    *,
    center_time_s: float,
    bones: Iterable[str],
    include_lt_loc: bool = True,
    radius: int = 3,
    weight: float = 0.50,
) -> ClipData:
    """Apply targeted SLERP/Lerp smoothing around a hot timestamp on a specific kinematic chain."""
    n = len(clip.frames)
    if n < 3 or clip.fps <= 0.0:
        return clip
    out = copy.deepcopy(clip)
    center_idx = int(round(center_time_s * clip.fps))
    center_idx = max(0, min(n - 1, center_idx))
    target_bones = [b for b in bones if b in out.rot_tracks]
    if not target_bones:
        target_bones = list(MAPPED_R15_BONE_NAMES)

    indices: list[int] = []
    for off in range(-radius, radius + 1):
        idx = center_idx + off
        if out.loop and n >= 4:
            idx = idx % (n - 1)
            if idx not in indices:
                indices.append(idx)
        elif 0 <= idx < n:
            if idx not in indices:
                indices.append(idx)

    for b in target_bones:
        tr = out.rot_tracks[b]
        new_t = list(tr)
        for idx in indices:
            if out.loop and n >= 4:
                prev_i = (idx - 1) % (n - 1)
                next_i = (idx + 1) % (n - 1)
                mid = q_slerp(tr[prev_i], tr[next_i], 0.5)
                new_t[idx] = q_slerp(tr[idx], mid, weight)
            else:
                if idx == 0:
                    new_t[0] = q_slerp(tr[0], tr[1], weight * 0.6)
                elif idx == n - 1:
                    new_t[-1] = q_slerp(tr[-1], tr[-2], weight * 0.6)
                else:
                    mid = q_slerp(tr[idx - 1], tr[idx + 1], 0.5)
                    new_t[idx] = q_slerp(tr[idx], mid, weight)
        if out.loop and n >= 4:
            new_t[-1] = new_t[0]
        out.rot_tracks[b] = enforce_hemisphere_continuity(new_t)

    if include_lt_loc:
        locs = out.lt_locs
        new_locs = list(locs)
        for idx in indices:
            if out.loop and n >= 4:
                prev_i = (idx - 1) % (n - 1)
                next_i = (idx + 1) % (n - 1)
                mx = 0.5 * (locs[prev_i][0] + locs[next_i][0])
                my = 0.5 * (locs[prev_i][1] + locs[next_i][1])
                mz = 0.5 * (locs[prev_i][2] + locs[next_i][2])
                new_locs[idx] = (
                    locs[idx][0] + (mx - locs[idx][0]) * weight,
                    locs[idx][1] + (my - locs[idx][1]) * weight,
                    locs[idx][2] + (mz - locs[idx][2]) * weight,
                )
            else:
                if idx == 0:
                    new_locs[0] = tuple(locs[0][k] + (locs[1][k] - locs[0][k]) * (weight * 0.6) for k in range(3))  # type: ignore[assignment]
                elif idx == n - 1:
                    new_locs[-1] = tuple(locs[-1][k] + (locs[-2][k] - locs[-1][k]) * (weight * 0.6) for k in range(3))  # type: ignore[assignment]
                else:
                    mx = 0.5 * (locs[idx - 1][0] + locs[idx + 1][0])
                    my = 0.5 * (locs[idx - 1][1] + locs[idx + 1][1])
                    mz = 0.5 * (locs[idx - 1][2] + locs[idx + 1][2])
                    new_locs[idx] = (
                        locs[idx][0] + (mx - locs[idx][0]) * weight,
                        locs[idx][1] + (my - locs[idx][1]) * weight,
                        locs[idx][2] + (mz - locs[idx][2]) * weight,
                    )
        if out.loop and n >= 4:
            new_locs[-1] = new_locs[0]
        out.lt_locs = new_locs

    return out


def _damp_angular_spikes(clip: ClipData, max_deg_s: float, *, weight: float = 0.55) -> tuple[ClipData, bool]:
    """Locally damp per-bone frame-to-frame rotational spikes exceeding max_deg_s."""
    n = len(clip.frames)
    if n < 3 or clip.fps <= 0.0:
        return clip, False
    max_step_rad = math.radians(max_deg_s / clip.fps) * 0.90
    out = copy.deepcopy(clip)
    changed = False

    for b, tr in out.rot_tracks.items():
        hot: set[int] = set()
        for i in range(1, n):
            if q_angle_rad(tr[i - 1], tr[i]) > max_step_rad:
                hot.add(i - 1)
                hot.add(i)
        if not hot:
            continue
        changed = True
        new_t = list(tr)
        for idx in sorted(hot):
            if out.loop and n >= 4:
                j = idx % (n - 1)
                prev_i = (j - 1) % (n - 1)
                next_i = (j + 1) % (n - 1)
                mid = q_slerp(tr[prev_i], tr[next_i], 0.5)
                new_t[j] = q_slerp(tr[j], mid, weight)
            else:
                if idx == 0:
                    new_t[0] = q_slerp(tr[0], tr[1], weight * 0.6)
                elif idx == n - 1:
                    new_t[-1] = q_slerp(tr[-1], tr[-2], weight * 0.6)
                else:
                    mid = q_slerp(tr[idx - 1], tr[idx + 1], 0.5)
                    new_t[idx] = q_slerp(tr[idx], mid, weight)
        if out.loop and n >= 4:
            new_t[-1] = new_t[0]
        out.rot_tracks[b] = enforce_hemisphere_continuity(new_t)

    return out, changed


def _smooth_clip_tracks(
    clip: ClipData,
    *,
    preserve_endpoints: bool = True,
    smooth_boundaries: bool = False,
    weight: float = 0.35,
) -> ClipData:
    out = copy.deepcopy(clip)
    n = len(out.frames)
    if n < 3:
        return out
    for b, track in out.rot_tracks.items():
        new_t = list(track)
        for i in range(1, n - 1):
            mid = q_slerp(track[i - 1], track[i + 1], 0.5)
            new_t[i] = q_slerp(track[i], mid, weight)
        if smooth_boundaries or not preserve_endpoints:
            if out.loop and n >= 4:
                mid_seam = q_slerp(track[1], track[-2], 0.5)
                seam_q = q_slerp(track[0], mid_seam, weight)
                new_t[0] = seam_q
                new_t[-1] = seam_q
            else:
                new_t[0] = q_slerp(track[0], track[1], weight * 0.55)
                new_t[-1] = q_slerp(track[-1], track[-2], weight * 0.55)
        elif out.loop and n >= 4:
            new_t[-1] = new_t[0]
        out.rot_tracks[b] = enforce_hemisphere_continuity(new_t)

    new_locs = list(out.lt_locs)
    for i in range(1, n - 1):
        mx = 0.5 * (out.lt_locs[i - 1][0] + out.lt_locs[i + 1][0])
        my = 0.5 * (out.lt_locs[i - 1][1] + out.lt_locs[i + 1][1])
        mz = 0.5 * (out.lt_locs[i - 1][2] + out.lt_locs[i + 1][2])
        new_locs[i] = (
            out.lt_locs[i][0] + (mx - out.lt_locs[i][0]) * weight,
            out.lt_locs[i][1] + (my - out.lt_locs[i][1]) * weight,
            out.lt_locs[i][2] + (mz - out.lt_locs[i][2]) * weight,
        )
    if smooth_boundaries or not preserve_endpoints:
        if out.loop and n >= 4:
            mx = 0.5 * (out.lt_locs[1][0] + out.lt_locs[-2][0])
            my = 0.5 * (out.lt_locs[1][1] + out.lt_locs[-2][1])
            mz = 0.5 * (out.lt_locs[1][2] + out.lt_locs[-2][2])
            seam_l = (
                out.lt_locs[0][0] + (mx - out.lt_locs[0][0]) * weight,
                out.lt_locs[0][1] + (my - out.lt_locs[0][1]) * weight,
                out.lt_locs[0][2] + (mz - out.lt_locs[0][2]) * weight,
            )
            new_locs[0] = seam_l
            new_locs[-1] = seam_l
        else:
            new_locs[0] = tuple(out.lt_locs[0][k] + (out.lt_locs[1][k] - out.lt_locs[0][k]) * (weight * 0.55) for k in range(3))  # type: ignore[assignment]
            new_locs[-1] = tuple(out.lt_locs[-1][k] + (out.lt_locs[-2][k] - out.lt_locs[-1][k]) * (weight * 0.55) for k in range(3))  # type: ignore[assignment]
    elif out.loop and n >= 4:
        new_locs[-1] = new_locs[0]
    out.lt_locs = new_locs
    return out


def _clamp_max_joint_angle(q: Quat, max_deg: float) -> Quat:
    ang = math.degrees(q_angle_rad(q_identity(), q))
    if ang <= max_deg or ang <= 1e-6:
        return q
    return q_slerp(q_identity(), q, max_deg / ang)


def _stretch_clip_uniformly(clip: ClipData, factor: float) -> ClipData:
    if factor <= 1.0 + 1e-6 or len(clip.frames) < 2:
        return clip
    out = copy.deepcopy(clip)
    new_dur = clip.duration_sec * factor
    n_out = max(len(clip.frames), int(round(new_dur * clip.fps)) + 1)
    f0 = clip.frames[0]
    new_frames = [float(f0 + i) for i in range(n_out)]
    new_rots: dict[str, list[Quat]] = {b: [] for b in MAPPED_R15_BONE_NAMES}
    new_lt: list[Vec3] = []
    for i in range(n_out):
        t_src = clip.duration_sec * (float(i) / float(n_out - 1))
        rots, lt, _ = clip.sample_at_time(t_src)
        for b in MAPPED_R15_BONE_NAMES:
            new_rots[b].append(rots[b])
        new_lt.append(lt)
    for b in MAPPED_R15_BONE_NAMES:
        if out.loop and n_out >= 4:
            new_rots[b][-1] = new_rots[b][0]
        new_rots[b] = enforce_hemisphere_continuity(new_rots[b])
    if out.loop and n_out >= 4:
        new_lt[-1] = new_lt[0]
    out.frames = new_frames
    out.rot_tracks = new_rots
    out.lt_locs = new_lt
    return out


def _lift_for_floor_and_bounds(
    clip: ClipData,
    eval_res: ClipValidationResult,
    *,
    floor_target: float,
    v09_y_target: float,
    scale: float,
) -> tuple[float, list[str]]:
    """Raise LowerTorso.Y uniformly to satisfy both V05 foot depth and V09 lowest body Y."""
    need_v05 = max(0.0, eval_res.max_foot_depth_studs - floor_target)
    need_v09 = max(0.0, v09_y_target - eval_res.lowest_body_y_studs)
    needed_studs = max(need_v05, need_v09)
    if needed_studs <= 1e-4:
        return 0.0, []
    delta_y_bl = needed_studs / scale
    clip.lt_locs = [(x, y + delta_y_bl, z) for (x, y, z) in clip.lt_locs]
    reasons = []
    if need_v05 > 1e-4:
        reasons.append(f"V05 foot depth {eval_res.max_foot_depth_studs:.3f}->{floor_target:.2f}")
    if need_v09 > 1e-4:
        reasons.append(f"V09 lowY {eval_res.lowest_body_y_studs:.2f}->{v09_y_target:.2f}")
    return needed_studs, reasons


def fix_clip(
    clip: ClipData,
    apply_categories: Iterable[str],
    *,
    profile: CharacterProfile | None = None,
    rules_cfg: dict[str, Any] | None = None,
    r15_ref: dict[str, Any] | None = None,
    max_iters: int = 20,
    max_stretch: float = 4.0,
    strict: bool = True,
) -> FixResult:
    """Explicitly apply user-selected fix categories without mutating original ClipData."""
    categories = resolve_apply_categories(apply_categories)
    rules = rules_cfg or load_roblox_rules()
    ref = r15_ref or load_r15_reference()
    before = validate_clip(clip, rules_cfg=rules, r15_ref=ref)
    cur = copy.deepcopy(clip)
    log: list[str] = []
    scale = float(ref.get("studs_per_blender_meter", 3.8584))
    rule_map = {r["id"]: r for r in rules["rules"]}
    v03_rule = rule_map["V03"]
    v04_rule = rule_map["V04"]
    v05_rule = rule_map["V05"]
    v08_rule = rule_map["V08"]
    v09_rule = rule_map["V09"]
    v11_rule = rule_map["V11"]

    # Safety buffers below WARN/FAIL lines so FBX export/re-import round-trip stays clean PASS
    speed_target = min(42.5, float(v03_rule.get("auto_fix_target", 45.0)) - 2.5)
    ang_target = max(1000.0, float(v04_rule["threshold"]) - 150.0)
    max_depth_allowed = float(v05_rule["threshold"])
    floor_margin = max(0.03, float(v05_rule.get("auto_fix_margin", 0.02)))
    floor_target = max(0.0, max_depth_allowed - floor_margin)
    v08_safe = max(10.0, float(v08_rule["threshold"]) - 2.0)
    v09_y_target = float(v09_rule["threshold"]) + 0.08
    v11_eps = float(v11_rule["threshold"])
    bone_chains = _build_bone_chains(ref)

    total_floor_lift_studs = 0.0
    total_stretch = 1.0

    # Pass 1: Non-iterative deterministic structural fixes
    if "strip_translation" in categories and cur.non_lt_locs:
        cur.non_lt_locs = {}
        log.append("strip_translation: stripped all non-LowerTorso translations")

    if "clamps" in categories:
        params = dict(profile.cleanup_params) if profile else {}
        toggles = {
            "right_hand_clamp": True,
            "left_hand_clamp": True,
            "elbow_cap": True,
            "feet_level": False,
            "head_forward_yaw": False,
            "upper_torso_align": False,
        }
        new_poses: list[dict[str, Quat]] = []
        for idx in range(len(cur.frames)):
            frame_pose = {b: cur.rot_tracks[b][idx] for b in MAPPED_R15_BONE_NAMES}
            fixed_pose, _ = apply_cleanup_pipeline(frame_pose, toggles, params)
            for b in MAPPED_R15_BONE_NAMES:
                fixed_pose[b] = _clamp_max_joint_angle(fixed_pose[b], v08_safe)
            new_poses.append(fixed_pose)
        for b in MAPPED_R15_BONE_NAMES:
            cur.rot_tracks[b] = enforce_hemisphere_continuity([p[b] for p in new_poses])
        log.append("clamps: applied wrist/elbow & V08 joint angle clamps across all frames")

    if "smoothing" in categories and cur.loop:
        cur, seam_closed = _close_loop_seam(cur, eps=v11_eps)
        if seam_closed:
            log.append("smoothing: smoothly blended looping clip tail onto frame 0 (closed V11 loop seam)")

    if "floor_lift" in categories:
        eval_now = validate_clip(cur, rules_cfg=rules, r15_ref=ref)
        lifted, reasons = _lift_for_floor_and_bounds(
            cur, eval_now, floor_target=floor_target, v09_y_target=v09_y_target, scale=scale
        )
        if lifted > 0.0:
            total_floor_lift_studs += lifted
            log.append(f"floor_lift: raised LowerTorso.Y by +{lifted:.3f} studs ({', '.join(reasons)})")

    # Pass 2: Iterative linear speed (V03) AND angular velocity (V04) repair
    iterations = 0
    latest = validate_clip(cur, rules_cfg=rules, r15_ref=ref)

    for it in range(1, max_iters + 1):
        iterations = it
        needs_speed = latest.peak_speed_studs_s > speed_target
        needs_ang = latest.peak_ang_vel_deg_s > ang_target
        if (not needs_speed and not needs_ang) or latest.is_face_only_or_static:
            break

        prev_spd = latest.peak_speed_studs_s
        prev_ang = latest.peak_ang_vel_deg_s

        if "smoothing" in categories:
            cand = copy.deepcopy(cur)
            # 1) Damp sharp local per-bone rotational spikes
            cand, damped = _damp_angular_spikes(cand, max_deg_s=ang_target, weight=0.55)
            # 2) Targeted kinematic-chain smoothing around offending V03 / V04 timestamps
            if needs_speed and latest.peak_speed_part:
                chain = bone_chains.get(latest.peak_speed_part, list(MAPPED_R15_BONE_NAMES))
                cand = _smooth_window(
                    cand,
                    center_time_s=latest.peak_speed_time_s,
                    bones=chain,
                    include_lt_loc=True,
                    radius=3,
                    weight=0.52,
                )
            if needs_ang and latest.peak_ang_vel_part:
                v04_f = next((f for f in latest.findings if f.rule_id == "V04"), None)
                ang_t = v04_f.time_sec if (v04_f and v04_f.time_sec is not None) else latest.peak_speed_time_s
                chain = bone_chains.get(latest.peak_ang_vel_part, list(MAPPED_R15_BONE_NAMES))
                cand = _smooth_window(
                    cand,
                    center_time_s=float(ang_t),
                    bones=chain,
                    include_lt_loc=False,
                    radius=3,
                    weight=0.55,
                )

            cand_eval = validate_clip(cand, rules_cfg=rules, r15_ref=ref)
            # 3) If targeted pass wasn't enough (or didn't drop spikes much), apply a gentle full-clip pass
            still_hot = cand_eval.peak_speed_studs_s > speed_target or cand_eval.peak_ang_vel_deg_s > ang_target
            dropped_enough = (cand_eval.peak_speed_studs_s < prev_spd * 0.94) or (cand_eval.peak_ang_vel_deg_s < prev_ang * 0.94)
            if still_hot and (not dropped_enough or it % 2 == 0):
                dt_frame = 1.0 / max(cand.fps, 1.0)
                at_boundary = (
                    cand_eval.peak_speed_time_s <= 1.5 * dt_frame
                    or cand_eval.peak_speed_time_s >= max(0.0, cand.duration_sec - 1.5 * dt_frame)
                )
                cand = _smooth_clip_tracks(
                    cand,
                    preserve_endpoints=not at_boundary,
                    smooth_boundaries=at_boundary,
                    weight=0.42,
                )
                cand_eval = validate_clip(cand, rules_cfg=rules, r15_ref=ref)

            # Never flatten a valid clip into V02 static
            if not cand_eval.is_face_only_or_static:
                cur = cand
                latest = cand_eval
                log.append(
                    f"smoothing (iter {it}): spd={latest.peak_speed_studs_s:.1f}, ang={latest.peak_ang_vel_deg_s:.0f} deg/s (damped={damped})"
                )
                if latest.peak_speed_studs_s <= speed_target and latest.peak_ang_vel_deg_s <= ang_target:
                    break
            elif "time_stretch" not in categories:
                break

        needs_speed = latest.peak_speed_studs_s > speed_target
        needs_ang = latest.peak_ang_vel_deg_s > ang_target
        if "time_stretch" in categories and (needs_speed or needs_ang):
            # Give smoothing 2 targeted iterations first if enabled and spike is moderate
            severe = (latest.peak_speed_studs_s > speed_target * 1.45) or (latest.peak_ang_vel_deg_s > ang_target * 1.45)
            if "smoothing" not in categories or it >= 2 or severe:
                dur_cap_factor = (9.8 / cur.duration_sec) if cur.duration_sec > 0.0 else 1.0
                max_allowed = min(max_stretch / total_stretch, dur_cap_factor)
                spd_ratio = (latest.peak_speed_studs_s / speed_target) if needs_speed else 1.0
                ang_ratio = (latest.peak_ang_vel_deg_s / ang_target) if needs_ang else 1.0
                needed_factor = min(max_allowed, max(spd_ratio, ang_ratio) * 1.06)
                if needed_factor > 1.01:
                    cur = _stretch_clip_uniformly(cur, needed_factor)
                    total_stretch *= needed_factor
                    log.append(
                        f"time_stretch (iter {it}): uniformly stretched by {needed_factor:.3f}x "
                        f"(total {total_stretch:.3f}x, dur={cur.duration_sec:.3f}s)"
                    )
                    latest = validate_clip(cur, rules_cfg=rules, r15_ref=ref)
                    if latest.peak_speed_studs_s <= speed_target and latest.peak_ang_vel_deg_s <= ang_target:
                        break
                elif "smoothing" not in categories:
                    break
        elif "smoothing" not in categories:
            break

    # Final seam + floor/bounds check in case smoothing or time-stretch shifted foot/hand slightly
    if "smoothing" in categories and cur.loop:
        cur, seam_post = _close_loop_seam(cur, eps=v11_eps)
        if seam_post:
            log.append("smoothing (post): ensured exact V11 loop seam continuity")

    if "floor_lift" in categories:
        eval_post = validate_clip(cur, rules_cfg=rules, r15_ref=ref)
        extra_studs, reasons = _lift_for_floor_and_bounds(
            cur, eval_post, floor_target=floor_target, v09_y_target=v09_y_target, scale=scale
        )
        if extra_studs > 0.0:
            total_floor_lift_studs += extra_studs
            log.append(f"floor_lift (post): raised LowerTorso.Y by +{extra_studs:.3f} studs ({', '.join(reasons)})")

    after = validate_clip(cur, rules_cfg=rules, r15_ref=ref)
    if strict and after.status == "FAIL":
        msgs = "; ".join(f"{f.rule_id}: {f.message}" for f in after.findings if f.severity == "FAIL")
        raise FixFailedError(
            f"Auto-fix failed on '{clip.name}' using categories {categories}. Remaining FAILs: {msgs}"
        )

    return FixResult(
        clip_name=clip.name,
        categories_applied=categories,
        iterations=iterations,
        stretch_factor=total_stretch,
        floor_lift_studs=total_floor_lift_studs,
        before=before,
        after=after,
        fixed_clip=cur,
        actions_log=log,
    )


def write_fix_log(results: list[FixResult], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fixed_clips": [r.to_summary_dict() for r in results]}
    log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
