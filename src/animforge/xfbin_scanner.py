"""Recursive XFBIN discovery, header sniffing, CRILAYLA handling, and archive classification."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import CharacterProfile, resolve_profile_for_charcode
from .constants import get_app_root, get_local_app_data_dir
from .crilayla import CRILAYLA_MAGIC, CriLaylaError, decompress_crilayla, sniff_file_header, sniff_magic

_CHARCODE_BODY_RE = re.compile(r"^(?:main_)?([a-z0-9]{4,12}?)_?bod\d*(?:_.*)?\.xfbin$", re.IGNORECASE)
_CHARCODE_ANIM_RE = re.compile(r"^(?:main_)?([a-z0-9]{4,12}?)_?(?:mot\d+|viewer\d+|anm\w*)\.xfbin$", re.IGNORECASE)
_CC2_FAMILY_RE = re.compile(r"^([0-9][a-z]{3})\d{2}$", re.IGNORECASE)


def _cc2_family_prefix(code: str) -> str | None:
    m = _CC2_FAMILY_RE.match((code or "").strip().lower())
    return m.group(1) if m else None


@dataclass
class DiscoveredArchive:
    path: str
    relative_path: str
    name: str
    size_bytes: int
    sha256: str
    magic: str
    decompressed_path: str | None
    role: str  # "body" | "animation" | "anmofs" | "vfx_excluded" | "other_ignored" | "error"
    reason: str
    chunk_counts: dict[str, int] = field(default_factory=dict)
    anm_names: list[str] = field(default_factory=list)


@dataclass
class ScanReport:
    input_dir: str
    charcode: str
    body_archive: DiscoveredArchive | None
    anim_archives: list[DiscoveredArchive]
    excluded_archives: list[DiscoveredArchive]
    other_archives: list[DiscoveredArchive]
    errors: list[str]
    detected_charcodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_dir": self.input_dir,
            "charcode": self.charcode,
            "detected_charcodes": list(self.detected_charcodes),
            "body_archive": asdict(self.body_archive) if self.body_archive else None,
            "anim_archives": [asdict(a) for a in self.anim_archives],
            "excluded_archives": [asdict(a) for a in self.excluded_archives],
            "other_archives": [asdict(a) for a in self.other_archives],
            "errors": list(self.errors),
        }


def detect_charcodes_in_directory(input_dir: str | Path) -> list[str]:
    """Recursively discover animatable CC2/ASBR character codes (excluding costume-only body skins with no animations)."""
    root = Path(input_dir)
    if not root.is_dir():
        return []

    body_codes: set[str] = set()
    anim_codes: set[str] = set()
    for p in root.rglob("*.xfbin"):
        if not p.is_file():
            continue
        low = p.name.lower()
        if any(ex in low for ex in ("anmofs", "eff", "ghh", "_f.", "_f_", "col")):
            continue
        mb = _CHARCODE_BODY_RE.match(low)
        if mb:
            body_codes.add(mb.group(1))
            continue
        ma = _CHARCODE_ANIM_RE.match(low)
        if ma:
            anim_codes.add(ma.group(1))

    body_families = {_cc2_family_prefix(c) for c in body_codes} - {None}

    # Keep codes that have motion archives AND either an exact body or a sibling-family body (e.g. 6pci02 + 6pci01bod1)
    animatable: list[str] = []
    orphan_anim: list[str] = []
    for c in sorted(anim_codes):
        fam = _cc2_family_prefix(c)
        if c in body_codes or (fam is not None and fam in body_families):
            animatable.append(c)
        else:
            orphan_anim.append(c)

    if animatable or orphan_anim:
        return animatable + orphan_anim
    # Fallback only when a folder contains body archives and zero motion archives anywhere
    return sorted(body_codes)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _ensure_xfbin_lib_on_path() -> bool:
    app_root = get_app_root()
    candidates: list[Path] = []
    env_a = os.environ.get("ANIMFORGE_ADDON_DIR")
    if env_a:
        candidates.append(Path(env_a) / "xfbin_lib")
    candidates.extend([
        app_root / "vendor" / "xfbin_lib",
        app_root / "vendor" / "Blender-XFBIN-Importer" / "xfbin_lib",
    ])
    appdata = os.environ.get("APPDATA")
    if appdata:
        bf_cfg = Path(appdata) / "Blender Foundation" / "Blender"
        if bf_cfg.is_dir():
            candidates.extend(sorted(bf_cfg.glob("*/scripts/addons/Blender-XFBIN-Importer/xfbin_lib"), reverse=True))
    for candidate in candidates:
        if candidate.is_dir():
            s = str(candidate)
            if s not in sys.path:
                sys.path.insert(0, s)
            return True
    return False


def inspect_nucc_chunks_basic(data: bytes) -> dict[str, int]:
    """Count top-level chunk type occurrences in a NUCC buffer (via xfbin_lib if available, or signature scan)."""
    if _ensure_xfbin_lib_on_path():
        try:
            from xfbin.xfbin_reader import read_xfbin_from_bytes  # type: ignore
        except Exception:
            read_xfbin_from_bytes = None
        if read_xfbin_from_bytes is not None:
            try:
                xf = read_xfbin_from_bytes(data)
                counts: dict[str, int] = {}
                for pg in xf.pages:
                    for ch in pg.chunks:
                        name = type(ch).__name__
                        counts[name] = counts.get(name, 0) + 1
                return counts
            except Exception:
                pass

    # Lightweight deterministic fallback when xfbin_lib isn't loaded (e.g. standalone python env without numpy)
    counts = {}
    for sig in (b"nuccChunkClump", b"nuccChunkAnm", b"nuccChunkCoord", b"nuccChunkParticle", b"nuccChunkBillboard", b"nuccChunkTrail"):
        cnt = data.count(sig)
        if cnt:
            counts[sig.decode("ascii")] = cnt
    return counts


def _inspect_xfbin_file(path: Path) -> tuple[dict[str, int], list[str]]:
    data = path.read_bytes()
    if _ensure_xfbin_lib_on_path():
        try:
            from xfbin.xfbin_reader import read_xfbin  # type: ignore

            xf = read_xfbin(str(path))
            counts: dict[str, int] = {}
            anm_names: list[str] = []
            for pg in xf.pages:
                for ch in pg.chunks:
                    cname = type(ch).__name__
                    counts[cname] = counts.get(cname, 0) + 1
                    if cname == "NuccChunkAnm" and getattr(ch, "name", None):
                        anm_names.append(str(ch.name))
            return counts, anm_names
        except Exception:
            pass
    return inspect_nucc_chunks_basic(data), []


def scan_xfbin_directory(
    input_dir: str | Path,
    profile: CharacterProfile,
    *,
    cache_dir: Path | None = None,
    inspect_chunks: bool = True,
) -> ScanReport:
    """Recursively discover, sniff, classify, and hash XFBIN files in sorted order (supports auto-detection)."""
    root = Path(input_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {root}")

    detected_codes = detect_charcodes_in_directory(root)
    raw_charcode = profile.charcode.lower().strip()
    if raw_charcode in ("", "auto", "*"):
        charcode = detected_codes[0] if detected_codes else "*"
        report_charcode = detected_codes[0] if detected_codes else "custom"
    else:
        charcode = raw_charcode
        report_charcode = profile.charcode

    body_patterns = [p.lower() for p in profile.discovery.get("body_archive_patterns", ["bod1", "bod"])]
    include_tags = [t.lower() for t in profile.discovery.get("anim_include_tags", ["anm", "mot", "viewer"])]
    exclude_tags = [t.lower() for t in profile.discovery.get("anim_exclude_tags", ["anmofs", "eff", "ghh"])]

    glob_pat = "*.xfbin" if charcode == "*" else f"*{charcode}*.xfbin"
    candidates = sorted(
        (p for p in root.rglob(glob_pat) if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix().lower(),
    )

    layla_dir = (cache_dir or (get_local_app_data_dir() / "layla_cache"))
    body_archive: DiscoveredArchive | None = None
    anim_archives: list[DiscoveredArchive] = []
    excluded_archives: list[DiscoveredArchive] = []
    other_archives: list[DiscoveredArchive] = []
    errors: list[str] = []

    for path in candidates:
        rel = path.relative_to(root).as_posix()
        lower_name = path.name.lower()
        size = path.stat().st_size
        sha = _sha256_file(path)
        magic, _ = sniff_file_header(path)
        active_path = path
        decomp_path_str: str | None = None

        if magic == "CRILAYLA":
            try:
                raw = decompress_crilayla(path.read_bytes())
                inner_magic = sniff_magic(raw[:16])
                if inner_magic != "NUCC":
                    errors.append(f"{rel}: CRILAYLA decoded to unexpected header {inner_magic}")
                    excluded_archives.append(
                        DiscoveredArchive(str(path), rel, path.name, size, sha, f"CRILAYLA->{inner_magic}", None, "error", "Decompressed payload is not NUCC")
                    )
                    continue
                layla_dir.mkdir(parents=True, exist_ok=True)
                out_p = layla_dir / f"{sha[:16]}_{path.name}"
                if not out_p.exists():
                    out_p.write_bytes(raw)
                active_path = out_p
                decomp_path_str = str(out_p)
                magic = "CRILAYLA->NUCC"
            except CriLaylaError as exc:
                errors.append(f"{rel}: {exc}")
                excluded_archives.append(
                    DiscoveredArchive(str(path), rel, path.name, size, sha, magic, None, "error", str(exc))
                )
                continue
        elif magic != "NUCC":
            other_archives.append(
                DiscoveredArchive(str(path), rel, path.name, size, sha, magic, None, "other_ignored", "Non-NUCC header")
            )
            continue

        chunk_counts: dict[str, int] = {}
        anm_names: list[str] = []
        non_anim_hint = any(t in lower_name for t in ("_f.", "_f_", "col", "tex", "ui", "snd", "hud", "prm"))
        if inspect_chunks and ("bod" in lower_name or any(t in lower_name for t in include_tags + exclude_tags) or not non_anim_hint):
            chunk_counts, anm_names = _inspect_xfbin_file(active_path)

        # Classification
        is_body_name = any(pat in lower_name for pat in body_patterns) and "_f" not in lower_name and "col" not in lower_name
        has_clump_body = (
            charcode == "*"
            and chunk_counts.get("NuccChunkClump", chunk_counts.get("nuccChunkClump", 0)) > 0
            and chunk_counts.get("NuccChunkAnm", chunk_counts.get("nuccChunkAnm", 0)) == 0
            and "_f" not in lower_name
            and not any(tag in lower_name for tag in exclude_tags)
        )
        has_anm_chunks = (chunk_counts.get("NuccChunkAnm", 0) > 0 or chunk_counts.get("nuccChunkAnm", 0) > 0)

        if is_body_name or has_clump_body:
            rec = DiscoveredArchive(
                str(path), rel, path.name, size, sha, magic, decomp_path_str,
                "body", "Matched body archive pattern", chunk_counts, anm_names,
            )
            if body_archive is None or ("bod1" in lower_name and "bod1" not in body_archive.name.lower()):
                if body_archive is not None:
                    other_archives.append(
                        DiscoveredArchive(
                            body_archive.path, body_archive.relative_path, body_archive.name,
                            body_archive.size_bytes, body_archive.sha256, body_archive.magic,
                            body_archive.decompressed_path, "other_ignored",
                            "Secondary body/costume archive", body_archive.chunk_counts, body_archive.anm_names,
                        )
                    )
                body_archive = rec
            else:
                other_archives.append(
                    DiscoveredArchive(str(path), rel, path.name, size, sha, magic, decomp_path_str, "other_ignored", "Secondary body/costume archive", chunk_counts, anm_names)
                )
        elif "anmofs" in lower_name:
            excluded_archives.append(
                DiscoveredArchive(str(path), rel, path.name, size, sha, magic, decomp_path_str, "anmofs", "Offset table archive (handled separately from motion import)", chunk_counts, anm_names)
            )
        elif any(tag in lower_name for tag in ("eff", "ghh")):
            excluded_archives.append(
                DiscoveredArchive(str(path), rel, path.name, size, sha, magic, decomp_path_str, "vfx_excluded", "VFX/prop archive excluded from body motion conversion", chunk_counts, anm_names)
            )
        elif (any(tag in lower_name for tag in include_tags) or has_anm_chunks) and not any(ex in lower_name for ex in exclude_tags) and not non_anim_hint:
            anim_archives.append(
                DiscoveredArchive(str(path), rel, path.name, size, sha, magic, decomp_path_str, "animation", "Matched animation archive tags or NuccChunkAnm chunks", chunk_counts, anm_names)
            )
        else:
            other_archives.append(
                DiscoveredArchive(str(path), rel, path.name, size, sha, magic, decomp_path_str, "other_ignored", "Non-animation support/UI archive", chunk_counts, anm_names)
            )

    # Sibling-family fallback for split CC2 characters (e.g. 6pci02mot1 + 6pci01bod1, 4klq02mot1 + 4klq01bod1, or costume skin -> base mot1)
    fam = _cc2_family_prefix(charcode) if charcode != "*" else None
    if fam and (body_archive is None or not anim_archives):
        sib_candidates = sorted(
            (p for p in root.rglob(f"*{fam}*.xfbin") if p.is_file() and charcode not in p.name.lower()),
            key=lambda p: p.relative_to(root).as_posix().lower(),
        )

        def _prep_sibling(path: Path) -> tuple[str, int, str, str, Path, str | None] | None:
            rel = path.relative_to(root).as_posix()
            size = path.stat().st_size
            sha = _sha256_file(path)
            magic, _ = sniff_file_header(path)
            active_path = path
            decomp_path_str: str | None = None
            if magic == "CRILAYLA":
                try:
                    raw = decompress_crilayla(path.read_bytes())
                    if sniff_magic(raw[:16]) != "NUCC":
                        return None
                    layla_dir.mkdir(parents=True, exist_ok=True)
                    out_p = layla_dir / f"{sha[:16]}_{path.name}"
                    if not out_p.exists():
                        out_p.write_bytes(raw)
                    active_path = out_p
                    decomp_path_str = str(out_p)
                    magic = "CRILAYLA->NUCC"
                except CriLaylaError:
                    return None
            elif magic != "NUCC":
                return None
            return rel, size, sha, magic, active_path, decomp_path_str

        if body_archive is None:
            bod_sibs = [
                p for p in sib_candidates
                if _CHARCODE_BODY_RE.match(p.name.lower())
                and "_f" not in p.name.lower()
                and "col" not in p.name.lower()
            ]
            # Prefer <fam>01bod1, then bod1, then lowest variant number
            bod_sibs.sort(key=lambda p: (0 if p.name.lower().startswith(f"{fam}01bod1") else (1 if "bod1" in p.name.lower() else 2), p.name.lower()))
            for sp in bod_sibs:
                prep = _prep_sibling(sp)
                if prep is None:
                    continue
                rel, size, sha, magic, active_path, decomp_path_str = prep
                cc_cnt, anm_n = _inspect_xfbin_file(active_path) if inspect_chunks else ({}, [])
                body_archive = DiscoveredArchive(
                    str(sp), rel, sp.name, size, sha, magic, decomp_path_str,
                    "body", f"Sibling body skeleton archive for '{report_charcode}'", cc_cnt, anm_n,
                )
                break

        if not anim_archives:
            mot_sibs = [
                p for p in sib_candidates
                if _CHARCODE_ANIM_RE.match(p.name.lower())
                and not any(ex in p.name.lower() for ex in exclude_tags)
                and not any(t in p.name.lower() for t in ("_f.", "_f_", "col", "tex", "ui", "snd", "hud", "prm"))
            ]
            # Group by sibling charcode and prefer 01 -> 02 -> 00 -> lowest variant
            by_sib: dict[str, list[Path]] = {}
            for mp in mot_sibs:
                m = _CHARCODE_ANIM_RE.match(mp.name.lower())
                if m:
                    by_sib.setdefault(m.group(1), []).append(mp)
            if by_sib:
                def _sib_rank(code: str) -> tuple[int, str]:
                    suf = code[-2:]
                    order = {"01": 0, "02": 1, "00": 2}
                    return (order.get(suf, 3), code)
                best_code = sorted(by_sib.keys(), key=_sib_rank)[0]
                for mp in sorted(by_sib[best_code], key=lambda p: p.name.lower()):
                    prep = _prep_sibling(mp)
                    if prep is None:
                        continue
                    rel, size, sha, magic, active_path, decomp_path_str = prep
                    cc_cnt, anm_n = _inspect_xfbin_file(active_path) if inspect_chunks else ({}, [])
                    anim_archives.append(
                        DiscoveredArchive(
                            str(mp), rel, mp.name, size, sha, magic, decomp_path_str,
                            "animation", f"Sibling animation archive for '{report_charcode}' ({best_code})", cc_cnt, anm_n,
                        )
                    )

    if body_archive is None:
        errors.append(f"No body skeleton archive found matching charcode '{report_charcode}' and {body_patterns}")
    if not anim_archives:
        errors.append(f"No animation archives found matching charcode '{report_charcode}' and tags {include_tags}")

    return ScanReport(
        input_dir=str(root),
        charcode=report_charcode,
        detected_charcodes=detected_codes,
        body_archive=body_archive,
        anim_archives=anim_archives,
        excluded_archives=excluded_archives,
        other_archives=other_archives,
        errors=errors,
    )
