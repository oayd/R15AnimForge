"""CRI LAYLA decompression and header sniffing for CyberConnect2 XFBIN files."""

from __future__ import annotations

import struct
from pathlib import Path

CRILAYLA_MAGIC = b"CRILAYLA"
NUCC_MAGIC = b"NUCC"
_PREFIX_RAW_SIZE = 0x100


class CriLaylaError(ValueError):
    """Raised when CRILAYLA input is malformed or truncated."""


def sniff_magic(head: bytes) -> str:
    """Classify the first 16+ bytes of a file or buffer."""
    if len(head) >= 8 and head[:8] == CRILAYLA_MAGIC:
        return "CRILAYLA"
    if len(head) >= 4 and head[:4] == NUCC_MAGIC:
        return "NUCC"
    return "UNKNOWN"


def sniff_file_header(path: str | Path) -> tuple[str, bytes]:
    with Path(path).open("rb") as fh:
        head = fh.read(16)
    return sniff_magic(head), head


class _ReverseBitReader:
    """Reads bits MSB-first walking backward from the end of a byte slice."""

    def __init__(self, buf: memoryview) -> None:
        self._buf = buf
        self._byte_pos = len(buf) - 1
        self._bit_pool = 0
        self._bits_left = 0

    def get_bits(self, count: int) -> int:
        val = 0
        rem = count
        while rem > 0:
            if self._bits_left == 0:
                if self._byte_pos < 0:
                    raise CriLaylaError("Unexpected end of CRILAYLA compressed bitstream")
                self._bit_pool = self._buf[self._byte_pos]
                self._byte_pos -= 1
                self._bits_left = 8
            take = rem if rem < self._bits_left else self._bits_left
            shift = self._bits_left - take
            val = (val << take) | ((self._bit_pool >> shift) & ((1 << take) - 1))
            self._bits_left -= take
            rem -= take
        return val


def decompress_crilayla(data: bytes) -> bytes:
    """Decompress a CRI CRILAYLA-compressed payload into raw bytes."""
    if len(data) < 16 + _PREFIX_RAW_SIZE or data[:8] != CRILAYLA_MAGIC:
        raise CriLaylaError("Invalid CRILAYLA header")

    uncompressed_size, compressed_size = struct.unpack_from("<II", data, 8)
    bitstream_end = 16 + compressed_size
    if len(data) < bitstream_end + _PREFIX_RAW_SIZE:
        raise CriLaylaError(
            f"Truncated CRILAYLA buffer: expected >= {bitstream_end + _PREFIX_RAW_SIZE} bytes, got {len(data)}"
        )

    total_size = uncompressed_size + _PREFIX_RAW_SIZE
    out = bytearray(total_size)
    # First 0x100 bytes are stored uncompressed immediately after the bitstream.
    out[:_PREFIX_RAW_SIZE] = data[bitstream_end : bitstream_end + _PREFIX_RAW_SIZE]

    reader = _ReverseBitReader(memoryview(data)[16:bitstream_end])
    vle_lens = (2, 3, 5, 8)
    bytes_written = 0
    out_end = total_size - 1

    while bytes_written < uncompressed_size:
        flag = reader.get_bits(1)
        if flag == 0:
            out[out_end - bytes_written] = reader.get_bits(8)
            bytes_written += 1
            continue

        back_offset = out_end - bytes_written + reader.get_bits(13) + 3
        match_len = 3
        for bits in vle_lens:
            delta = reader.get_bits(bits)
            match_len += delta
            if delta != (1 << bits) - 1:
                break
        else:
            while True:
                delta = reader.get_bits(8)
                match_len += delta
                if delta != 255:
                    break

        for _ in range(match_len):
            if bytes_written >= uncompressed_size:
                break
            if back_offset >= total_size:
                raise CriLaylaError(f"CRILAYLA back-reference out of range: {back_offset} >= {total_size}")
            out[out_end - bytes_written] = out[back_offset]
            back_offset -= 1
            bytes_written += 1

    return bytes(out)
