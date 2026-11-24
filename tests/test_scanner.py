"""Unit tests for XFBIN directory scanner and CRILAYLA/VFX handling."""

from __future__ import annotations

import struct
from pathlib import Path

from animforge.config import load_profile
from animforge.crilayla import CRILAYLA_MAGIC
from animforge.xfbin_scanner import scan_xfbin_directory


def _encode_literals_layla(payload: bytes) -> bytes:
    """Minimal literal-only CRILAYLA encoder for scanner discovery testing."""
    prefix, tail = payload[:0x100], payload[0x100:]
    raw_bytes: list[int] = []
    cur, bits = 0, 0
    for b in reversed(tail):
        for v, n in ((0, 1), (b, 8)):
            for i in range(n - 1, -1, -1):
                cur = (cur << 1) | ((v >> i) & 1)
                bits += 1
                if bits == 8:
                    raw_bytes.append(cur)
                    cur, bits = 0, 0
    if bits > 0:
        raw_bytes.append(cur << (8 - bits))
    stream = bytes(reversed(raw_bytes))
    return CRILAYLA_MAGIC + struct.pack("<II", len(tail), len(stream)) + stream + prefix


def test_scanner_excludes_vfx_and_decodes_synthetic_layla(tmp_path: Path) -> None:
    inp = tmp_path / "data"
    (inp / "spc").mkdir(parents=True)
    (inp / "spc_viewer").mkdir(parents=True)

    # Normal body, main motion, and CRILAYLA-compressed viewer archive
    (inp / "spc" / "4rhn01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 128 + b"nuccChunkClump")
    (inp / "spc" / "MAIN_4rhn01mot1.xfbin").write_bytes(b"NUCC" + b"\x00" * 128 + b"nuccChunkAnm")
    viewer_raw = b"NUCC" + b"\x00" * 260 + b"nuccChunkAnm"
    (inp / "spc_viewer" / "4rhn01viewer1.xfbin").write_bytes(_encode_literals_layla(viewer_raw))

    # VFX/anmofs/UI exclusions + other character files ignored
    (inp / "spc" / "4rhn01eff1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)
    (inp / "spc" / "4rhn01ghh1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)
    (inp / "spc" / "4rhn01_anmofs.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)
    (inp / "spc" / "2jtr01bod1.xfbin").write_bytes(b"NUCC" + b"\x00" * 64)

    profile = load_profile()
    report = scan_xfbin_directory(inp, profile, cache_dir=tmp_path / "layla_cache", inspect_chunks=True)

    assert not report.errors
    assert report.body_archive is not None
    assert report.body_archive.name == "4rhn01bod1.xfbin"
    assert [a.name for a in report.anim_archives] == ["MAIN_4rhn01mot1.xfbin", "4rhn01viewer1.xfbin"]
    assert report.anim_archives[1].magic == "CRILAYLA->NUCC"
    assert report.anim_archives[1].decompressed_path is not None

    excl = {a.name: a.role for a in report.excluded_archives}
    assert excl["4rhn01eff1.xfbin"] == "vfx_excluded"
    assert excl["4rhn01ghh1.xfbin"] == "vfx_excluded"
    assert excl["4rhn01_anmofs.xfbin"] == "anmofs"
