"""JoJo ASBR / CC2 charcode-to-character-name resolution (offline catalog + optional messageInfo.bin.xfbin)."""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .constants import get_app_root
from .crilayla import decompress_crilayla

_CC2_CODE_RE = re.compile(r"^(?P<part>[0-9])(?P<abbr>[a-z]{3})(?P<var>\d{2})$", re.IGNORECASE)
_CHOICE_PREFIX_RE = re.compile(r"^([A-Za-z0-9_*]+)\s*(?:[—–\-]|$)")


@dataclass(frozen=True)
class CharacterInfo:
    charcode: str
    name: str | None
    part: str | None = None
    role: str | None = None

    @property
    def label(self) -> str:
        """Human-friendly display label, e.g. 'Yukako Yamagishi (Part 4)'."""
        if not self.name:
            return f"{self.part}" if self.part else self.charcode
        tags: list[str] = []
        if self.part:
            tags.append(self.part)
        if self.role and self.role not in ("Playable", "DLC"):
            tags.append(self.role)
        elif self.role == "DLC":
            tags.append("DLC")
        return f"{self.name} ({' • '.join(tags)})" if tags else self.name


@lru_cache(maxsize=4)
def _load_catalog_cached(catalog_path: str) -> dict[str, Any]:
    p = Path(catalog_path)
    if not p.is_file():
        return {"parts": {}, "characters": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"parts": {}, "characters": {}}


def load_charcode_catalog(app_root: Path | None = None) -> dict[str, Any]:
    root = app_root or get_app_root()
    return _load_catalog_cached(str(root / "data" / "asbr_charcodes.json"))


def crc32_bzip2(text: str) -> int:
    """Compute CRC-32/BZIP2 (poly 0x04C11DB7, non-reflected, init/xorout 0xFFFFFFFF) used by ASBR messageInfo."""
    crc = 0xFFFFFFFF
    for b in text.encode("utf-8"):
        crc ^= b << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


def _find_message_info_file(input_dir: Path) -> Path | None:
    """Locate messageInfo.bin.xfbin (preferring English) near input_dir (up to 3 ancestor levels)."""
    cur = input_dir.resolve() if input_dir.exists() else input_dir
    if cur.is_file():
        cur = cur.parent
    rel_candidates = (
        Path("message/WIN64/eng/messageInfo.bin.xfbin"),
        Path("data/message/WIN64/eng/messageInfo.bin.xfbin"),
        Path("message/eng/messageInfo.bin.xfbin"),
        Path("WIN64/eng/messageInfo.bin.xfbin"),
        Path("eng/messageInfo.bin.xfbin"),
        Path("messageInfo.bin.xfbin"),
    )
    ancestors = [cur] + list(cur.parents[:3])
    for base in ancestors:
        for rel in rel_candidates:
            cand = base / rel
            if cand.is_file():
                return cand
    return None


def parse_message_info_names(
    input_dir: str | Path,
    charcodes: Iterable[str],
) -> dict[str, str]:
    """Read localized display names for charcodes from messageInfo.bin.xfbin if present near input_dir.

    ASBR stores message entries inside a NUCC `nuccChunkBinary` payload as 40-byte records containing
    endian-swapped CRC-32/BZIP2 hashes of keys such as `c_name_<charcode>` and relative UTF-8 string pointers.
    Returns an empty dict cleanly if no messageInfo file exists or format differs.
    """
    codes = [c.strip().lower() for c in charcodes if c and c.strip().lower() not in ("auto", "*")]
    if not codes:
        return {}
    msg_path = _find_message_info_file(Path(input_dir))
    if msg_path is None:
        return {}

    try:
        raw = msg_path.read_bytes()
        if raw[:8] == b"CRILAYLA":
            raw = decompress_crilayla(raw)
        if not raw.startswith(b"NUCC"):
            return {}
    except Exception:
        return {}

    # Build lookup of both direct and byte-swapped CRC-32/BZIP2 hashes for candidate name keys
    hash_to_code: dict[int, str] = {}
    for cc in codes:
        for key in (f"c_name_{cc}", f"chara_name_{cc}", f"c_name_{cc[:4]}01"):
            h = crc32_bzip2(key)
            swapped = struct.unpack("<I", struct.pack(">I", h))[0]
            hash_to_code.setdefault(h, cc)
            hash_to_code.setdefault(swapped, cc)

    # Scan binary for 4-byte hash hits followed by a valid relative uint64 pointer to a UTF-8 string
    found: dict[str, str] = {}
    n = len(raw)
    pos = 0
    while pos + 24 <= n and len(found) < len(codes):
        val = struct.unpack_from("<I", raw, pos)[0]
        cc = hash_to_code.get(val)
        if cc and cc not in found:
            # In ASBR messageInfo entries, string pointer sits at offset +8 or +16 relative to the pointer field
            for ptr_off in (8, 12, 16):
                if pos + ptr_off + 8 > n:
                    continue
                rel = struct.unpack_from("<Q", raw, pos + ptr_off)[0]
                str_pos = pos + ptr_off + rel
                if pos < str_pos < n - 1 and rel < 16_000_000:
                    end = raw.find(b"\x00", str_pos, min(n, str_pos + 128))
                    if end > str_pos:
                        try:
                            text = raw[str_pos:end].decode("utf-8").strip()
                        except UnicodeDecodeError:
                            text = ""
                        if 2 <= len(text) <= 48 and all(ord(ch) >= 32 for ch in text):
                            found[cc] = text
                            break
        pos += 4
    return found


