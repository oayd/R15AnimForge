"""Tests for universal JoJo ASBR / CC2 character auto-detection and dynamic profiles."""

from __future__ import annotations

import json
from pathlib import Path

from animforge.action_matcher import learn_stance_tag, resolve_action_for_role, resolve_builder_fields
from animforge.charcodes import (
    crc32_bzip2,
    extract_charcode_from_choice,
    format_charcode_choice,
    load_charcode_catalog,
    lookup_character,
    parse_message_info_names,
)
from animforge.cli import main as cli_main
from animforge.config import load_profile, resolve_profile_for_charcode
from animforge.math_retarget import map_skeleton_bones, normalize_bone_name
from animforge.xfbin_scanner import detect_charcodes_in_directory, scan_xfbin_directory


def test_detect_and_scan_any_asbr_character(tmp_path: Path) -> None:
    inp = tmp_path / "jotaro"
    (inp / "spc").mkdir(parents=True)
    (inp / "spc" / "4jtr01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")
    (inp / "spc" / "4jtr01bod1_f.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)
    (inp / "spc" / "MAIN_4jtr01mot1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkAnm")
    (inp / "spc" / "4jtr01viewer1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkAnm")
    (inp / "spc" / "4jtr01eff1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)
    (inp / "spc" / "4jtr01_anmofs.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)

    auto_prof = load_profile("auto")
    report = scan_xfbin_directory(inp, auto_prof, cache_dir=tmp_path / "layla", inspect_chunks=True)

    assert not report.errors
    assert report.charcode == "4jtr01"
    assert report.detected_charcodes == ["4jtr01"]
    assert report.body_archive is not None
    assert report.body_archive.name == "4jtr01bod1.xfbin"
    assert [a.name for a in report.anim_archives] == ["4jtr01viewer1.xfbin", "MAIN_4jtr01mot1.xfbin"]

    resolved = resolve_profile_for_charcode(auto_prof, report.charcode)
    assert resolved.charcode == "4jtr01"
    assert resolved.output_prefix == "4jtr01_R15_"
    assert resolved.procedural_builders["idle_loop"]["base_action"] == "4jtr01_bas_nut_st"
    assert resolved.procedural_builders["jump"]["output_name"] == "4jtr01_R15_Jump"
    assert resolved.procedural_builders["fall"]["output_name"] == "4jtr01_R15_Fall"


def test_auto_resolves_rohan_to_dedicated_profile() -> None:
    auto_prof = load_profile("auto")
    rohan = resolve_profile_for_charcode(auto_prof, "4rhn01")
    assert rohan.charcode == "4rhn01"
    assert rohan.output_prefix == "Rohan_R15_"


def test_multi_character_folder_and_cli_charcode_override(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    inp = tmp_path / "asbr_dump"
    inp.mkdir()
    (inp / "4dio01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")
    (inp / "MAIN_4dio01mot1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkAnm")
    (inp / "4jtr01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")
    (inp / "MAIN_4jtr01mot1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkAnm")

    codes = detect_charcodes_in_directory(inp)
    assert codes == ["4dio01", "4jtr01"]

    rc = cli_main(["scan", str(inp), "--charcode", "4jtr01", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["charcode"] == "4jtr01"
    assert payload["body_archive"]["name"] == "4jtr01bod1.xfbin"
    assert payload["detected_charcodes"] == ["4dio01", "4jtr01"]


def test_universal_bone_mapping_for_other_asbr_characters() -> None:
    auto_prof = resolve_profile_for_charcode(load_profile("auto"), "4dio01")
    assert normalize_bone_name("4dio01t0 l_upperarm", "auto") == "l upperarm"
    assert normalize_bone_name("1jns01_r_hand", "1jns01") == "r hand"

    bones = [
        "4dio01t0 pelvis",
        "4dio01t0 spine1",
        "4dio01t0 head",
        "4dio01t0 l upperarm",
        "4dio01t0 l forearm",
        "4dio01t0 l hand",
        "4dio01t0 r upperarm",
        "4dio01t0 r forearm",
        "4dio01t0 r hand",
        "4dio01t0 l thigh",
        "4dio01t0 l calf",
        "4dio01t0 l foot",
        "4dio01t0 r thigh",
        "4dio01t0 r calf",
        "4dio01t0 r foot",
    ]
    mapped = map_skeleton_bones(bones, auto_prof)
    assert mapped["LowerTorso"] == "4dio01t0 pelvis"
    assert mapped["UpperTorso"] == "4dio01t0 spine1"
    assert mapped["RightHand"] == "4dio01t0 r hand"


def test_action_formula_learner_handles_7val01_and_case_variants() -> None:
    # Mirrors real 7val01 archive actions with `_r_` stance tag and missing `nut_st` / `nut_jump`
    actions = [
        "7val01_bas_r_nut",
        "7val01_bas_r_jump",
        "7val01_bas_r_jump_back",
        "7val01_bas_r_jump_back_lp",
        "7val01_bas_r_jump_front",
        "7val01_bas_r_jump_front_lp",
        "7val01_bas_r_jump_lp",
    ]
    assert learn_stance_tag(actions, "7val01") == "r"

    prof = resolve_profile_for_charcode(load_profile("auto"), "7val01")
    idle_res = resolve_builder_fields(prof.procedural_builders["idle_loop"], actions, charcode="7val01")
    assert idle_res["base_action"].source == "7val01_bas_r_nut"
    assert idle_res["breathing_action"].source == "7val01_bas_r_nut"

    jump_res = resolve_builder_fields(prof.procedural_builders["jump"], actions, charcode="7val01")
    assert jump_res["launch_action"].source == "7val01_bas_r_jump"
    assert jump_res["hang_action"].source == "7val01_bas_r_jump_lp"
    assert jump_res["crouch_action"].source == "7val01_bas_r_jump"  # graceful fallback when nut_jump absent

    fall_res = resolve_builder_fields(prof.procedural_builders["fall"], actions, charcode="7val01")
    assert fall_res["hang_action"].source == "7val01_bas_r_jump_lp"

    # Case-insensitive matching
    upper_match = resolve_action_for_role("7val01_bas_jump_lp", "hang_action", ["7VAL01_BAS_R_JUMP_LP"], charcode="7val01")
    assert upper_match.source == "7VAL01_BAS_R_JUMP_LP"

    # Arbitrary other ASBR characters: Stand stance `_s_`, cross-variant prefix (`3jtr10` inside `3jtr01`), or non-`bas_` fallback
    stand_match = resolve_action_for_role("3jtr01_bas_jump_lp", "hang_action", ["3jtr10_bas_s_jump_lp", "3jtr10_bas_s_jump_front_lp"], charcode="3jtr01")
    assert stand_match.source == "3jtr10_bas_s_jump_lp"
    non_bas_match = resolve_action_for_role("6jln01_bas_jump_lp", "hang_action", ["6jln01_std_jump_lp", "6jln01_std_jump"], charcode="6jln01")
    assert non_bas_match.source == "6jln01_std_jump_lp"


def test_asbr_charcode_catalog_and_messageinfo_fallback(tmp_path: Path) -> None:
    cat = load_charcode_catalog()
    assert len(cat["characters"]) >= 135

    ykk = lookup_character("4ykk01")
    assert ykk.name == "Yukako Yamagishi"
    assert ykk.part == "Part 4"

    rhn = lookup_character("4rhn01")
    assert rhn.name == "Rohan Kishibe"

    # Verified distinction: 7val01 is Valkyrie (Gyro's horse), 7vtn01 is Funny Valentine
    assert lookup_character("7val01").name == "Valkyrie"
    assert lookup_character("7val01").role == "Horse"
    assert lookup_character("7vtn01").name == "Funny Valentine"

    # DLC and Stand codes
    assert lookup_character("8wou01").name == "Wonder of U"
    assert lookup_character("3stp01").name == "Star Platinum"

    # Unknown modded code falls back cleanly to Part label
    modded = lookup_character("5xyz01")
    assert modded.name is None
    assert modded.part == "Part 5"

    # Dropdown choice format & round-trip extraction
    choice = format_charcode_choice("4ykk01")
    assert choice == "4ykk01 — Yukako Yamagishi (Part 4)"
    assert extract_charcode_from_choice(choice) == "4ykk01"
    assert extract_charcode_from_choice("4rhn01") == "4rhn01"
    assert extract_charcode_from_choice("auto") == "auto"

    # Optional messageInfo.bin.xfbin binary parser extracts localized name override
    import struct

    msg_dir = tmp_path / "data" / "message" / "WIN64" / "eng"
    spc_dir = tmp_path / "data" / "spc"
    msg_dir.mkdir(parents=True)
    spc_dir.mkdir(parents=True)

    key_hash = crc32_bzip2("c_name_9mod01")
    swapped = struct.unpack("<I", struct.pack(">I", key_hash))[0]
    name_bytes = b"Custom Fighter\x00"
    # Build minimal NUCC binary: 16-byte header + entry(hash @ 16, pad @ 20, ptr @ 24 pointing +16 to offset 40)
    buf = bytearray(b"NUCC" + b"\x00" * 12)
    buf += struct.pack("<IIQ", swapped, 0, 16)  # hash at +0, pad at +4, ptr at +8 (pos=24 -> str at 24+16=40)
    buf += b"\x00" * 8
    buf += name_bytes
    (msg_dir / "messageInfo.bin.xfbin").write_bytes(bytes(buf))

    parsed = parse_message_info_names(spc_dir, ["9mod01"])
    assert parsed.get("9mod01") == "Custom Fighter"
    assert lookup_character("9mod01", extra_names=parsed).name == "Custom Fighter"


def test_scanner_filters_costume_only_skins_and_pairs_split_body_motion(tmp_path: Path) -> None:
    inp = tmp_path / "spc"
    inp.mkdir()
    # Normal character + costume-only skin (1jnt11 has bod1 only, no mot1)
    (inp / "1jnt01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")
    (inp / "1jnt01mot1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkAnm")
    (inp / "1jnt11bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")
    # Split character like Enrico Pucci: body is 6pci01, animations are 6pci02
    (inp / "6pci01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")
    (inp / "6pci02mot1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkAnm")
    (inp / "6pci11bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64 + b"nuccChunkClump")

    codes = detect_charcodes_in_directory(inp)
    assert codes == ["1jnt01", "6pci02"]
    assert "1jnt11" not in codes
    assert "6pci01" not in codes
    assert "6pci11" not in codes

    # Scanning 6pci02 automatically pairs sibling body 6pci01bod1.xfbin
    prof_02 = resolve_profile_for_charcode(load_profile("auto"), "6pci02")
    rep_02 = scan_xfbin_directory(inp, prof_02, cache_dir=tmp_path / "layla", inspect_chunks=True)
    assert not rep_02.errors
    assert rep_02.body_archive is not None and rep_02.body_archive.name == "6pci01bod1.xfbin"
    assert [a.name for a in rep_02.anim_archives] == ["6pci02mot1.xfbin"]

    # Manually typing 6pci01 also works via sibling motion fallback -> 6pci02mot1.xfbin
    prof_01 = resolve_profile_for_charcode(load_profile("auto"), "6pci01")
    rep_01 = scan_xfbin_directory(inp, prof_01, cache_dir=tmp_path / "layla", inspect_chunks=True)
    assert not rep_01.errors
    assert rep_01.body_archive is not None and rep_01.body_archive.name == "6pci01bod1.xfbin"
    assert [a.name for a in rep_01.anim_archives] == ["6pci02mot1.xfbin"]
