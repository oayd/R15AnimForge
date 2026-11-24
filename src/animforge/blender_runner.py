"""Subprocess orchestrator for parallel isolated Blender 5.3 headless workers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import CharacterProfile, load_profile, resolve_profile_for_charcode
from .constants import get_app_root, get_local_app_data_dir
from .xfbin_scanner import ScanReport, scan_xfbin_directory

EventCallback = Callable[[dict[str, Any]], None]


def find_blender_executable(override: str | Path | None = None) -> Path:
    """Locate bundled portable Blender 5.3+ or explicit override without using %APPDATA%."""
    app_root = get_app_root()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    env_b = os.environ.get("ANIMFORGE_BLENDER")
    if env_b:
        candidates.append(Path(env_b))
    candidates.append(app_root / "blender" / "blender.exe")
    which_b = shutil.which("blender")
    if which_b:
        candidates.append(Path(which_b))
    prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    bf_root = Path(prog_files) / "Blender Foundation"
    if bf_root.is_dir():
        candidates.extend(sorted(bf_root.glob("Blender */blender.exe"), reverse=True))
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError(
        "Blender 5.3+ executable not found. Pass --blender <path>, set ANIMFORGE_BLENDER, or bundle under <app>/blender/blender.exe."
    )


def find_xfbin_addon_dir(override: str | Path | None = None) -> Path | None:
    app_root = get_app_root()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    env_a = os.environ.get("ANIMFORGE_ADDON_DIR")
    if env_a:
        candidates.append(Path(env_a))
    candidates.append(app_root / "vendor" / "Blender-XFBIN-Importer")
    appdata = os.environ.get("APPDATA")
    if appdata:
        bf_cfg = Path(appdata) / "Blender Foundation" / "Blender"
        if bf_cfg.is_dir():
            candidates.extend(sorted(bf_cfg.glob("*/scripts/addons/Blender-XFBIN-Importer"), reverse=True))
    for c in candidates:
        if (c / "__init__.py").is_file():
            return c.resolve()
    return None


def prepare_private_blender_env(addon_dir: Path | None = None) -> dict[str, str]:
    """Build subprocess env isolating Blender user resources/config/scripts inside %LOCALAPPDATA%\\AnimForge."""
    private_root = get_local_app_data_dir() / "blender_env"
    res_dir = private_root / "resources"
    cfg_dir = private_root / "config"
    scr_dir = private_root / "scripts"
    addons_dir = scr_dir / "addons"
    for d in (res_dir, cfg_dir, addons_dir):
        d.mkdir(parents=True, exist_ok=True)

    if addon_dir and (addon_dir / "__init__.py").is_file():
        dst = addons_dir / addon_dir.name
        if not dst.exists():
            shutil.copytree(addon_dir, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    env = os.environ.copy()
    env["BLENDER_USER_RESOURCES"] = str(res_dir)
    env["BLENDER_USER_CONFIG"] = str(cfg_dir)
    env["BLENDER_USER_SCRIPTS"] = str(scr_dir)
    return env


def _compute_settings_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _get_src_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for cand in (exe_dir / "_internal" / "animforge_src", exe_dir / "animforge_src"):
            if (cand / "animforge" / "worker_blender.py").is_file():
                return cand
    return Path(__file__).resolve().parent.parent


def _get_worker_script_path() -> Path:
    return _get_src_dir() / "animforge" / "worker_blender.py"


def _run_blender_worker(
    blender_exe: Path,
    args_payload: dict[str, Any],
    env: dict[str, str],
    on_event: EventCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, list[dict[str, Any]], str]:
    args_dir = get_local_app_data_dir() / "worker_jobs"
    args_dir.mkdir(parents=True, exist_ok=True)
    args_file = args_dir / f"job_{uuid.uuid4().hex[:10]}.json"
    args_file.write_text(json.dumps(args_payload, indent=2), encoding="utf-8")

    cmd = [
        str(blender_exe),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(_get_worker_script_path()),
        "--",
        str(args_file),
    ]
    events: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                break
            stripped = line.rstrip("\r\n")
            if stripped.startswith("@@EVT "):
                try:
                    evt = json.loads(stripped[6:])
                    events.append(evt)
                    if on_event:
                        on_event(evt)
                except json.JSONDecodeError:
                    raw_lines.append(stripped)
            else:
                raw_lines.append(stripped)
        rc = proc.wait()
        return rc, events, "\n".join(raw_lines)
    finally:
        try:
            args_file.unlink(missing_ok=True)
        except OSError:
            pass


def run_convert_job(
    input_dir: str | Path,
    output_dir: str | Path,
    profile: CharacterProfile,
    *,
    blender_exe: Path | None = None,
    addon_dir: Path | None = None,
    workers: int | None = None,
    cleanup_toggles: dict[str, bool] | None = None,
    action_filter: Sequence[str] | None = None,
    use_cache: bool = True,
    on_event: EventCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run headless parallel XFBIN -> R15 FBX conversion (NEVER calls validation or fix)."""
    b_exe = blender_exe or find_blender_executable()
    a_dir = addon_dir or find_xfbin_addon_dir()
    env = prepare_private_blender_env(a_dir)

    scan: ScanReport = scan_xfbin_directory(input_dir, profile, inspect_chunks=True)
    if scan.body_archive is None or not scan.anim_archives:
        raise RuntimeError(f"Scan failed before conversion: {'; '.join(scan.errors)}")
    profile = resolve_profile_for_charcode(profile, scan.charcode)

    body_path = scan.body_archive.decompressed_path or scan.body_archive.path
    anim_paths = [a.decompressed_path or a.path for a in scan.anim_archives]

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    cache_file = out_path / ".animforge_cache.json"
    resume_cache: dict[str, str] = {}
    if use_cache and cache_file.is_file():
        try:
            resume_cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            resume_cache = {}

    toggles = dict(cleanup_toggles or profile.cleanup_defaults_convert)
    settings_hash = _compute_settings_hash({
        "profile_charcode": profile.charcode,
        "bone_map": profile.bone_map_normalized,
        "rest_corr": profile.rest_pose_correction_deg,
        "cleanup": toggles,
        "body_sha": scan.body_archive.sha256,
        "anim_shas": [a.sha256 for a in scan.anim_archives],
    })

    # Estimate total actions from scan nuccChunkAnm counts (or explicit filter) to split across workers
    if action_filter:
        total_anm_est = len(action_filter)
    else:
        total_anm_est = sum(
            a.chunk_counts.get("NuccChunkAnm", 0) or a.chunk_counts.get("nuccChunkAnm", 0) or len(a.anm_names)
            for a in scan.anim_archives
        ) or 200
    cpu_half = max(1, (os.cpu_count() or 2) // 2)
    n_workers = max(1, min(4, cpu_half)) if workers is None else max(1, int(workers))
    if total_anm_est < 8:
        n_workers = 1
    else:
        n_workers = min(n_workers, max(1, (total_anm_est + 5) // 6))

    step = max(1, (total_anm_est + n_workers - 1) // n_workers)

    all_events: list[dict[str, Any]] = []
    errors: list[str] = []

    def _run_slice(w_idx: int) -> tuple[int, list[dict[str, Any]], str]:
        c_start = w_idx * step
        c_end = 10_000_000 if w_idx == n_workers - 1 else (w_idx + 1) * step
        payload = {
            "command": "convert",
            "app_root": str(get_app_root()),
            "src_dir": str(_get_src_dir()),
            "addon_dir": str(a_dir) if a_dir else None,
            "profile_path": str(profile.path),
            "profile_payload": profile.raw,
            "body_archive": body_path,
            "anim_archives": anim_paths,
            "output_dir": str(out_path),
            "action_filter": list(action_filter) if action_filter else [],
            "chunk_start": c_start,
            "chunk_end": c_end,
            "resume_cache": resume_cache if use_cache else {},
            "settings_hash": settings_hash,
            "cleanup_toggles": toggles,
        }
        return _run_blender_worker(b_exe, payload, env, on_event=on_event, cancel_event=cancel_event)

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = [pool.submit(_run_slice, i) for i in range(n_workers)]
        for fut in as_completed(futs):
            rc, evts, raw_log = fut.result()
            all_events.extend(evts)
            if rc != 0:
                fatals = [e.get("error", "") for e in evts if e.get("type") == "fatal_error"]
                errors.append(f"Blender worker exited with code {rc}: {fatals or raw_log[-400:]}")

    done_files: list[dict[str, Any]] = []
    cached_files: list[dict[str, Any]] = []
    skipped_actions: list[dict[str, Any]] = []
    action_errors: list[dict[str, Any]] = []

    for ev in all_events:
        t = ev.get("type")
        if t == "action_done":
            done_files.append(ev)
            resume_cache[ev["filename"]] = ev["cache_key"]
        elif t == "action_cached":
            cached_files.append(ev)
        elif t == "action_skipped":
            skipped_actions.append(ev)
        elif t == "action_error":
            action_errors.append(ev)

    if use_cache:
        cache_file.write_text(json.dumps(resume_cache, indent=2), encoding="utf-8")

    # Sort deterministically by filename
    done_files.sort(key=lambda d: d.get("filename", ""))
    face_only_clips = [d["filename"] for d in done_files if d.get("face_only")]

    report = {
        "scan": scan.to_dict(),
        "output_dir": str(out_path),
        "workers_used": n_workers,
        "exported_count": len(done_files) + len(cached_files),
        "newly_exported": len(done_files),
        "cached_skipped": len(cached_files),
        "skipped_no_data": len(skipped_actions) // max(1, n_workers),
        "face_only_informational": face_only_clips,
        "action_errors": action_errors,
        "worker_errors": errors,
        "files": done_files + cached_files,
        "verified_for_roblox_upload": False,
    }
    (out_path / "convert_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors or action_errors:
        raise RuntimeError(f"Conversion completed with errors: worker={errors}, actions={action_errors}")
    return report


def run_build_procedural_job(
    input_dir: str | Path,
    output_dir: str | Path,
    profile: CharacterProfile,
    *,
    builders: Sequence[str] | None = None,
    upper_torso_align: bool = False,
    smooth_jump_launch: bool = True,
    blender_exe: Path | None = None,
    addon_dir: Path | None = None,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    b_exe = blender_exe or find_blender_executable()
    a_dir = addon_dir or find_xfbin_addon_dir()
    env = prepare_private_blender_env(a_dir)
    scan = scan_xfbin_directory(input_dir, profile, inspect_chunks=False)
    if scan.body_archive is None or not scan.anim_archives:
        raise RuntimeError(f"Scan failed: {'; '.join(scan.errors)}")
    profile = resolve_profile_for_charcode(profile, scan.charcode)

    payload = {
        "command": "build_procedural",
        "app_root": str(get_app_root()),
        "src_dir": str(_get_src_dir()),
        "addon_dir": str(a_dir) if a_dir else None,
        "profile_path": str(profile.path),
        "profile_payload": profile.raw,
        "body_archive": scan.body_archive.decompressed_path or scan.body_archive.path,
        "anim_archives": [a.decompressed_path or a.path for a in scan.anim_archives],
        "output_dir": str(output_dir),
        "builders": list(builders) if builders else ["idle_loop", "alt_idle_look", "jump", "fall"],
        "upper_torso_align": upper_torso_align,
        "smooth_jump_launch": smooth_jump_launch,
    }
    rc, evts, raw_log = _run_blender_worker(b_exe, payload, env, on_event=on_event)
    if rc != 0:
        raise RuntimeError(f"Procedural builder failed ({rc}): {raw_log[-500:]}")
    built = [e for e in evts if e.get("type") == "builder_done"]
    return {"built": built, "output_dir": str(output_dir)}


def extract_clips_from_fbx(
    fbx_paths: Sequence[Path],
    *,
    blender_exe: Path | None = None,
    on_event: EventCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Use headless Blender 5.3 to sample exported .fbx clips into ClipData dicts."""
    b_exe = blender_exe or find_blender_executable()
    env = prepare_private_blender_env(None)
    temp_dir = Path(tempfile.mkdtemp(prefix="animforge_extract_"))
    try:
        payload = {
            "command": "extract_fbx_clips",
            "app_root": str(get_app_root()),
            "src_dir": str(_get_src_dir()),
            "fbx_files": [str(p) for p in fbx_paths],
            "temp_clip_dir": str(temp_dir),
        }
        rc, evts, raw_log = _run_blender_worker(b_exe, payload, env, on_event=on_event, cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Operation cancelled by user.")
        if rc != 0:
            raise RuntimeError(f"FBX extract worker failed ({rc}): {raw_log[-500:]}")
        out: list[dict[str, Any]] = []
        for ev in evts:
            if ev.get("type") == "extract_done":
                out.append(json.loads(Path(ev["clip_json"]).read_text(encoding="utf-8")))
        return out
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def export_clips_to_fbx(
    items: Sequence[tuple[dict[str, Any], Path]],
    *,
    blender_exe: Path | None = None,
    on_event: EventCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[str]:
    """Export modified/fixed ClipData dicts to clean FBX files via headless Blender."""
    b_exe = blender_exe or find_blender_executable()
    env = prepare_private_blender_env(None)
    temp_dir = Path(tempfile.mkdtemp(prefix="animforge_export_"))
    try:
        clip_specs = []
        for idx, (clip_dict, out_fbx) in enumerate(items):
            cj = temp_dir / f"clip_{idx}.json"
            cj.write_text(json.dumps(clip_dict), encoding="utf-8")
            clip_specs.append({"clip_json": str(cj), "out_fbx": str(out_fbx)})
        payload = {
            "command": "export_clips_fbx",
            "app_root": str(get_app_root()),
            "src_dir": str(_get_src_dir()),
            "clips": clip_specs,
        }
        rc, evts, raw_log = _run_blender_worker(b_exe, payload, env, on_event=on_event, cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Operation cancelled by user.")
        if rc != 0:
            raise RuntimeError(f"FBX export worker failed ({rc}): {raw_log[-500:]}")
        return [e["file"] for e in evts if e.get("type") == "export_clip_done"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
