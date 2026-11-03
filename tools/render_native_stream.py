#!/usr/bin/env python3
"""Render one encoded NTV1 frame through the stream's decoder contract."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from encode_native_stream import TILE


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

    from itertools import islice
    from export_native_video import frames

    if args.frame < 0:
        parser.error("frame must be nonnegative")
    rgb = next(islice(frames(args.stream), args.frame, args.frame + 1), None)
    if rgb is None:
        parser.error("frame is outside the stream")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(args.output)
    print(f"rendered frame {args.frame}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
