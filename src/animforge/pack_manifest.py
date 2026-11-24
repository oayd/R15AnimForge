"""Animation Pack Manifest builder and V10 schema validator."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import CharacterProfile, load_roblox_rules

_ASSET_ID_RE = re.compile(r"^(?:rbxassetid://)?(\d{5,})$")


@dataclass
class PackCheckResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_asset_id(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    m = _ASSET_ID_RE.match(s)
    return m.group(1) if m else None


def validate_pack_manifest(manifest: dict[str, Any], *, rules_cfg: dict[str, Any] | None = None) -> PackCheckResult:
    """Validate Roblox Animation Pack bundle structure (Rule V10) against roblox_rules.json."""
    rules = rules_cfg or load_roblox_rules()
    schema = rules["pack_schema"]
    errors: list[str] = []
    warnings: list[str] = []

    expected_models = set(schema.keys())
    actual_models = set(manifest.get("animations", {}).keys())

    missing_models = sorted(expected_models - actual_models)
    unexpected_models = sorted(actual_models - expected_models)
    if missing_models:
        errors.append(f"V10: Animation pack missing required model slot(s): {', '.join(missing_models)}")
    if unexpected_models:
        errors.append(f"V10: Animation pack contains unexpected top-level slot(s): {', '.join(unexpected_models)}")

    seen_asset_ids: dict[str, str] = {}
    anims = manifest.get("animations", {})

    for model_name, spec in schema.items():
        if model_name not in anims:
            continue
        entry = anims[model_name]
        sv_map: dict[str, list[dict[str, Any]]]
        if "string_values" in spec:
            sv_map = entry
            expected_svs = spec["string_values"]
        else:
            sv_map = entry
            expected_svs = {spec["string_value"]: spec["children"]}

        extra_svs = set(sv_map.keys()) - set(expected_svs.keys())
        missing_svs = set(expected_svs.keys()) - set(sv_map.keys())
        if extra_svs:
            errors.append(f"V10: {model_name}.R15Anim has unexpected StringValue(s): {', '.join(sorted(extra_svs))}")
        if missing_svs:
            errors.append(f"V10: {model_name}.R15Anim is missing StringValue(s): {', '.join(sorted(missing_svs))}")

        for sv_name, expected_children in expected_svs.items():
            if sv_name not in sv_map:
                continue
            children_list = sv_map[sv_name]
            if not isinstance(children_list, list):
                errors.append(f"V10: {model_name}.R15Anim.{sv_name} must be a list of Animation child entries.")
                continue

            child_names = [c.get("name") for c in children_list]
            if sorted(child_names) != sorted(expected_children) or len(child_names) != len(expected_children):
                errors.append(
                    f"V10: {model_name}.R15Anim.{sv_name} has invalid/duplicate children {child_names}; expected exactly {expected_children}."
                )
                continue

            for ch in children_list:
                cname = ch.get("name", "?")
                raw_id = ch.get("animation_id", "")
                aid = _extract_asset_id(raw_id)
                if aid is None:
                    errors.append(f"V10: {model_name}.R15Anim.{sv_name}.{cname} has empty or invalid asset ID: {raw_id!r}")
                else:
                    path_str = f"{model_name}.R15Anim.{sv_name}.{cname}"
                    if aid in seen_asset_ids:
                        errors.append(f"V10: Duplicate AnimationId '{aid}' shared by {seen_asset_ids[aid]} and {path_str}")
                    else:
                        seen_asset_ids[aid] = path_str

                if "weight" in ch:
                    try:
                        w = float(ch["weight"])
                        if w <= 0.0:
                            errors.append(f"V10: {model_name}.R15Anim.{sv_name}.{cname} Weight must be > 0 (got {w}).")
                    except (TypeError, ValueError):
                        errors.append(f"V10: {model_name}.R15Anim.{sv_name}.{cname} Weight must be numeric.")

                source_note = ch.get("source_confirmed")
                if source_note is False:
                    warnings.append(f"{model_name}.R15Anim.{sv_name}.{cname}: source not confirmed from CC2 archive (flagged from profile).")

    return PackCheckResult(valid=(len(errors) == 0), errors=errors, warnings=warnings, manifest=manifest)


def build_default_pack_manifest(profile: CharacterProfile, slot_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """Generate a clean V10 pack manifest template from character profile pack_slot_defaults."""
    ids = slot_ids or {}
    sd = profile.pack_slot_defaults

    def _entry(slot_key: str, child_name: str, weight: int | None = None) -> dict[str, Any]:
        meta = sd.get(slot_key, {})
        obj: dict[str, Any] = {
            "name": child_name,
            "animation_id": ids.get(slot_key, ""),
            "source_clip": meta.get("source"),
            "source_confirmed": bool(meta.get("confirmed", False)),
            "note": meta.get("note", ""),
        }
        if weight is not None:
            obj["weight"] = weight
        return obj

    return {
        "schema_version": 1,
        "pack_name": f"{profile.charcode}_Animation_Pack",
        "animations": {
            "IdleAnimation": {"idle": [_entry("Idle1", "Animation1", weight=1), _entry("Idle2", "Animation2", weight=9)]},
            "WalkAnimation": {"walk": [_entry("Walk", "WalkAnim")]},
            "RunAnimation": {"run": [_entry("Run", "RunAnim")]},
            "JumpAnimation": {"jump": [_entry("Jump", "JumpAnim")]},
            "FallAnimation": {"fall": [_entry("Fall", "FallAnim")]},
            "ClimbAnimation": {"climb": [_entry("Climb", "ClimbAnim")]},
            "SwimAnimation": {"swim": [_entry("Swim", "SwimAnim")], "swimidle": [_entry("SwimIdle", "SwimIdleAnim")]},
        },
    }


def save_and_validate_pack_manifest(manifest: dict[str, Any], out_path: Path) -> PackCheckResult:
    res = validate_pack_manifest(manifest)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return res
