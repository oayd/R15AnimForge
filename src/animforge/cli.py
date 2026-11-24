"""Unified argparse CLI sharing the core R15 AnimForge library.

Commands: scan | convert | validate | fix | build-pack | selftest
`convert` NEVER validates or fixes unless `--verify-for-upload` is explicitly passed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from .config import load_profile, load_r15_reference, load_roblox_rules, resolve_profile_for_charcode
from .constants import APP_VERSION, MAPPED_R15_BONE_NAMES


def _clip_dict_to_clip_data(d: dict[str, Any]) -> Any:
    from .validation import ClipData, infer_clip_is_loop

    name = str(d["name"])
    is_loop = infer_clip_is_loop(name)
    raw_nlt = d.get("non_lt_locs") or {}
    return ClipData(
        name=name,
        fps=float(d.get("fps", 30.0)),
        frames=[float(x) for x in d["frames"]],
        rot_tracks={k: [tuple(q) for q in v] for k, v in d["rot_tracks"].items()},
        lt_locs=[tuple(v) for v in d["lt_locs"]],
        loop=bool(d.get("loop", is_loop)),
        non_lt_locs={k: [tuple(v) for v in pts] for k, pts in raw_nlt.items()},
        source_path=d.get("source_path"),
    )


def _clip_data_to_dict(c: Any) -> dict[str, Any]:
    return {
        "name": c.name,
        "fps": c.fps,
        "frames": c.frames,
        "rot_tracks": c.rot_tracks,
        "lt_locs": c.lt_locs,
        "loop": c.loop,
        "source_path": c.source_path,
    }


def _emit_output(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2))


def cmd_scan(args: argparse.Namespace) -> int:
    from .charcodes import lookup_character, parse_message_info_names
    from .xfbin_scanner import scan_xfbin_directory

    profile = load_profile(args.profile or "auto")
    if args.charcode:
        profile = resolve_profile_for_charcode(profile, args.charcode)
    rep = scan_xfbin_directory(args.input_dir, profile, inspect_chunks=not args.fast)
    extra_names = parse_message_info_names(args.input_dir, rep.detected_charcodes or [rep.charcode])
    cinfo = lookup_character(rep.charcode, extra_names=extra_names)
    d = rep.to_dict()
    d["character_name"] = cinfo.name
    d["character_label"] = cinfo.label
    d["detected_characters"] = {
        cc: lookup_character(cc, extra_names=extra_names).label for cc in rep.detected_charcodes
    }
    if args.json:
        _emit_output(d, True)
    else:
        name_part = f" — {cinfo.label}" if cinfo.name else ""
        print(f"Scan for '{rep.charcode}'{name_part} in {rep.input_dir} (detected codes={len(rep.detected_charcodes)}):")
        print(f"  Body: {rep.body_archive.relative_path if rep.body_archive else 'MISSING'}")
        print(f"  Animation archives ({len(rep.anim_archives)}): {[a.name for a in rep.anim_archives]}")
        print(f"  Excluded ({len(rep.excluded_archives)}): {[(a.name, a.role) for a in rep.excluded_archives]}")
        if rep.errors:
            print(f"  Errors: {rep.errors}")
    return 1 if rep.errors else 0


def cmd_convert(args: argparse.Namespace) -> int:
    from .blender_runner import find_blender_executable, find_xfbin_addon_dir, run_build_procedural_job, run_convert_job

    profile = load_profile(args.profile or "auto")
    if args.charcode:
        profile = resolve_profile_for_charcode(profile, args.charcode)
    toggles = dict(profile.cleanup_defaults_convert)
    if args.clean_right_hand:
        toggles["right_hand_clamp"] = True
    if args.clean_left_hand:
        toggles["left_hand_clamp"] = True
    if args.clean_elbows:
        toggles["elbow_cap"] = True
    if args.clean_feet:
        toggles["feet_level"] = True
    if args.clean_head:
        toggles["head_forward_yaw"] = True
    if args.clean_uppertorso:
        toggles["upper_torso_align"] = True

    b_exe = find_blender_executable(args.blender)
    a_dir = find_xfbin_addon_dir(args.addon_dir)

    def _on_evt(ev: dict[str, Any]) -> None:
        if not args.json and ev.get("type") == "action_done":
            tag = " [FACE-ONLY]" if ev.get("face_only") else ""
            print(f"  [EXPORTED] {ev['filename']} ({ev['frames']}f){tag}")

    rep = run_convert_job(
        args.input_dir,
        args.output_dir,
        profile,
        blender_exe=b_exe,
        addon_dir=a_dir,
        workers=args.workers,
        cleanup_toggles=toggles,
        action_filter=args.actions,
        use_cache=not args.no_cache,
        on_event=_on_evt,
    )

    if args.build_procedural:
        proc_rep = run_build_procedural_job(
            args.input_dir,
            args.output_dir,
            profile,
            upper_torso_align=bool(args.clean_uppertorso),
            blender_exe=b_exe,
            addon_dir=a_dir,
        )
        rep["procedural_built"] = proc_rep["built"]

    exit_code = 0
    if args.verify_for_upload:
        # Lazy opt-in import ONLY when --verify-for-upload was explicitly requested!
        v_exit, v_payload = _run_validate_on_path(Path(args.output_dir), Path(args.output_dir), b_exe, args.json)
        rep["verified_for_roblox_upload"] = True
        rep["validation_summary"] = v_payload["summary"]
        if v_exit != 0:
            exit_code = v_exit

    if args.json:
        _emit_output(rep, True)
    else:
        print(f"[DONE] Exported {rep['exported_count']} FBX file(s) to {rep['output_dir']}")
        if not args.verify_for_upload:
            print("[INFO] Files not checked for Roblox upload (validation is opt-in). Run `animforge validate` when ready.")
    return exit_code


def _collect_fbx_targets(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.glob("*.fbx"))
    raise FileNotFoundError(f"Validation target does not exist: {target}")


def _run_validate_on_path(target: Path, report_dir: Path, blender_exe: Path, as_json: bool) -> tuple[int, dict[str, Any]]:
    from .blender_runner import extract_clips_from_fbx
    from .validation import validate_clip, write_validation_reports

    fbx_files = _collect_fbx_targets(target)
    if not fbx_files:
        raise FileNotFoundError(f"No .fbx files found under: {target}")

    raw_clips = extract_clips_from_fbx(fbx_files, blender_exe=blender_exe)
    results = [validate_clip(_clip_dict_to_clip_data(d)) for d in raw_clips]
    payload = write_validation_reports(
        results,
        report_dir / "validation_report.json",
        report_dir / "validation_report.html",
    )
    if not as_json:
        for r in results:
            print(f"  [{r.status}] {r.clip_name}: dur={r.duration_sec:.3f}s spd={r.peak_speed_studs_s:.2f} ang={r.peak_ang_vel_deg_s:.0f} depth={r.max_foot_depth_studs:.3f}")
        print(f"Summary: {payload['summary']} -> {report_dir / 'validation_report.html'}")
    fail_count = payload["summary"]["FAIL"]
    return (1 if fail_count > 0 else 0), payload


def cmd_validate(args: argparse.Namespace) -> int:
    from .blender_runner import find_blender_executable

    b_exe = find_blender_executable(args.blender)
    target = Path(args.target)
    rep_dir = Path(args.report_dir) if args.report_dir else (target.parent if target.is_file() else target)
    code, payload = _run_validate_on_path(target, rep_dir, b_exe, args.json)
    if args.json:
        _emit_output(payload, True)
    return code


def cmd_fix(args: argparse.Namespace) -> int:
    from .blender_runner import export_clips_to_fbx, extract_clips_from_fbx, find_blender_executable
    from .fixer import (
        FixConfigurationError,
        FixFailedError,
        fix_clip,
        next_fixed_output_dir,
        resolve_apply_categories,
        write_fix_log,
    )

    if not args.apply:
        raise FixConfigurationError("Refusing to run `fix` without explicit --apply <categories>.")
    raw_cats = [c for item in args.apply for c in item.split(",")]
    categories = resolve_apply_categories(raw_cats)

    profile = load_profile(args.profile)
    b_exe = find_blender_executable(args.blender)
    target = Path(args.target)
    fbx_files = _collect_fbx_targets(target)
    if not fbx_files:
        raise FileNotFoundError(f"No .fbx files found under: {target}")

    base_parent = target.parent if target.is_file() else target
    out_dir = Path(args.out_dir) if args.out_dir else next_fixed_output_dir(base_parent)
    if out_dir.resolve() == base_parent.resolve():
        raise FixConfigurationError("Fix output folder must be separate from originals (`_fixed`). Never overwrite originals.")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_clips = extract_clips_from_fbx(fbx_files, blender_exe=b_exe)
    fix_results = []
    skipped_unfixable: list[dict[str, str]] = []
    export_items: list[tuple[dict[str, Any], Path]] = []

    for d in raw_clips:
        clip = _clip_dict_to_clip_data(d)
        src_path = Path(d["source_path"]) if d.get("source_path") else Path(f"{clip.name}.fbx")
        try:
            fr = fix_clip(clip, categories, profile=profile)
        except FixFailedError as exc:
            skipped_unfixable.append({"clip_name": clip.name, "reason": str(exc)})
            continue
        fix_results.append(fr)
        stem = src_path.stem if src_path.stem.endswith("_fixed") else f"{src_path.stem}_fixed"
        out_fbx = out_dir / f"{stem}.fbx"
        export_items.append((_clip_data_to_dict(fr.fixed_clip), out_fbx))

    exported = export_clips_to_fbx(export_items, blender_exe=b_exe) if export_items else []
    write_fix_log(fix_results, out_dir / "fix_log.json")

    reval_summary: dict[str, Any] | None = None
    reval_rc = 0
    if exported:
        reval_rc, reval_payload = _run_validate_on_path(out_dir, out_dir, b_exe, args.json)
        reval_summary = reval_payload["summary"]

    summary = {
        "out_dir": str(out_dir),
        "categories": categories,
        "fixed_files": exported,
        "skipped_unfixable": skipped_unfixable,
        "revalidation_summary": reval_summary,
        "clips": [r.to_summary_dict() for r in fix_results],
    }
    if args.json:
        _emit_output(summary, True)
    else:
        for r in fix_results:
            print(f"  [FIXED] {r.clip_name}: speed {r.before.peak_speed_studs_s}->{r.after.peak_speed_studs_s}, depth {r.before.max_foot_depth_studs}->{r.after.max_foot_depth_studs}, status={r.after.status}")
        for sk in skipped_unfixable:
            print(f"  [SKIPPED] {sk['clip_name']}: {sk['reason']}")
        print(f"Fixed {len(exported)} file(s) saved to {out_dir} (skipped {len(skipped_unfixable)} unfixable)")
    return 1 if (not exported and skipped_unfixable) or reval_rc != 0 else 0


def cmd_build_pack(args: argparse.Namespace) -> int:
    from .pack_manifest import build_default_pack_manifest, save_and_validate_pack_manifest, validate_pack_manifest
    from .studio_postprocess import write_studio_postprocess_script

    profile = load_profile(args.profile)
    manifest_path = Path(args.manifest)

    if args.write_template:
        manifest = build_default_pack_manifest(profile)
        res = save_and_validate_pack_manifest(manifest, manifest_path)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        res = validate_pack_manifest(manifest)

    studio_lua_written: str | None = None
    if args.studio_script_out:
        studio_lua_written = str(write_studio_postprocess_script(args.studio_script_out))

    out = res.to_dict()
    if studio_lua_written:
        out["studio_postprocess_lua"] = studio_lua_written

    if args.json:
        _emit_output(out, True)
    else:
        print(f"Pack manifest valid={res.valid} errors={len(res.errors)} warnings={len(res.warnings)}")
        for w in res.warnings:
            print(f"  [WARN] {w}")
        for e in res.errors:
            print(f"  [ERROR] {e}")
        if studio_lua_written:
            print(f"Studio Luau script written to: {studio_lua_written}")

    if args.write_template:
        return 0
    return 0 if res.valid else 1


def cmd_selftest(args: argparse.Namespace) -> int:
    from .builders import build_idle_loop
    from .cleanup import apply_head_forward_yaw
    from .crilayla import decompress_crilayla, sniff_magic
    from .fixer import FixConfigurationError, resolve_apply_categories
    from .math_retarget import euler_to_quat, q_identity, quat_to_euler
    from .validation import ClipData, validate_clip

    checks: list[dict[str, Any]] = []
    profile = load_profile(args.profile)
    _ = load_r15_reference()
    _ = load_roblox_rules()
    checks.append({"name": "configs_load", "ok": True})

    # Head yaw + math check
    q = euler_to_quat(0.2, -0.3, 0.1, "YXZ")
    r = quat_to_euler(q, "YXZ")
    assert all(abs(a - b) < 1e-6 for a, b in zip((0.2, -0.3, 0.1), r))
    p = apply_head_forward_yaw({b: q_identity() for b in MAPPED_R15_BONE_NAMES}, yaw_deg=36.0)
    assert abs(math.degrees(quat_to_euler(p["Head"], "YXZ")[1]) - 36.0) < 1e-4
    checks.append({"name": "math_and_head_yaw", "ok": True})

    # Opt-in fixer guard
    guard_ok = False
    try:
        resolve_apply_categories([])
    except FixConfigurationError:
        guard_ok = True
    assert guard_ok
    checks.append({"name": "fix_requires_apply", "ok": True})

    # Validation smoke
    n = 31
    c = ClipData(
        name="selftest",
        fps=30.0,
        frames=[float(i + 1) for i in range(n)],
        rot_tracks={
            b: [euler_to_quat(0.2 * math.sin(2 * math.pi * i / (n - 1)), 0.0, 0.0, "YXZ") if b == "LeftUpperArm" else q_identity() for i in range(n)]
            for b in MAPPED_R15_BONE_NAMES
        },
        lt_locs=[(0.0, 0.0, 0.0) for _ in range(n)],
        loop=True,
    )
    assert validate_clip(c).status == "PASS"
    assert sniff_magic(b"NUCC\x00\x00") == "NUCC"
    checks.append({"name": "validation_and_sniff", "ok": True})

    # Procedural builder smoke
    idle = build_idle_loop(c, c, profile)
    assert len(idle.frames) == 91
    checks.append({"name": "procedural_idle_builder", "ok": True})

    payload = {"version": APP_VERSION, "status": "PASS", "checks": checks}
    if args.json:
        _emit_output(payload, True)
    else:
        print(f"R15 AnimForge selftest PASSED ({len(checks)}/{len(checks)} checks)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="animforge", description="R15 AnimForge: CC2 XFBIN to Roblox R15 FBX converter, validator & pack builder")
    p.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--profile", default=None, help="Character profile JSON path or name (default: auto for scan/convert)")
        sp.add_argument("--charcode", default=None, help="Override ASBR/CC2 character code (e.g. 4jtr01, 4dio01)")
        sp.add_argument("--json", action="store_true", help="Emit JSON output")

    s_scan = sub.add_parser("scan", help="Scan input folder for XFBIN archives")
    s_scan.add_argument("input_dir")
    s_scan.add_argument("--fast", action="store_true", help="Skip deep chunk parsing")
    _add_common(s_scan)

    s_conv = sub.add_parser("convert", help="Convert XFBIN archives to plain R15 FBX files (never validates/fixes unless --verify-for-upload)")
    s_conv.add_argument("input_dir")
    s_conv.add_argument("output_dir")
    s_conv.add_argument("--workers", type=int, default=None)
    s_conv.add_argument("--actions", nargs="*", default=None, help="Optional subset of action names")
    s_conv.add_argument("--blender", default=None, help="Path to blender.exe")
    s_conv.add_argument("--addon-dir", default=None, help="Override Blender-XFBIN-Importer directory")
    s_conv.add_argument("--no-cache", action="store_true")
    s_conv.add_argument("--build-procedural", action="store_true", help="Also generate idle_loop, alt_idle_look, Jump, and Fall FBXs")
    s_conv.add_argument("--verify-for-upload", action="store_true", help="OPT-IN: run Roblox upload validation after convert")
    s_conv.add_argument("--clean-right-hand", action="store_true")
    s_conv.add_argument("--clean-left-hand", action="store_true")
    s_conv.add_argument("--clean-elbows", action="store_true")
    s_conv.add_argument("--clean-feet", action="store_true")
    s_conv.add_argument("--clean-head", action="store_true")
    s_conv.add_argument("--clean-uppertorso", action="store_true")
    _add_common(s_conv)

    s_val = sub.add_parser("validate", help="Read-only Roblox upload verification on .fbx file or folder")
    s_val.add_argument("target")
    s_val.add_argument("--report-dir", default=None)
    s_val.add_argument("--blender", default=None)
    _add_common(s_val)

    s_fix = sub.add_parser("fix", help="Explicitly apply named fixes into a separate _fixed folder")
    s_fix.add_argument("target")
    s_fix.add_argument("--apply", nargs="+", required=False, default=None, help="Categories: strip_translation,floor_lift,clamps,smoothing,time_stretch")
    s_fix.add_argument("--out-dir", default=None)
    s_fix.add_argument("--blender", default=None)
    _add_common(s_fix)

    s_pack = sub.add_parser("build-pack", help="Validate or scaffold a Roblox Animation Pack manifest (V10) & Luau post-processor")
    s_pack.add_argument("manifest")
    s_pack.add_argument("--write-template", action="store_true")
    s_pack.add_argument("--studio-script-out", default=None)
    _add_common(s_pack)

    s_self = sub.add_parser("selftest", help="Run built-in offline sanity checks")
    _add_common(s_self)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        dispatch = {
            "scan": cmd_scan,
            "convert": cmd_convert,
            "validate": cmd_validate,
            "fix": cmd_fix,
            "build-pack": cmd_build_pack,
            "selftest": cmd_selftest,
        }
        return int(dispatch[args.command](args))
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}))
        else:
            print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
