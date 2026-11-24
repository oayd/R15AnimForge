"""Read-only, opt-in Roblox upload validation engine (Rules V02-V09, V11-V12) and HTML/JSON reporter.

IMPORTANT: Never imported or called by `convert`. Validation runs ONLY when explicitly requested.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .config import load_r15_reference, load_roblox_rules
from .constants import MAPPED_R15_BONE_NAMES
from .math_retarget import (
    Quat,
    Vec3,
    blender_quat_to_roblox_quat,
    q_angle_rad,
    q_identity,
    q_mul,
    q_norm,
    q_rotate_vec,
    q_slerp,
)


import re

_LOOP_KEYWORDS = ("idle", "walk", "run", "dash", "fall", "climb", "swim", "loop", "_lp")
_NON_LOOP_SUFFIXES = (
    "_st",
    "_ed",
    "_end",
    "_str",
    "_miss",
    "_hit",
    "_turn",
    "_crouch",
    "_land",
    "_wake",
    "_rise",
)
_COMBAT_PREFIX_TOKENS = ("_atk_", "_dmg_", "_hha_", "_gha_", "_spa_", "_dem_", "_grd_", "_fce_")
_NEUTRAL_IDLE_END_RE = re.compile(r"(?:^|_)bas_(?:[a-z0-9]{1,3}_)?nut$")


def infer_clip_is_loop(name: str) -> bool:
    """Infer whether an animation clip is expected to loop (locomotion/idle/lp vs one-shot transitions/attacks)."""
    low = name.strip().lower()
    while low.endswith("_fixed"):
        low = low[:-6]
    # Explicit loop markers always loop (e.g. *_lp, *_idle_loop, dmg_spin_lp)
    if any(kw in low for kw in ("_lp", "loop")):
        return True
    # One-shot jumps never loop unless explicitly marked *_lp / loop above
    if "jump" in low:
        return False
    # One-shot transition/start/end/turn/crouch clips do not loop (e.g. bas_nut_crouch, bas_nut_turn)
    if any(low.endswith(suf) for suf in _NON_LOOP_SUFFIXES):
        return False
    # Neutral standing idle ends in bas_nut or bas_<stance>_nut (not bas_nut_<transition>)
    if _NEUTRAL_IDLE_END_RE.search(low):
        return True
    # Combat/damage/special/guard/facial clips only loop if explicitly *_lp / loop (handled above)
    if any(tok in low for tok in _COMBAT_PREFIX_TOKENS):
        return False
    return any(kw in low for kw in _LOOP_KEYWORDS)


@dataclass
class ClipData:
    """In-memory sampled R15 clip representation in Blender R15_Rig local space."""

    name: str
    fps: float
    frames: list[float]
    rot_tracks: dict[str, list[Quat]]
    lt_locs: list[Vec3]
    loop: bool = True
    non_lt_locs: dict[str, list[Vec3]] = field(default_factory=dict)
    source_path: str | None = None

    @property
    def duration_sec(self) -> float:
        if len(self.frames) < 2 or self.fps <= 0.0:
            return 0.0
        return float(self.frames[-1] - self.frames[0]) / float(self.fps)

    def sample_at_time(self, t_sec: float) -> tuple[dict[str, Quat], Vec3, dict[str, Vec3]]:
        """Evaluate joint quaternions (SLERP) and translations (Lerp) at t_sec in [0, duration]."""
        if not self.frames:
            return {b: q_identity() for b in MAPPED_R15_BONE_NAMES}, (0.0, 0.0, 0.0), {}
        target_f = self.frames[0] + max(0.0, t_sec) * self.fps
        if target_f <= self.frames[0]:
            i0, i1, alpha = 0, 0, 0.0
        elif target_f >= self.frames[-1]:
            last = len(self.frames) - 1
            i0, i1, alpha = last, last, 0.0
        else:
            # Binary search
            lo, hi = 0, len(self.frames) - 1
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                if self.frames[mid] <= target_f:
                    lo = mid
                else:
                    hi = mid
            span = self.frames[hi] - self.frames[lo]
            i0, i1 = lo, hi
            alpha = 0.0 if span <= 1e-9 else (target_f - self.frames[lo]) / span

        rots: dict[str, Quat] = {}
        for b in MAPPED_R15_BONE_NAMES:
            track = self.rot_tracks.get(b)
            if not track:
                rots[b] = q_identity()
            elif i0 == i1:
                rots[b] = track[i0]
            else:
                rots[b] = q_slerp(track[i0], track[i1], alpha)

        l0 = self.lt_locs[i0]
        l1 = self.lt_locs[i1]
        lt_loc = (
            l0[0] + (l1[0] - l0[0]) * alpha,
            l0[1] + (l1[1] - l0[1]) * alpha,
            l0[2] + (l1[2] - l0[2]) * alpha,
        )
        non_lt: dict[str, Vec3] = {}
        for b, pts in self.non_lt_locs.items():
            p0, p1 = pts[i0], pts[i1]
            non_lt[b] = (
                p0[0] + (p1[0] - p0[0]) * alpha,
                p0[1] + (p1[1] - p0[1]) * alpha,
                p0[2] + (p1[2] - p0[2]) * alpha,
            )
        return rots, lt_loc, non_lt


@dataclass
class RuleFinding:
    rule_id: str
    severity: str  # "FAIL" | "WARN"
    kind: str      # "measured" | "heuristic" | "unverified_soft"
    message: str
    time_sec: float | None = None
    body_part: str | None = None
    measured_value: float | None = None
    threshold: float | None = None


@dataclass
class ClipValidationResult:
    clip_name: str
    source_path: str | None
    status: str  # "PASS" | "WARN" | "FAIL"
    duration_sec: float
    frame_count: int
    loop: bool
    is_face_only_or_static: bool
    peak_speed_studs_s: float
    peak_speed_part: str
    peak_speed_time_s: float
    peak_ang_vel_deg_s: float
    peak_ang_vel_part: str
    max_foot_depth_studs: float
    max_non_lt_trans_studs: float
    max_joint_angle_deg: float
    lowest_body_y_studs: float
    findings: list[RuleFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [asdict(f) for f in self.findings]
        return d


def _bone_local_to_rbx_studs(v: Vec3, scale: float) -> Vec3:
    return (-v[0] * scale, v[1] * scale, -v[2] * scale)


def _solve_rbx_world_transforms(
    rots_bl: dict[str, Quat],
    lt_loc_bl: Vec3,
    non_lt_locs_bl: dict[str, Vec3],
    r15_ref: dict[str, Any],
) -> tuple[dict[str, tuple[Vec3, Quat]], dict[str, Vec3]]:
    """Compute world (pos_studs, quat_rbx) for all 16 R15 joints and virtual extremity tips."""
    scale = float(r15_ref.get("studs_per_blender_meter", 3.8584))
    bone_meta = {b["name"]: b for b in r15_ref["bones"]}
    world: dict[str, tuple[Vec3, Quat]] = {"HumanoidRootPart": ((0.0, 0.0, 0.0), q_identity())}
    tips: dict[str, Vec3] = {}

    for meta in r15_ref["bones"]:
        name = meta["name"]
        parent = meta["parent"]
        if parent is None:
            continue
        p_pos, p_rot = world[parent]
        base_off = meta["rbx_studs_offset"]
        if name == "LowerTorso":
            trans_studs = _bone_local_to_rbx_studs(lt_loc_bl, scale)
        elif name in non_lt_locs_bl:
            trans_studs = _bone_local_to_rbx_studs(non_lt_locs_bl[name], scale)
        else:
            trans_studs = (0.0, 0.0, 0.0)

        rel_off = (base_off[0] + trans_studs[0], base_off[1] + trans_studs[1], base_off[2] + trans_studs[2])
        rot_off = q_rotate_vec(p_rot, rel_off)
        w_pos = (p_pos[0] + rot_off[0], p_pos[1] + rot_off[1], p_pos[2] + rot_off[2])
        q_local_rbx = blender_quat_to_roblox_quat(rots_bl.get(name, q_identity()))
        w_rot = q_norm(q_mul(p_rot, q_local_rbx))
        world[name] = (w_pos, w_rot)

        for tip_name, tip_local in meta.get("virtual_tips_rbx_local", {}).items():
            t_off = q_rotate_vec(w_rot, tip_local)
            tips[tip_name] = (w_pos[0] + t_off[0], w_pos[1] + t_off[1], w_pos[2] + t_off[2])

    return world, tips


def _foot_lowest_y_studs(foot_pos: Vec3, foot_rot: Quat, half_ext: Sequence[float]) -> float:
    hx, hy, hz = float(half_ext[0]), float(half_ext[1]), float(half_ext[2])
    min_y = foot_pos[1]
    for sx in (-hx, hx):
        for sy in (-hy, hy):
            for sz in (-hz, hz):
                off = q_rotate_vec(foot_rot, (sx, sy, sz))
                min_y = min(min_y, foot_pos[1] + off[1])
    return min_y


def _check_v12_sanity(clip: ClipData, findings: list[RuleFinding]) -> None:
    if len(clip.frames) < 2:
        findings.append(RuleFinding("V12", "FAIL", "measured", "Clip must contain at least 2 frames."))
        return
    for idx in range(1, len(clip.frames)):
        if clip.frames[idx] <= clip.frames[idx - 1]:
            findings.append(RuleFinding("V12", "FAIL", "measured", f"Non-monotonic keyframe time at index {idx}."))
            return
    for b in MAPPED_R15_BONE_NAMES:
        track = clip.rot_tracks.get(b, [])
        if len(track) != len(clip.frames):
            findings.append(RuleFinding("V12", "FAIL", "measured", f"Bone '{b}' track length {len(track)} != {len(clip.frames)}.", body_part=b))
            return
        for q in track:
            if any(not math.isfinite(x) for x in q):
                findings.append(RuleFinding("V12", "FAIL", "measured", f"NaN/Inf quaternion in bone '{b}'.", body_part=b))
                return
            mag = math.sqrt(sum(x * x for x in q))
            if abs(mag - 1.0) > 1e-2:
                findings.append(RuleFinding("V12", "FAIL", "measured", f"Non-unit quaternion magnitude {mag:.4f} in bone '{b}'.", body_part=b, measured_value=mag, threshold=1.0))
                return
    for loc in clip.lt_locs:
        if any(not math.isfinite(x) for x in loc):
            findings.append(RuleFinding("V12", "FAIL", "measured", "NaN/Inf in LowerTorso location."))
            return


def validate_clip(
    clip: ClipData,
    *,
    rules_cfg: dict[str, Any] | None = None,
    r15_ref: dict[str, Any] | None = None,
) -> ClipValidationResult:
    """Validate a single ClipData read-only against V02-V09, V11-V12."""
    rules_data = rules_cfg or load_roblox_rules()
    ref = r15_ref or load_r15_reference()
    rules = {r["id"]: r for r in rules_data["rules"]}
    findings: list[RuleFinding] = []

    _check_v12_sanity(clip, findings)

    dur = clip.duration_sec
    # V07 key spacing & clip duration
    v07_min_dt = float(rules["V07"].get("min_dt_sec", 1.0 / 70.0))
    if dur <= 0.0 or dur > 10.0:
        findings.append(RuleFinding("V07", "FAIL", "measured", f"Animation duration {dur:.4f}s outside (0, 10.0]s.", measured_value=dur, threshold=10.0))
    elif clip.fps > 0.0:
        dts = [(clip.frames[i] - clip.frames[i - 1]) / clip.fps for i in range(1, len(clip.frames))]
        min_dt = min(dts)
        if min_dt < v07_min_dt * 0.99:
            findings.append(RuleFinding("V07", "FAIL", "measured", f"Key spacing {min_dt:.5f}s < 1/70s ({v07_min_dt:.5f}s).", measured_value=min_dt, threshold=v07_min_dt))

    # V02 joint manipulation
    v02_thresh = float(rules["V02"]["threshold"])
    max_rot_motion = 0.0
    for b in MAPPED_R15_BONE_NAMES:
        tr = clip.rot_tracks.get(b, [])
        if tr:
            q0 = tr[0]
            for q in tr[1:]:
                d = q_angle_rad(q0, q)
                if d > max_rot_motion:
                    max_rot_motion = d
    is_static = max_rot_motion < v02_thresh
    if is_static:
        findings.append(RuleFinding("V02", "FAIL", "measured", "Curve Animation does not contain any joint manipulation (e.g., face-only or static).", measured_value=max_rot_motion, threshold=v02_thresh))

    # V06 non-LowerTorso translation & V08 joint angle from rest
    scale = float(ref.get("studs_per_blender_meter", 3.8584))
    max_non_lt_studs = 0.0
    max_non_lt_part = ""
    v06_limit = float(rules["V06"]["threshold"])
    for b, locs in clip.non_lt_locs.items():
        if b == "LowerTorso":
            continue
        for loc in locs:
            tx, ty, tz = _bone_local_to_rbx_studs(loc, scale)
            mag = math.sqrt(tx * tx + ty * ty + tz * tz)
            if mag > max_non_lt_studs:
                max_non_lt_studs = mag
                max_non_lt_part = b
    if max_non_lt_studs > v06_limit:
        findings.append(RuleFinding("V06", "FAIL", "measured", f"Only LowerTorso may translate; {max_non_lt_part} moves {max_non_lt_studs:.3f} studs (> {v06_limit:.2f}).", body_part=max_non_lt_part, measured_value=max_non_lt_studs, threshold=v06_limit))

    max_joint_deg = 0.0
    max_joint_part = ""
    v08_limit = float(rules["V08"]["threshold"])
    for b in MAPPED_R15_BONE_NAMES:
        for q in clip.rot_tracks.get(b, []):
            deg = math.degrees(q_angle_rad(q_identity(), q))
            if deg > max_joint_deg:
                max_joint_deg = deg
                max_joint_part = b
    if max_joint_deg > v08_limit:
        findings.append(RuleFinding("V08", "FAIL", "measured", f"Joint '{max_joint_part}' rotation {max_joint_deg:.1f} deg exceeds {v08_limit:.0f} deg.", body_part=max_joint_part, measured_value=max_joint_deg, threshold=v08_limit))

    # Evaluate kinematics across 30 fps and 70 fps (V03 speed, V04 ang vel, V05 floor, V09 bounds)
    bone_meta = {b["name"]: b for b in ref["bones"]}
    floor_rel_y = float(ref.get("roblox_floor_rel_hrp_y_studs", -2.7009))
    lf_ext = bone_meta["LeftFoot"].get("foot_half_extents_rbx", [0.45, 0.15, 0.50])
    rf_ext = bone_meta["RightFoot"].get("foot_half_extents_rbx", [0.45, 0.15, 0.50])

    peak_spd = 0.0
    peak_spd_part = ""
    peak_spd_t = 0.0
    peak_ang = 0.0
    peak_ang_part = ""
    peak_ang_t = 0.0
    max_foot_depth = -999.0
    max_foot_part = ""
    max_foot_t = 0.0
    lowest_y = 999.0
    lowest_part = ""
    lowest_t = 0.0
    max_dist = 0.0
    max_dist_part = ""
    max_dist_t = 0.0

    if dur > 0.0 and not any(f.rule_id == "V12" for f in findings):
        sample_rates = [float(x) for x in rules["V03"].get("sample_rates_fps", [30, 70])]
        for rate in sample_rates:
            dt = 1.0 / rate
            prev_pts: dict[str, Vec3] | None = None
            prev_rots: dict[str, Quat] | None = None
            t = 0.0
            while t <= dur + 1e-6:
                st = min(t, dur)
                rots_bl, lt_bl, nlt_bl = clip.sample_at_time(st)
                world, tips = _solve_rbx_world_transforms(rots_bl, lt_bl, nlt_bl, ref)

                all_pts: dict[str, Vec3] = {k: v[0] for k, v in world.items() if k != "HumanoidRootPart"}
                all_pts.update(tips)

                for pname, pos in all_pts.items():
                    if pos[1] < lowest_y:
                        lowest_y, lowest_part, lowest_t = pos[1], pname, st
                    dist = math.sqrt(pos[0] * pos[0] + pos[1] * pos[1] + pos[2] * pos[2])
                    if dist > max_dist:
                        max_dist, max_dist_part, max_dist_t = dist, pname, st

                lf_min_y = _foot_lowest_y_studs(world["LeftFoot"][0], world["LeftFoot"][1], lf_ext)
                rf_min_y = _foot_lowest_y_studs(world["RightFoot"][0], world["RightFoot"][1], rf_ext)
                for fname, fy in (("LeftFoot", lf_min_y), ("RightFoot", rf_min_y)):
                    depth = floor_rel_y - fy
                    if depth > max_foot_depth:
                        max_foot_depth, max_foot_part, max_foot_t = depth, fname, st

                cur_rots = {k: v[1] for k, v in world.items() if k != "HumanoidRootPart"}
                if prev_pts is not None and prev_rots is not None:
                    for pname, pos in all_pts.items():
                        pp = prev_pts[pname]
                        spd = math.sqrt((pos[0] - pp[0]) ** 2 + (pos[1] - pp[1]) ** 2 + (pos[2] - pp[2]) ** 2) / dt
                        if spd > peak_spd:
                            peak_spd, peak_spd_part, peak_spd_t = spd, pname, st
                    for jname, qr in cur_rots.items():
                        ang = math.degrees(q_angle_rad(qr, prev_rots[jname])) / dt
                        if ang > peak_ang:
                            peak_ang, peak_ang_part, peak_ang_t = ang, jname, st

                prev_pts = all_pts
                prev_rots = cur_rots
                t += dt
    else:
        max_foot_depth = 0.0
        lowest_y = 0.0

    v03_hard = float(rules["V03"]["threshold"])
    v03_target = float(rules["V03"].get("auto_fix_target", 45.0))
    if peak_spd > v03_hard:
        findings.append(RuleFinding("V03", "FAIL", "measured", f"Body point '{peak_spd_part}' speed {peak_spd:.2f} studs/s exceeds {v03_hard:.2f} max.", time_sec=peak_spd_t, body_part=peak_spd_part, measured_value=peak_spd, threshold=v03_hard))
    elif peak_spd > v03_target:
        findings.append(RuleFinding("V03", "WARN", "measured", f"Body point '{peak_spd_part}' speed {peak_spd:.2f} studs/s exceeds 45.0 safety margin (hard limit 51.0).", time_sec=peak_spd_t, body_part=peak_spd_part, measured_value=peak_spd, threshold=v03_target))

    v04_limit = float(rules["V04"]["threshold"])
    if peak_ang > v04_limit:
        findings.append(RuleFinding("V04", "WARN", "heuristic", f"Joint '{peak_ang_part}' angular velocity {peak_ang:.0f} deg/s exceeds heuristic {v04_limit:.0f} deg/s.", time_sec=peak_ang_t, body_part=peak_ang_part, measured_value=peak_ang, threshold=v04_limit))

    v05_limit = float(rules["V05"]["threshold"])
    if max_foot_depth > v05_limit:
        findings.append(RuleFinding("V05", "FAIL", "measured", f"{max_foot_part} dips {max_foot_depth:.3f} studs below floor at t={max_foot_t:.2f}s (max {v05_limit:.2f}).", time_sec=max_foot_t, body_part=max_foot_part, measured_value=max_foot_depth, threshold=v05_limit))

    v09_min_y = float(rules["V09"]["threshold"])
    v09_max_d = float(rules["V09"].get("max_distance", 25.0))
    if lowest_y < v09_min_y:
        findings.append(RuleFinding("V09", "WARN", "unverified_soft", f"Body point '{lowest_part}' at Y={lowest_y:.2f} < {v09_min_y:.1f} studs from HRP.", time_sec=lowest_t, body_part=lowest_part, measured_value=lowest_y, threshold=v09_min_y))
    if max_dist > v09_max_d:
        findings.append(RuleFinding("V09", "WARN", "unverified_soft", f"Body point '{max_dist_part}' distance {max_dist:.2f} > {v09_max_d:.1f} studs.", time_sec=max_dist_t, body_part=max_dist_part, measured_value=max_dist, threshold=v09_max_d))

    # V11 Loop seam quality
    if clip.loop and len(clip.frames) >= 4:
        eps = float(rules["V11"]["threshold"])
        max_seam_rad = max(q_angle_rad(clip.rot_tracks[b][0], clip.rot_tracks[b][-1]) for b in MAPPED_R15_BONE_NAMES if b in clip.rot_tracks)
        loc_diff = math.sqrt(sum((clip.lt_locs[-1][i] - clip.lt_locs[0][i]) ** 2 for i in range(3)))
        if max_seam_rad > eps or loc_diff > eps:
            findings.append(RuleFinding("V11", "WARN", "measured", f"Loop seam mismatch (rot={math.degrees(max_seam_rad):.2f} deg, loc={loc_diff:.3f}).", measured_value=max_seam_rad, threshold=eps))

    has_fail = any(f.severity == "FAIL" for f in findings)
    has_warn = any(f.severity == "WARN" for f in findings)
    status = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")

    return ClipValidationResult(
        clip_name=clip.name,
        source_path=clip.source_path,
        status=status,
        duration_sec=round(dur, 4),
        frame_count=len(clip.frames),
        loop=clip.loop,
        is_face_only_or_static=is_static,
        peak_speed_studs_s=round(peak_spd, 2),
        peak_speed_part=peak_spd_part,
        peak_speed_time_s=round(peak_spd_t, 3),
        peak_ang_vel_deg_s=round(peak_ang, 1),
        peak_ang_vel_part=peak_ang_part,
        max_foot_depth_studs=round(max_foot_depth, 3),
        max_non_lt_trans_studs=round(max_non_lt_studs, 4),
        max_joint_angle_deg=round(max_joint_deg, 1),
        lowest_body_y_studs=round(lowest_y, 3),
        findings=findings,
    )


def write_validation_reports(
    results: Sequence[ClipValidationResult],
    json_out: Path,
    html_out: Path | None = None,
) -> dict[str, Any]:
    """Write machine-readable JSON and self-contained HTML validation reports."""
    counts = {
        "total": len(results),
        "PASS": sum(1 for r in results if r.status == "PASS"),
        "WARN": sum(1 for r in results if r.status == "WARN"),
        "FAIL": sum(1 for r in results if r.status == "FAIL"),
    }
    payload = {"summary": counts, "clips": [r.to_dict() for r in results]}
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if html_out is not None:
        rows = []
        for r in results:
            badge_cls = r.status.lower()
            notes = "<br>".join(
                f"<strong>[{html.escape(f.rule_id)} {html.escape(f.severity)}]</strong> {html.escape(f.message)}"
                for f in r.findings
            ) or "All checked rules passed."
            rows.append(
                f"<tr><td>{html.escape(r.clip_name)}</td>"
                f"<td><span class='badge {badge_cls}'>{r.status}</span></td>"
                f"<td>{r.duration_sec:.3f}s ({r.frame_count}f)</td>"
                f"<td>{r.peak_speed_studs_s:.2f} ({html.escape(r.peak_speed_part)})</td>"
                f"<td>{r.peak_ang_vel_deg_s:.0f} ({html.escape(r.peak_ang_vel_part)})</td>"
                f"<td>{r.max_foot_depth_studs:.3f}</td>"
                f"<td>{notes}</td></tr>"
            )
        doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>R15 AnimForge Validation Report</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#11161d;color:#e7edf5;margin:24px}}
.summary{{display:flex;gap:16px;margin-bottom:20px}}
.card{{background:#1a222d;padding:12px 18px;border-radius:8px;border:1px solid #2b3748}}
table{{width:100%;border-collapse:collapse;background:#171e27;font-size:13px}}
th,td{{padding:8px 10px;border-bottom:1px solid #283242;text-align:left;vertical-align:top}}
th{{background:#1f2937;position:sticky;top:0}}
.badge{{padding:2px 8px;border-radius:4px;font-weight:700;font-size:11px}}
.pass{{background:#14532d;color:#bbf7d0}}
.warn{{background:#713f12;color:#fef08a}}
.fail{{background:#7f1d1d;color:#fecaca}}
</style></head><body>
<h1>R15 AnimForge — Roblox Upload Validation Report</h1>
<div class="summary">
<div class="card">Total: <strong>{counts['total']}</strong></div>
<div class="card">PASS: <strong>{counts['PASS']}</strong></div>
<div class="card">WARN: <strong>{counts['WARN']}</strong></div>
<div class="card">FAIL: <strong>{counts['FAIL']}</strong></div>
</div>
<table><thead><tr><th>Clip</th><th>Status</th><th>Length</th><th>Peak Speed (studs/s)</th><th>Peak Ang Vel (deg/s)</th><th>Max Foot Depth (studs)</th><th>Findings</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""
        html_out.parent.mkdir(parents=True, exist_ok=True)
        html_out.write_text(doc, encoding="utf-8")
    return payload
