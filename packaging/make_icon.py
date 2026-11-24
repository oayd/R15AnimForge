"""Generate a crisp multi-resolution Windows .ico (no external image libraries required)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _render_png(size: int) -> bytes:
    rows = bytearray()
    cx = cy = (size - 1) * 0.5
    r_outer = size * 0.44
    for y in range(size):
        rows.append(0)  # filter type 0
        for x in range(size):
            dx, dy = abs(x - cx), abs(y - cy)
            # Rounded square badge + geometric 'R15' forge anvil/diamond emblem
            in_badge = max(dx, dy) <= r_outer
            if not in_badge:
                rows.extend((0, 0, 0, 0))
                continue
            dist = (dx * dx + dy * dy) ** 0.5 / size
            in_diamond = (dx + dy) < size * 0.28 and (dx + dy) > size * 0.10
            in_core = dx < size * 0.06 and dy < size * 0.16
            if in_diamond or in_core:
                rows.extend((56, 189, 248, 255))  # bright cyan emblem
            elif dist > 0.38:
                rows.extend((30, 64, 175, 255))  # royal blue border
            else:
                rows.extend((15, 23, 42, 255))  # dark slate core
    raw = zlib.compress(bytes(rows), 9)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", raw) + _png_chunk(b"IEND", b"")


def write_ico(path: Path) -> None:
    sizes = (16, 32, 48, 256)
    images = [_render_png(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for s, img in zip(sizes, images):
        w_byte = 0 if s >= 256 else s
        entries.extend(struct.pack("<BBBBHHII", w_byte, w_byte, 0, 0, 1, 32, len(img), offset))
        offset += len(img)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(entries) + b"".join(images))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "animforge.ico"
    write_ico(out)
    print(f"Wrote {out}")
