"""Headless Blender 5.3 worker script invoked via `--background --factory-startup --python-exit-code 1`.

Streams structured events as lines prefixed with `@@EVT ` to stdout.
Never imports `animforge.validation` or `animforge.fixer` when running `convert`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import bpy  # type: ignore
from mathutils import Quaternion, Vector  # type: ignore


def emit(evt_type: str, **payload: Any) -> None:
    rec = {"type": evt_type, **payload}
    print("@@EVT " + json.dumps(rec), flush=True)


def _is_xfbin_registered() -> bool:
    return hasattr(bpy.types, "IMPORT_SCENE_OT_xfbin")


def register_xfbin_importer(addon_dir: str | None) -> None:
    if _is_xfbin_registered():
        return
    if addon_dir:
        p = Path(addon_dir)
        mod_name = p.name
        try:
            bpy.ops.preferences.addon_enable(module=mod_name)
            if _is_xfbin_registered():
                return
        except Exception:
            pass
        init_py = p / "__init__.py"
        if init_py.is_file():
            import importlib.util

            parent_s = str(p.parent)
            if parent_s not in sys.path:
                sys.path.insert(0, parent_s)
            spec = importlib.util.spec_from_file_location("blender_xfbin_importer_bundled", str(init_py), submodule_search_locations=[str(p)])
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["blender_xfbin_importer_bundled"] = mod
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register()
    if not _is_xfbin_registered():
        raise RuntimeError(f"import_scene.xfbin operator unavailable (addon_dir={addon_dir})")


def import_xfbin_file(filepath: str) -> None:
    bpy.ops.import_scene.xfbin(
        directory=os.path.dirname(filepath),
        files=[{"name": os.path.basename(filepath)}],
    )


def get_matching_slot(action: Any, arm_name: str, charcode: str) -> Any:
    if not hasattr(action, "slots"):
        return None
    for s in action.slots:
        if s.target_id_type == "OBJECT" and (arm_name in s.name_display or charcode in s.name_display):
            return s
    for s in action.slots:
        if s.target_id_type == "OBJECT":
            return s
    return None


def action_has_fcurves(action: Any, slot: Any) -> bool:
    if hasattr(action, "fcurves"):
        return len(action.fcurves) > 0
    if not getattr(action, "layers", None) or not slot:
        return False
    for layer in action.layers:
        for strip in layer.strips:
            cb = strip.channelbag(slot)
            if cb and len(cb.fcurves) > 0:
                return True
    return False


def create_r15_armature(r15_ref: dict[str, Any]) -> Any:
    arm_data = bpy.data.armatures.new("R15_Rig_Data")
    arm_obj = bpy.data.objects.new("R15_Rig", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bones: dict[str, Any] = {}
    for bmeta in r15_ref["bones"]:
        b = arm_data.edit_bones.new(bmeta["name"])
        b.head = Vector(bmeta["head"])
        b.tail = Vector(bmeta["tail"])
        b.roll = float(bmeta.get("roll", 0.0))
        parent = bmeta.get("parent")
        if parent and parent in bones:
            b.parent = bones[parent]
        bones[bmeta["name"]] = b
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm_obj


def bake_clip_to_r15_action(
    r15_arm: Any,
    action_name: str,
    frames: list[float],
    rot_tracks: dict[str, list[tuple[float, float, float, float]]],
    loc_track: list[tuple[float, float, float]],
    ordered_bones: list[str],
) -> Any:
    r15_arm.animation_data_create()
    act = bpy.data.actions.new(name=action_name)
    r15_arm.animation_data.action = act
    if hasattr(act, "slots"):
        slot = act.slots.new(id_type="OBJECT", name=r15_arm.name)
        r15_arm.animation_data.action_slot = slot
        layer = act.layers.new("Layer") if not act.layers else act.layers[0]
        strip = layer.strips.new(type="KEYFRAME") if not layer.strips else layer.strips[0]
        cb = strip.channelbag(slot) or strip.channelbags.new(slot)
        fcurves = cb.fcurves
    else:
        fcurves = act.fcurves

    n = len(frames)
    for idx, val in enumerate((1.0, 0.0, 0.0, 0.0)):
        fc = fcurves.new(data_path='pose.bones["HumanoidRootPart"].rotation_quaternion', index=idx)
        fc.keyframe_points.add(2)
        fc.keyframe_points.foreach_set("co", [frames[0], val, frames[-1], val])
        fc.update()

    for idx in range(3):
        fc = fcurves.new(data_path='pose.bones["LowerTorso"].location', index=idx)
        fc.keyframe_points.add(n)
        fc.keyframe_points.foreach_set("co", [x for f, v in zip(frames, loc_track) for x in (f, v[idx])])
        fc.update()

    for b in ordered_bones:
        quats = rot_tracks[b]
        dp = f'pose.bones["{b}"].rotation_quaternion'
        for idx in range(4):
            fc = fcurves.new(data_path=dp, index=idx)
            fc.keyframe_points.add(n)
            fc.keyframe_points.foreach_set("co", [x for f, q in zip(frames, quats) for x in (f, q[idx])])
            fc.update()
    return act


def export_r15_fbx(r15_arm: Any, out_path: str, frame_start: int, frame_end: int, export_flags: dict[str, Any]) -> None:
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    for pb in r15_arm.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)

    bpy.ops.object.select_all(action="DESELECT")
    r15_arm.select_set(True)
    bpy.context.view_layer.objects.active = r15_arm

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=out_path,
        use_selection=bool(export_flags.get("use_selection", True)),
        object_types=set(export_flags.get("object_types", ["ARMATURE"])),
        global_scale=float(export_flags.get("global_scale", 0.01)),
        add_leaf_bones=bool(export_flags.get("add_leaf_bones", False)),
        bake_anim=bool(export_flags.get("bake_anim", True)),
        bake_anim_use_all_actions=bool(export_flags.get("bake_anim_use_all_actions", False)),
        bake_anim_use_nla_strips=bool(export_flags.get("bake_anim_use_nla_strips", False)),
        axis_forward=str(export_flags.get("axis_forward", "-Z")),
        axis_up=str(export_flags.get("axis_up", "Y")),
        bake_anim_simplify_factor=float(export_flags.get("bake_anim_simplify_factor", 1.0)),
    )


def _find_source_armature(preferred_clump: str) -> Any:
    pref = preferred_clump.lower()
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    # 1. Preferred clump with full skeleton (>= 12 bones) and not face rig (_f)
    for obj in arms:
        if pref and pref in obj.name.lower() and "_f" not in obj.name.lower() and len(obj.data.bones) >= 12:
            return obj
    # 2. Any body armature (bod in name, not _f, >= 12 bones)
    for obj in arms:
        if "bod" in obj.name.lower() and "_f" not in obj.name.lower() and len(obj.data.bones) >= 12:
            return obj
    # 3. Any non-face armature with >= 12 bones
    for obj in arms:
        if "_f" not in obj.name.lower() and len(obj.data.bones) >= 12:
            return obj
    return arms[0] if arms else None


def _load_worker_profile(args: dict[str, Any], app_root: Path) -> Any:
    from animforge.config import load_profile, profile_from_dict

    payload = args.get("profile_payload")
    if isinstance(payload, dict):
        p = Path(args["profile_path"]) if args.get("profile_path") else None
        return profile_from_dict(payload, path=p)
    return load_profile(args.get("profile_path"), app_root=app_root)


def _resolve_action_name(target: str, available_names: list[str], charcode: str, role: str = "") -> str | None:
    from animforge.action_matcher import resolve_action_for_role

    res = resolve_action_for_role(target, role, available_names, charcode=charcode)
    return res.source


def _sanitize_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in name)


def _action_motion_is_face_only(rot_tracks: dict[str, list[tuple[float, float, float, float]]], q_angle_fn: Any) -> bool:
    max_ang = 0.0
    for track in rot_tracks.values():
        if not track:
            continue
        q0 = track[0]
        for q in track[1:]:
            d = q_angle_fn(q0, q)
            if d > max_ang:
                max_ang = d
    return max_ang < 0.001


def _extract_source_tracks_for_action(
    source_arm: Any,
    action: Any,
    charcode: str,
    mapped_bones: dict[str, str],
    src_rest_quat: dict[str, tuple[float, float, float, float]],
    src_pelvis_rest: Any,
    pelvis_scale: float,
    r15_mat3_inv: Any,
    rest_offsets: dict[str, tuple[float, float, float, float]],
    parent_map: dict[str, str],
    ordered_bones: list[str],
    r15_root_quat: tuple[float, float, float, float],
    retarget_fn: Any,
    enforce_fn: Any,
    cleanup_toggles: dict[str, bool],
    cleanup_params: dict[str, Any],
    cleanup_fn: Any,
) -> tuple[list[float], dict[str, list[tuple[float, float, float, float]]], list[tuple[float, float, float]], list[str]]:
    slot = get_matching_slot(action, source_arm.name, charcode)
    source_arm.animation_data.action = action
    if slot:
        source_arm.animation_data.action_slot = slot

    frame_start = int(action.frame_range[0])
    frame_end = int(action.frame_range[1])
    frames = [float(f) for f in range(frame_start, frame_end + 1)]

    raw_poses: list[dict[str, tuple[float, float, float, float]]] = []
    loc_track: list[tuple[float, float, float]] = []
    cleanup_applied: set[str] = set()

    for f in range(frame_start, frame_end + 1):
        bpy.context.scene.frame_set(f)
        pose_world = {
            rb: tuple(source_arm.pose.bones[mapped_bones[rb]].matrix.to_quaternion())
            for rb in ordered_bones
        }
        locals_q = retarget_fn(
            pose_world,
            src_rest_quat,
            rest_offsets,
            parent_map,
            ordered_bones,
            r15_root_quat,
        )
        if any(cleanup_toggles.values()):
            locals_q, applied = cleanup_fn(locals_q, cleanup_toggles, cleanup_params)
            cleanup_applied.update(applied)
        raw_poses.append(locals_q)

        pelvis_pose_loc = source_arm.pose.bones[mapped_bones["LowerTorso"]].matrix.translation
        delta_world = (pelvis_pose_loc - src_pelvis_rest) * pelvis_scale
        delta_local = r15_mat3_inv @ delta_world
        loc_track.append((float(delta_local.x), float(delta_local.y), float(delta_local.z)))

    rot_tracks = {b: enforce_fn([p[b] for p in raw_poses]) for b in ordered_bones}
    return frames, rot_tracks, loc_track, sorted(cleanup_applied)


def handle_convert(args: dict[str, Any]) -> None:
    from animforge.cleanup import apply_cleanup_pipeline
    from animforge.config import load_r15_reference
    from animforge.math_retarget import (
        build_rest_offset_quats,
        enforce_hemisphere_continuity,
        map_skeleton_bones,
        q_angle_rad,
        retarget_frame_poses,
    )

    app_root = Path(args["app_root"])
    profile = _load_worker_profile(args, app_root)
    r15_ref = load_r15_reference(app_root=app_root)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    register_xfbin_importer(args.get("addon_dir"))
    bpy.context.scene.render.fps = 30

    import_xfbin_file(args["body_archive"])
    for af in args["anim_archives"]:
        try:
            import_xfbin_file(af)
        except Exception as exc:
            emit("warning", message=f"Failed importing {af}: {exc}")

    source_arm = _find_source_armature(profile.discovery.get("preferred_body_clump", profile.charcode))
    if source_arm is None:
        raise RuntimeError("No source armature found after importing body XFBIN.")

    out_dir = Path(args["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    mapped_bones = map_skeleton_bones(
        [b.name for b in source_arm.data.bones],
        profile,
        report_out_path=out_dir / "bone_map_report.json",
    )

    r15_arm = create_r15_armature(r15_ref)
    ordered_bones = [b["name"] for b in r15_ref["bones"] if b.get("parent") is not None]
    parent_map = {b["name"]: b["parent"] for b in r15_ref["bones"] if b.get("parent") is not None}

    src_rest_quat = {
        r15_b: tuple(source_arm.data.bones[cc2_b].matrix_local.to_quaternion())
        for r15_b, cc2_b in mapped_bones.items()
    }
    src_pelvis_rest = source_arm.data.bones[mapped_bones["LowerTorso"]].matrix_local.translation.copy()
    r15_root_quat = tuple(r15_arm.data.bones["HumanoidRootPart"].matrix_local.to_quaternion())
    r15_mat3_inv = r15_arm.data.bones["LowerTorso"].matrix_local.to_3x3().inverted()
    pelvis_h = float(r15_ref.get("pelvis_rest_height_blender", 0.70))
    pelvis_scale = pelvis_h / max(float(src_pelvis_rest.z), 0.01)
    rest_offsets = build_rest_offset_quats(profile)

    if not source_arm.animation_data:
        source_arm.animation_data_create()
    if not r15_arm.animation_data:
        r15_arm.animation_data_create()

    # Snapshot source actions ONCE before any bake loop!
    snapshot_actions = list(bpy.data.actions)
    selected_names = set(args.get("action_filter") or [])
    chunk_start = int(args.get("chunk_start", 0))
    chunk_end = int(args.get("chunk_end", len(snapshot_actions)))
    cache_map: dict[str, str] = args.get("resume_cache", {})
    settings_hash = str(args.get("settings_hash", ""))
    cleanup_toggles = dict(args.get("cleanup_toggles") or profile.cleanup_defaults_convert)
    export_flags = dict(r15_ref.get("fbx_export_defaults", {}))
    prefix = str(profile.output_prefix)

    # Filter exportable actions deterministically first so worker slices align identically
    exportable: list[tuple[Any, str]] = []
    for act in snapshot_actions:
        slot = get_matching_slot(act, source_arm.name, profile.charcode)
        if not action_has_fcurves(act, slot):
            emit("action_skipped", action=act.name, reason="no_fcurves")
            continue
        fs, fe = int(act.frame_range[0]), int(act.frame_range[1])
        if fe <= fs:
            emit("action_skipped", action=act.name, reason="non_positive_duration")
            continue
        if selected_names and act.name not in selected_names:
            continue
        safe = _sanitize_name(act.name)
        exportable.append((act, f"{prefix}{safe}.fbx"))

    emit("worker_plan", total_exportable=len(exportable), slice_start=chunk_start, slice_end=min(chunk_end, len(exportable)))

    for idx in range(chunk_start, min(chunk_end, len(exportable))):
        act, out_filename = exportable[idx]
        fbx_out = out_dir / out_filename
        action_key = f"{act.name}|{settings_hash}"
        if cache_map.get(out_filename) == action_key and fbx_out.is_file():
            emit("action_cached", action=act.name, file=str(fbx_out), frames=int(act.frame_range[1]) - int(act.frame_range[0]))
            continue

        try:
            fs, fe = int(act.frame_range[0]), int(act.frame_range[1])
            frames, rot_tracks, loc_track, cleanup_log = _extract_source_tracks_for_action(
                source_arm, act, profile.charcode, mapped_bones, src_rest_quat, src_pelvis_rest,
                pelvis_scale, r15_mat3_inv, rest_offsets, parent_map, ordered_bones, r15_root_quat,
                retarget_frame_poses, enforce_hemisphere_continuity, cleanup_toggles, profile.cleanup_params, apply_cleanup_pipeline,
            )
            face_only = _action_motion_is_face_only(rot_tracks, q_angle_rad)

            baked = bake_clip_to_r15_action(r15_arm, f"R15_{_sanitize_name(act.name)}", frames, rot_tracks, loc_track, ordered_bones)
            export_r15_fbx(r15_arm, str(fbx_out), fs, fe, export_flags)
            r15_arm.animation_data.action = None
            bpy.data.actions.remove(baked)

            emit(
                "action_done",
                action=act.name,
                file=str(fbx_out),
                filename=out_filename,
                frames=fe - fs,
                frame_start=fs,
                frame_end=fe,
                face_only=face_only,
                cleanup_applied=cleanup_log,
                cache_key=action_key,
            )
        except Exception as exc:
            emit("action_error", action=act.name, file=str(fbx_out), error=str(exc))


def handle_build_procedural(args: dict[str, Any]) -> None:
    from animforge.builders import build_alt_idle_look, build_idle_loop, build_jump_and_fall
    from animforge.config import load_r15_reference
    from animforge.math_retarget import build_rest_offset_quats, enforce_hemisphere_continuity, map_skeleton_bones, retarget_frame_poses
    from animforge.validation import ClipData

    app_root = Path(args["app_root"])
    profile = _load_worker_profile(args, app_root)
    r15_ref = load_r15_reference(app_root=app_root)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    register_xfbin_importer(args.get("addon_dir"))
    bpy.context.scene.render.fps = 30
    import_xfbin_file(args["body_archive"])
    for af in args["anim_archives"]:
        try:
            import_xfbin_file(af)
        except Exception as exc:
            emit("warning", message=f"Failed importing {af}: {exc}")

    source_arm = _find_source_armature(profile.discovery.get("preferred_body_clump", profile.charcode))
    if source_arm is None:
        raise RuntimeError("No source armature found.")

    out_dir = Path(args["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    mapped_bones = map_skeleton_bones([b.name for b in source_arm.data.bones], profile, report_out_path=out_dir / "bone_map_report.json")
    r15_arm = create_r15_armature(r15_ref)
    ordered_bones = [b["name"] for b in r15_ref["bones"] if b.get("parent") is not None]
    parent_map = {b["name"]: b["parent"] for b in r15_ref["bones"] if b.get("parent") is not None}

    src_rest_quat = {r15_b: tuple(source_arm.data.bones[cc2_b].matrix_local.to_quaternion()) for r15_b, cc2_b in mapped_bones.items()}
    src_pelvis_rest = source_arm.data.bones[mapped_bones["LowerTorso"]].matrix_local.translation.copy()
    r15_root_quat = tuple(r15_arm.data.bones["HumanoidRootPart"].matrix_local.to_quaternion())
    r15_mat3_inv = r15_arm.data.bones["LowerTorso"].matrix_local.to_3x3().inverted()
    pelvis_scale = float(r15_ref.get("pelvis_rest_height_blender", 0.70)) / max(float(src_pelvis_rest.z), 0.01)
    rest_offsets = build_rest_offset_quats(profile)

    source_arm.animation_data_create()
    r15_arm.animation_data_create()

    avail_names = [a.name for a in bpy.data.actions]
    clip_cache: dict[str, ClipData] = {}

    def _load_src_clip(name: str, role: str = "") -> ClipData:
        resolved_name = _resolve_action_name(name, avail_names, profile.charcode, role=role)
        if not resolved_name:
            raise KeyError(f"Source action '{name}' ({role}) not found in imported archives.")
        if resolved_name in clip_cache:
            return clip_cache[resolved_name]
        act = bpy.data.actions[resolved_name]
        frames, rot_tracks, loc_track, _ = _extract_source_tracks_for_action(
            source_arm, act, profile.charcode, mapped_bones, src_rest_quat, src_pelvis_rest,
            pelvis_scale, r15_mat3_inv, rest_offsets, parent_map, ordered_bones, r15_root_quat,
            retarget_frame_poses, enforce_hemisphere_continuity, {}, {}, lambda x, t, p: (x, []),
        )
        c = ClipData(name=resolved_name, fps=30.0, frames=frames, rot_tracks=rot_tracks, lt_locs=loc_track)
        clip_cache[resolved_name] = c
        return c

    builders_cfg = profile.procedural_builders
    align_ut = bool(args.get("upper_torso_align", False))
    smooth_jump = bool(args.get("smooth_jump_launch", True))
    requested = set(args.get("builders") or ["idle_loop", "alt_idle_look", "jump", "fall"])

    outputs: list[ClipData] = []
    st_clip: ClipData | None = None
    nut_clip: ClipData | None = None
    if "idle_loop" in requested or "alt_idle_look" in requested or "jump" in requested:
        try:
            st_clip = _load_src_clip(builders_cfg["idle_loop"]["base_action"], role="base_action")
        except Exception as exc:
            emit("warning", message=f"Idle base clip unavailable: {exc}")
        try:
            nut_clip = _load_src_clip(builders_cfg["idle_loop"]["breathing_action"], role="breathing_action")
        except Exception as exc:
            emit("warning", message=f"Idle breathing clip unavailable: {exc}")

    if "idle_loop" in requested and (st_clip or nut_clip):
        outputs.append(build_idle_loop(st_clip or nut_clip, nut_clip or st_clip, profile, apply_upper_torso=align_ut))  # type: ignore[arg-type]
    if "alt_idle_look" in requested and (st_clip or nut_clip):
        outputs.append(build_alt_idle_look(st_clip or nut_clip, nut_clip or st_clip, profile, apply_upper_torso=align_ut))  # type: ignore[arg-type]
    if "jump" in requested or "fall" in requested:
        try:
            lp_clip = _load_src_clip(builders_cfg["fall"]["hang_action"], role="hang_action")
            if "jump" in requested:
                bj_clip = _load_src_clip(builders_cfg["jump"]["launch_action"], role="launch_action")
                try:
                    nj_clip = _load_src_clip(builders_cfg["jump"]["crouch_action"], role="crouch_action")
                except KeyError:
                    nj_clip = bj_clip
                base_for_jump = st_clip or nut_clip or nj_clip
                j_clip, f_clip = build_jump_and_fall(
                    base_for_jump, nj_clip, bj_clip, lp_clip, profile,
                    apply_upper_torso_on_jump=align_ut,
                    smooth_jump_launch=smooth_jump,
                )
                outputs.append(j_clip)
                if "fall" in requested:
                    outputs.append(f_clip)
            elif "fall" in requested:
                # Build fall standalone cleanly from hang_action even if launch/idle aren't present
                try:
                    bj_clip = _load_src_clip(builders_cfg["jump"]["launch_action"], role="launch_action")
                except KeyError:
                    bj_clip = lp_clip
                base_for_fall = st_clip or nut_clip or bj_clip
                _, f_clip = build_jump_and_fall(
                    base_for_fall, bj_clip, bj_clip, lp_clip, profile,
                    apply_upper_torso_on_jump=align_ut,
                    smooth_jump_launch=smooth_jump,
                )
                outputs.append(f_clip)
        except Exception as exc:
            emit("warning", message=f"Jump/Fall builder skipped: {exc}")

    export_flags = dict(r15_ref.get("fbx_export_defaults", {}))
    for clip in outputs:
        fbx_out = out_dir / f"{clip.name}.fbx"
        baked = bake_clip_to_r15_action(r15_arm, clip.name, clip.frames, clip.rot_tracks, clip.lt_locs, ordered_bones)
        export_r15_fbx(r15_arm, str(fbx_out), int(clip.frames[0]), int(clip.frames[-1]), export_flags)
        r15_arm.animation_data.action = None
        bpy.data.actions.remove(baked)
        emit("builder_done", builder=clip.name, file=str(fbx_out), frames=len(clip.frames) - 1, loop=clip.loop)


def handle_extract_fbx_clips(args: dict[str, Any]) -> None:
    from animforge.config import load_r15_reference
    from animforge.math_retarget import enforce_hemisphere_continuity

    app_root = Path(args["app_root"])
    r15_ref = load_r15_reference(app_root=app_root)
    ordered_bones = [b["name"] for b in r15_ref["bones"] if b.get("parent") is not None]
    out_dir = Path(args["temp_clip_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    for item in args["fbx_files"]:
        fbx_path = Path(item)
        try:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            bpy.context.scene.render.fps = 30
            bpy.ops.import_scene.fbx(filepath=str(fbx_path))
            fps = float(bpy.context.scene.render.fps or 30.0)
            arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
            if arm is None or not arm.animation_data or not arm.animation_data.action:
                emit("extract_error", file=str(fbx_path), error="No armature or action in FBX")
                continue

            fr = arm.animation_data.action.frame_range
            f0, f1 = int(round(fr[0])), int(round(fr[1]))
            frames = [float(f) for f in range(f0, f1 + 1)]
            raw_quats: dict[str, list[tuple[float, float, float, float]]] = {b: [] for b in ordered_bones}
            lt_locs: list[tuple[float, float, float]] = []
            raw_non_lt: dict[str, list[tuple[float, float, float]]] = {b: [] for b in ordered_bones if b != "LowerTorso"}

            lt_head = Vector(next(b["head"] for b in r15_ref["bones"] if b["name"] == "LowerTorso"))
            rest_rel_offsets: dict[str, Any] = {}
            for b in ordered_bones:
                eb = arm.data.bones.get(b)
                if eb and eb.parent:
                    rest_rel_offsets[b] = eb.parent.matrix_local.inverted() @ eb.matrix_local.translation

            for f in range(f0, f1 + 1):
                bpy.context.scene.frame_set(f)
                bpy.context.view_layer.update()
                for b in ordered_bones:
                    pb = arm.pose.bones.get(b)
                    if pb and pb.parent:
                        q = (pb.parent.matrix.inverted() @ pb.matrix).to_quaternion().normalized()
                        raw_quats[b].append((float(q.w), float(q.x), float(q.y), float(q.z)))
                        if b != "LowerTorso":
                            cur_rel = pb.parent.matrix.inverted() @ pb.matrix.translation
                            rest_rel = rest_rel_offsets.get(b, cur_rel)
                            dv = cur_rel - rest_rel
                            raw_non_lt[b].append((float(dv.x), float(dv.y), float(dv.z)))
                    else:
                        raw_quats[b].append((1.0, 0.0, 0.0, 0.0))
                        if b != "LowerTorso":
                            raw_non_lt[b].append((0.0, 0.0, 0.0))
                pb_lt = arm.pose.bones.get("LowerTorso")
                if pb_lt and pb_lt.parent:
                    rel_pos = pb_lt.parent.matrix.inverted() @ pb_lt.matrix.translation
                    # HumanoidRootPart local Y=+Z_world, Z=-Y_world; subtract rest offset (0, 0.70, 0) in R15 bone space
                    dx = float(rel_pos.x - lt_head.x)
                    dy = float(rel_pos.y - lt_head.z)
                    dz = float(rel_pos.z + lt_head.y)
                    lt_locs.append((dx, dy, dz))
                else:
                    lt_locs.append((0.0, 0.0, 0.0))

            rot_tracks = {b: enforce_hemisphere_continuity(raw_quats[b]) for b in ordered_bones}
            non_lt_locs = {
                b: pts for b, pts in raw_non_lt.items()
                if any((p[0] * p[0] + p[1] * p[1] + p[2] * p[2]) > 1e-8 for p in pts)
            }
            clip_json = out_dir / f"{fbx_path.stem}.clip.json"
            payload = {
                "name": fbx_path.stem,
                "source_path": str(fbx_path),
                "fps": fps,
                "frames": frames,
                "rot_tracks": rot_tracks,
                "lt_locs": lt_locs,
                "non_lt_locs": non_lt_locs,
            }
            clip_json.write_text(json.dumps(payload), encoding="utf-8")
            emit("extract_done", file=str(fbx_path), clip_json=str(clip_json), frames=len(frames))
        except Exception as exc:
            emit("extract_error", file=str(fbx_path), error=str(exc))


def handle_export_clips_fbx(args: dict[str, Any]) -> None:
    from animforge.config import load_r15_reference

    app_root = Path(args["app_root"])
    r15_ref = load_r15_reference(app_root=app_root)
    ordered_bones = [b["name"] for b in r15_ref["bones"] if b.get("parent") is not None]
    export_flags = dict(r15_ref.get("fbx_export_defaults", {}))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 30
    r15_arm = create_r15_armature(r15_ref)
    r15_arm.animation_data_create()

    for item in args["clips"]:
        clip_data = json.loads(Path(item["clip_json"]).read_text(encoding="utf-8"))
        out_fbx = item["out_fbx"]
        bpy.context.scene.render.fps = int(round(float(clip_data.get("fps", 30.0)) or 30.0))
        frames = [float(x) for x in clip_data["frames"]]
        rot_tracks = {k: [tuple(q) for q in v] for k, v in clip_data["rot_tracks"].items()}
        lt_locs = [tuple(p) for p in clip_data["lt_locs"]]
        baked = bake_clip_to_r15_action(r15_arm, clip_data["name"], frames, rot_tracks, lt_locs, ordered_bones)
        export_r15_fbx(r15_arm, out_fbx, int(round(frames[0])), int(round(frames[-1])), export_flags)
        r15_arm.animation_data.action = None
        bpy.data.actions.remove(baked)
        emit("export_clip_done", file=out_fbx)


def main() -> None:
    if "--" not in sys.argv:
        raise SystemExit("Usage: blender --background --python worker_blender.py -- <args.json>")
    idx = sys.argv.index("--")
    args_path = sys.argv[idx + 1]
    args = json.loads(Path(args_path).read_text(encoding="utf-8"))
    src_dir = str(args["src_dir"])
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    cmd = args["command"]
    try:
        if cmd == "convert":
            handle_convert(args)
        elif cmd == "build_procedural":
            handle_build_procedural(args)
        elif cmd == "extract_fbx_clips":
            handle_extract_fbx_clips(args)
        elif cmd == "export_clips_fbx":
            handle_export_clips_fbx(args)
        else:
            raise ValueError(f"Unknown worker command: {cmd}")
    except Exception as exc:
        emit("fatal_error", error=str(exc), traceback=traceback.format_exc())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
