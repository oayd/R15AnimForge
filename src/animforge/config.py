"""Configuration loaders for R15 reference rig, Roblox validation rules, and character profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import get_app_root


@dataclass(frozen=True)
class CharacterProfile:
    """Loaded character profile (charcode, bone map, rest offsets, cleanup, and recipes)."""

    path: Path
    raw: dict[str, Any]
    charcode: str
    profile_name: str
    output_prefix: str
    discovery: dict[str, Any]
    bone_map_normalized: dict[str, list[str]]
    rest_pose_correction_deg: dict[str, list[float]]
    cleanup_defaults_convert: dict[str, bool] = field(default_factory=dict)
    cleanup_params: dict[str, Any] = field(default_factory=dict)
    procedural_builders: dict[str, Any] = field(default_factory=dict)
    pack_slot_defaults: dict[str, Any] = field(default_factory=dict)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_r15_reference(app_root: Path | None = None) -> dict[str, Any]:
    root = app_root or get_app_root()
    return load_json(root / "data" / "r15_reference.json")


def load_roblox_rules(app_root: Path | None = None) -> dict[str, Any]:
    root = app_root or get_app_root()
    return load_json(root / "data" / "roblox_rules.json")


def _render_placeholders(obj: Any, charcode: str, output_prefix: str) -> Any:
    if isinstance(obj, str):
        return obj.replace("{output_prefix}", output_prefix).replace("{charcode}", charcode)
    if isinstance(obj, list):
        return [_render_placeholders(x, charcode, output_prefix) for x in obj]
    if isinstance(obj, dict):
        return {k: _render_placeholders(v, charcode, output_prefix) for k, v in obj.items()}
    return obj


def profile_from_dict(raw: dict[str, Any], path: Path | None = None) -> CharacterProfile:
    resolved = path.resolve() if path is not None else Path("<dynamic>").resolve()
    cc = str(raw["charcode"])
    out_pref = str(raw.get("output_prefix", f"{cc}_R15_")).replace("{charcode}", cc)
    return CharacterProfile(
        path=resolved,
        raw=raw,
        charcode=cc,
        profile_name=str(raw.get("profile_name", cc)),
        output_prefix=out_pref,
        discovery=dict(raw.get("discovery", {})),
        bone_map_normalized={k: [str(v) for v in vals] for k, vals in raw["bone_map_normalized"].items()},
        rest_pose_correction_deg={k: [float(x) for x in vals] for k, vals in raw.get("rest_pose_correction_deg", {}).items()},
        cleanup_defaults_convert={k: bool(v) for k, v in raw.get("cleanup_defaults_convert", {}).items()},
        cleanup_params=dict(raw.get("cleanup_params", {})),
        procedural_builders=dict(raw.get("procedural_builders", {})),
        pack_slot_defaults=dict(raw.get("pack_slot_defaults", {})),
    )


def load_profile(profile_path: str | Path | None = None, app_root: Path | None = None) -> CharacterProfile:
    root = app_root or get_app_root()
    if profile_path is None:
        resolved = root / "profiles" / "4rhn01.json"
    else:
        candidate = Path(profile_path)
        if candidate.is_file():
            resolved = candidate
        else:
            by_name = root / "profiles" / (candidate.name if candidate.suffix == ".json" else f"{candidate.name}.json")
            resolved = by_name if by_name.is_file() else candidate

    raw = load_json(resolved)
    return profile_from_dict(raw, path=resolved)


def resolve_profile_for_charcode(
    profile: CharacterProfile,
    charcode: str,
    app_root: Path | None = None,
) -> CharacterProfile:
    """Resolve `auto` templates or charcode overrides into a concrete CharacterProfile."""
    cc = (charcode or "").strip()
    if not cc or cc.lower() in ("auto", "*"):
        return profile
    if cc.lower() == profile.charcode.lower():
        return profile

    root = app_root or get_app_root()
    if profile.charcode.lower() in ("auto", "*"):
        specific = root / "profiles" / f"{cc.lower()}.json"
        if specific.is_file():
            return load_profile(specific, app_root=root)

    raw_copy = json.loads(json.dumps(profile.raw))
    old_cc = str(raw_copy.get("charcode", ""))
    raw_copy["charcode"] = cc
    if old_cc.lower() in ("auto", "*"):
        from .charcodes import lookup_character

        cinfo = lookup_character(cc, app_root=root)
        raw_copy["profile_name"] = cinfo.label if cinfo.name else f"ASBR / CC2 ({cc})"
        out_pref = str(raw_copy.get("output_prefix", "{charcode}_R15_")).replace("{charcode}", cc)
    else:
        raw_copy["profile_name"] = f"{profile.profile_name} [{cc}]"
        out_pref = f"{cc}_R15_"
    raw_copy["output_prefix"] = out_pref

    rendered = _render_placeholders(raw_copy, cc, out_pref)
    if old_cc and old_cc.lower() not in ("auto", "*"):
        # Replace explicit charcode tokens if switching an explicit profile via --charcode
        rendered = json.loads(json.dumps(rendered).replace(old_cc, cc))
        rendered["output_prefix"] = out_pref
    return profile_from_dict(rendered, path=profile.path)


def list_profiles(app_root: Path | None = None) -> list[Path]:
    root = app_root or get_app_root()
    pdir = root / "profiles"
    if not pdir.is_dir():
        return []
    files = sorted(pdir.glob("*.json"), key=lambda p: (0 if p.stem == "auto" else 1, p.stem.lower()))
    return files
