#!/usr/bin/env python3
"""Render one encoded NTV1 frame through the stream's decoder contract."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from encode_native_stream import (
    FRAME_BLOCK_BYTES,
    HEIGHT,
    PALETTE_BYTES,
    TILE,
    TILES,
    TILES_X,
    VIDEO_HEADER_BYTES,
    decode_supply,
)


def component(word: int, high_shift: int, low_shift: int) -> int:
    value = ((word >> high_shift) & 1) | ((word >> low_shift) & 0x1E)
    return round(value * 255 / 31)


def palette_color(word: int) -> tuple[int, int, int]:
    return (
        component(word, 14, 7),
        component(word, 13, 3),
        ((word >> 12) & 1 | ((word << 1) & 0x1E)) * 255 // 31,
    )


def tile_indices(tile: bytes) -> np.ndarray:
    output = np.zeros((TILE, TILE), dtype=np.uint8)
    for y in range(TILE):
        for x in range(TILE):
            base = (64 if x < 8 else 0) + y * 4
            bit = x if x < 8 else x - 8
            plane_bytes = (tile[base + 1], tile[base], tile[base + 3], tile[base + 2])
            value = sum(((byte >> bit) & 1) << plane for plane, byte in enumerate(plane_bytes))
            output[y, x] = value
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stream", type=Path)
    parser.add_argument("frame", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    previous = [bytes(128)] * TILES
    cache = {0: bytes(128)}
    palettes = [(0, 0, 0)] * (20 * 16)
    with args.stream.open("rb") as source:
        for frame_number in range(args.frame + 1):
            block = source.read(FRAME_BLOCK_BYTES)
            if len(block) != FRAME_BLOCK_BYTES:
                raise RuntimeError("frame is outside the stream")
            (
                magic, encoded, flags, used, count, map_offset, supply_offset,
                _, primary_count, continuation_offset, _,
            ) = struct.unpack_from(">4sHHHHHHIHH8s", block)
            if magic != b"NTV1" or encoded != frame_number:
                raise RuntimeError("stream frame mismatch")
            if continuation_offset == 0:  # version-1 streams before aux reuse
                primary_count = count
            if flags & 1:
                words = struct.unpack_from(">320H", block, VIDEO_HEADER_BYTES)
                palettes = [palette_color(word) for word in words]
            tile_map = struct.unpack_from(f">{TILES}H", block, map_offset)
            current = [cache.get(slot, bytes(128)) for slot in tile_map]
            offset = supply_offset
            for supply_index in range(count):
                if supply_index == primary_count:
                    offset = continuation_offset
                position, destination, mode, length = struct.unpack_from(">HHBB", block, offset)
                payload = block[offset + 6 : offset + 6 + length]
                decoded = decode_supply(previous[position], mode, payload)
                current[position] = decoded
                cache[destination] = decoded
                offset += 6 + length + (length & 1)
            previous = current

    rgb = np.zeros((HEIGHT, TILES_X * TILE, 3), dtype=np.uint8)
    for position, tile in enumerate(previous):
        ty, tx = divmod(position, TILES_X)
        palette_id = (ty // 4) * 5 + (tx // 4)
        indices = tile_indices(tile)
        colors = np.asarray(palettes[palette_id * 16 : palette_id * 16 + 16], dtype=np.uint8)
        rgb[ty * TILE : (ty + 1) * TILE, tx * TILE : (tx + 1) * TILE] = colors[indices]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(args.output)
    print(f"rendered frame {args.frame} ({used} bytes, {count} supplies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