def lookup_character(
    charcode: str,
    *,
    extra_names: dict[str, str] | None = None,
    app_root: Path | None = None,
) -> CharacterInfo:
    """Resolve a CC2 charcode (e.g. '4ykk01') to its human-readable character name, part, and role."""
    cc = (charcode or "").strip().lower()
    if not cc or cc in ("auto", "*"):
        return CharacterInfo(charcode="auto", name=None, part=None, role=None)

    cat = load_charcode_catalog(app_root=app_root)
    chars = cat.get("characters", {})
    parts = cat.get("parts", {})

    if extra_names and cc in extra_names and extra_names[cc].strip():
        meta = chars.get(cc, {})
        part = meta.get("part")
        if not part:
            m = _CC2_CODE_RE.match(cc)
            part = parts.get(m.group("part")) if m else None
        return CharacterInfo(
            charcode=cc,
            name=extra_names[cc].strip(),
            part=part,
            role=meta.get("role"),
        )

    if cc in chars:
        meta = chars[cc]
        return CharacterInfo(
            charcode=cc,
            name=meta.get("name"),
            part=meta.get("part"),
            role=meta.get("role"),
        )

    # Variant fallback: e.g. '3jtr10' -> check '3jtr01'
    m = _CC2_CODE_RE.match(cc)
    if m:
        base_cc = f"{m.group('part')}{m.group('abbr')}01"
        if base_cc in chars:
            meta = chars[base_cc]
            return CharacterInfo(
                charcode=cc,
                name=f"{meta.get('name')} ({m.group('var')})",
                part=meta.get("part"),
                role=meta.get("role"),
            )
        return CharacterInfo(
            charcode=cc,
            name=None,
            part=parts.get(m.group("part")),
            role=None,
        )

    return CharacterInfo(charcode=cc, name=None, part=None, role=None)


def format_charcode_choice(
    charcode: str,
    *,
    extra_names: dict[str, str] | None = None,
    app_root: Path | None = None,
) -> str:
    """Format a charcode for the GUI dropdown, e.g. '4ykk01 — Yukako Yamagishi (Part 4)'."""
    cc = extract_charcode_from_choice(charcode)
    if not cc or cc.lower() in ("auto", "*"):
        return "auto"
    info = lookup_character(cc, extra_names=extra_names, app_root=app_root)
    if info.name:
        return f"{info.charcode} — {info.label}"
    if info.part:
        return f"{info.charcode} — {info.part}"
    return info.charcode


def extract_charcode_from_choice(choice: str) -> str:
    """Extract raw charcode ('4ykk01' or 'auto') from either a formatted dropdown label or raw text."""
    s = (choice or "").strip()
    if not s:
        return "auto"
    if "—" in s:
        return s.split("—", 1)[0].strip()
    m = _CHOICE_PREFIX_RE.match(s)
    if m:
        return m.group(1).strip()
    return s
