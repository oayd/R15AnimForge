"""Unit tests for CRI LAYLA sniffer and decompressor."""

from __future__ import annotations

import struct

import pytest

from animforge.crilayla import CRILAYLA_MAGIC, NUCC_MAGIC, CriLaylaError, decompress_crilayla, sniff_magic


class _BitWriter:
    """Pairs with _ReverseBitReader: writes bits MSB-first and reverses bytes at flush."""

    def __init__(self) -> None:
        self._bytes: list[int] = []
        self._cur = 0
        self._bits = 0

    def put_bits(self, value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            bit = (value >> i) & 1
            self._cur = (self._cur << 1) | bit
            self._bits += 1
            if self._bits == 8:
                self._bytes.append(self._cur)
                self._cur = 0
                self._bits = 0

    def finalize(self) -> bytes:
        if self._bits > 0:
            self._bytes.append(self._cur << (8 - self._bits))
        return bytes(reversed(self._bytes))


def _encode_crilayla_with_backref(payload: bytes) -> bytes:
    """Synthetic encoder using 0x100 raw prefix + literal/LZ backrefs written backwards."""
    assert len(payload) > 0x100
    prefix = payload[:0x100]
    tail = payload[0x100:]
    uncompressed_size = len(tail)

    bw = _BitWriter()
    # Walk tail backward from end -> index 0
    pos = len(tail) - 1
    while pos >= 0:
        # Try a 4-byte backreference from further right if available
        if pos >= 3 and pos + 6 < len(tail) and tail[pos - 3 : pos + 1] == tail[pos + 3 : pos + 7]:
            bw.put_bits(1, 1)
            # back_offset in decoder = cursor + raw_off + 3 -> at tail[pos+3+3]? Wait: cursor = out_end - bytes_written
            # out[cursor + delta] = tail[pos + (delta)], so delta = (pos+6) - pos = 6 => raw_off = 6 - 3 = 3
            bw.put_bits(3, 13)
            # match_len = 3 + 1 => 2-bit VLE = 1 (< 3 stops immediately)
            bw.put_bits(1, 2)
            pos -= 4
        else:
            bw.put_bits(0, 1)
            bw.put_bits(tail[pos], 8)
            pos -= 1

    stream = bw.finalize()
    header = CRILAYLA_MAGIC + struct.pack("<II", uncompressed_size, len(stream))
    return header + stream + prefix


def test_sniff_magic() -> None:
    assert sniff_magic(b"NUCC\x00\x00\x00\x00\x11\x22\x33\x44\x55\x66\x77\x88") == "NUCC"
    assert sniff_magic(b"CRILAYLA\x00\x01\x00\x00\x10\x00\x00\x00") == "CRILAYLA"
    assert sniff_magic(b"BAD!" * 4) == "UNKNOWN"


def test_crilayla_roundtrip_literals_and_backref() -> None:
    # Build synthetic NUCC payload (> 0x100 bytes) with a repeated block so both literals and backrefs exercise
    body = b"NUCC" + b"\x00" * 252 + b"ABCD" + b"XYZ" + b"ABCD" + b"12345" + bytes(range(40))
    compressed = _encode_crilayla_with_backref(body)
    assert sniff_magic(compressed[:16]) == "CRILAYLA"
    decoded = decompress_crilayla(compressed)
    assert decoded == body
    assert decoded[:4] == NUCC_MAGIC


def test_crilayla_truncated_raises() -> None:
    with pytest.raises(CriLaylaError):
        decompress_crilayla(b"CRILAYLA\x10\x00\x00\x00\x20\x00\x00\x00short")
