"""Unit tests for procedural builders (idle loop, alt idle look, jump, fall)."""

from __future__ import annotations

import math

from animforge.builders import build_alt_idle_look, build_idle_loop, build_jump_and_fall
from animforge.config import load_profile
from animforge.constants import MAPPED_R15_BONE_NAMES
from animforge.math_retarget import euler_to_quat, q_angle_rad, q_identity
from animforge.validation import ClipData, validate_clip


def _synth_src_clip(name: str, n_frames: int, yaw_deg: float = 10.0, pitch_deg: float = 15.0) -> ClipData:
    frames = [float(i) for i in range(n_frames)]
    rots = {b: [] for b in MAPPED_R15_BONE_NAMES}
    locs = []
    for i in range(n_frames):
        a = math.sin(2.0 * math.pi * i / max(1, n_frames - 1))
        for b in MAPPED_R15_BONE_NAMES:
            if b == "UpperTorso":
                rots[b].append(euler_to_quat(math.radians(pitch_deg * a), math.radians(yaw_deg), 0.0, "YXZ"))
            elif b == "LowerTorso":
                rots[b].append(euler_to_quat(0.0, math.radians(-45.0 + 3.0 * a), 0.0, "YXZ"))
            elif b == "RightHand":
                rots[b].append(euler_to_quat(math.radians(25.0), 0.0, 0.0, "YXZ"))
            else:
                rots[b].append(q_identity())
        locs.append((0.0, 0.02 * a, 0.0))
    return ClipData(name=name, fps=30.0, frames=frames, rot_tracks=rots, lt_locs=locs, loop=True)


def test_procedural_builders_seams_and_continuity() -> None:
    profile = load_profile()
    st = _synth_src_clip("4rhn01_bas_nut_st", 62)
    nut = _synth_src_clip("4rhn01_bas_nut", 62, pitch_deg=6.0)
    nj = _synth_src_clip("4rhn01_bas_nut_jump", 5)
    for leg in ("LeftUpperLeg", "RightUpperLeg"):
        nj.rot_tracks[leg] = [euler_to_quat(math.radians(-45.0), 0.0, 0.0, "YXZ")] * 5
    for shin in ("LeftLowerLeg", "RightLowerLeg"):
        nj.rot_tracks[shin] = [euler_to_quat(math.radians(90.0), 0.0, 0.0, "YXZ")] * 5
    bj = _synth_src_clip("4rhn01_bas_jump", 22, pitch_deg=30.0)
    for leg in ("LeftUpperLeg", "RightUpperLeg"):
        bj.rot_tracks[leg] = [euler_to_quat(math.radians(-45.0), 0.0, 0.0, "YXZ")] * 22
    for shin in ("LeftLowerLeg", "RightLowerLeg"):
        bj.rot_tracks[shin] = [euler_to_quat(math.radians(90.0), 0.0, 0.0, "YXZ")] * 22
    lp = _synth_src_clip("4rhn01_bas_jump_lp", 12, pitch_deg=10.0)

    idle = build_idle_loop(st, nut, profile)
    assert len(idle.frames) == 91
    assert idle.loop is True
    for b in MAPPED_R15_BONE_NAMES:
        assert math.degrees(q_angle_rad(idle.rot_tracks[b][0], idle.rot_tracks[b][-1])) < 1e-4

    alt = build_alt_idle_look(st, nut, profile)
    assert len(alt.frames) == 121
    for b in MAPPED_R15_BONE_NAMES:
        assert math.degrees(q_angle_rad(alt.rot_tracks[b][0], alt.rot_tracks[b][-1])) < 1e-4

    jump, fall = build_jump_and_fall(st, nj, bj, lp, profile)
    assert len(jump.frames) == 36 and jump.loop is False
    assert len(fall.frames) == 36 and fall.loop is True

    # Jump f0 equals idle f0; Fall f0 == Fall f35; Fall f0 == lp[0] apex (before sine sway vanishes at phase=0)
    for b in MAPPED_R15_BONE_NAMES:
        assert math.degrees(q_angle_rad(jump.rot_tracks[b][0], idle.rot_tracks[b][0])) < 1e-4
        assert math.degrees(q_angle_rad(jump.rot_tracks[b][-1], fall.rot_tracks[b][0])) < 1e-4
        assert math.degrees(q_angle_rad(fall.rot_tracks[b][0], fall.rot_tracks[b][-1])) < 1e-4

    for clip in (idle, alt, fall):
        res = validate_clip(clip)
        assert res.status != "FAIL", (clip.name, [f.message for f in res.findings])

    j_res = validate_clip(jump)
    assert j_res.peak_speed_studs_s <= 45.0, j_res.findings
