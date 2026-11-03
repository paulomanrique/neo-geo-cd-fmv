#!/usr/bin/env python3
"""Author a media-free emulator fixture that repeatedly recycles 15 tile slots."""

import argparse
import hashlib
import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from encode_native_stream import palette_bytes, render_indexed, write_attributes
from palette_fit import color_grid
from video_encoder import VideoEncoder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    options = Namespace(metric="oklab", palette_stability=0, palette_fitter="rgb",
                        full_cache=True, differential_maps=True, denoise="0")
    encoder = VideoEncoder(options)
    grid, _ = color_grid()
    colors = [tuple(int(c) for c in grid[(v << 10) | (v << 5) | v]) for v in range(15)]
    palettes = [colors] * 20
    packed = palette_bytes(palettes)
    rng = np.random.default_rng(48)
    stream = bytearray()
    previous = np.ones((224, 320), dtype=np.uint8)
    # Keep the real stream slot IDs and memory layout, but force frequent eviction.
    with patch("encode_native_stream.SPRITE_CACHE_TILES", 16):
        for number in range(96):
            target = previous.copy()
            if number % 4 == 0:
                target[:16, :64] = rng.integers(1, 16, (16, 64), dtype=np.uint8)
            elif number % 4 == 1:
                target[:16, 16:64] = np.tile(target[:16, :16], (1, 3))
            else:
                for column in range(4):
                    target[(number + column) % 16, column * 16 + number % 16] = 1 + number % 15
            rgb = render_indexed(target, palettes)
            stream.extend(encoder.frame(rgb, target, palettes, packed).block)
            previous = target
    (args.output / "STREAM.DAT").write_bytes(stream)
    (args.output / "AUDIO0.PCM").write_bytes(b"\x08" * 1048576)
    write_attributes(args.output / "tile_attributes.bin")
    constants = dict(VIDEO_FRAME_COUNT=96, VIDEO_FPS_NUM=150000, VIDEO_REFRESH_DEN=591856,
                     VIDEO_COLUMNS=20, VIDEO_ROWS=14, VIDEO_TILES_PER_FRAME=280,
                     VIDEO_PALETTES_PER_FRAME=20, VIDEO_PALETTE_BYTES=640,
                     VIDEO_PACKET_BYTES=8192, VIDEO_AUX_DATA_OFFSET=8832,
                     VIDEO_FRAME_BLOCK_BYTES=10240, VIDEO_MAX_SUPPLIES=64)
    (args.output / "video.inc").write_text("".join(f"{k} equ {v}\n" for k, v in constants.items()))
    (args.output / "video.json").write_text(json.dumps({
        "frame_count": 96, "fps": 15, "width": 320, "height": 224,
        "duration_seconds": 6.4, "adpcm_bytes": 59259,
        "source_sha256": hashlib.sha256(b"cache-stress-seed-48").hexdigest(),
        "average_supplies": encoder.stats["total_supplies"] / 96,
        "average_used_video_bytes": encoder.stats["total_used"] / 96,
        "synthetic_cache_capacity": 16,
    }, indent=2) + "\n")
    print(f"Authored 96 frames with {encoder.stats['total_supplies']} uploads in a 16-slot working set")


if __name__ == "__main__":
    main()
