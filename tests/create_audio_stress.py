#!/usr/bin/env python3
"""Create a silent-picture disc fixture with deterministic streamed PCM banks."""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from encode_native_stream import write_attributes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    count = 4500
    size = (count * 500000 + 405 - 1) // 405
    adpcm = np.random.default_rng(170).bytes((size + 1) // 2)
    preload, future = adpcm[:1048576], adpcm[1048576:]
    stream = bytearray()
    body = b"\0\0"
    for number in range(count):
        header = struct.pack(">4sHHHHHHIHH8s", b"NTV1", number, 2, 34, 0, 32, 34,
                             zlib.crc32(body), 0, 8832, bytes(8))
        start, end = number * 500000 // 810, (number + 1) * 500000 // 810
        audio = future[start:end]
        aux = struct.pack(">4sHHI", b"NTA1", number, len(audio), zlib.crc32(audio))
        stream.extend((header + body).ljust(8192, b"\0"))
        stream.extend((aux + audio).ljust(640, b"\x08") + bytes(1408))
    (args.output / "STREAM.DAT").write_bytes(stream)
    (args.output / "AUDIO0.PCM").write_bytes(preload)
    write_attributes(args.output / "tile_attributes.bin")
    constants = dict(VIDEO_FRAME_COUNT=count, VIDEO_FPS_NUM=150000, VIDEO_REFRESH_DEN=591856,
                     VIDEO_COLUMNS=20, VIDEO_ROWS=14, VIDEO_TILES_PER_FRAME=280,
                     VIDEO_PALETTES_PER_FRAME=20, VIDEO_PALETTE_BYTES=640,
                     VIDEO_PACKET_BYTES=8192, VIDEO_AUX_DATA_OFFSET=8832,
                     VIDEO_FRAME_BLOCK_BYTES=10240, VIDEO_MAX_SUPPLIES=64)
    (args.output / "video.inc").write_text("".join(f"{k} equ {v}\n" for k, v in constants.items()))
    (args.output / "video.json").write_text(json.dumps(dict(
        frame_count=count, fps=15, width=320, height=224, duration_seconds=count / 15,
        adpcm_bytes=len(adpcm), source_sha256=hashlib.sha256(adpcm).hexdigest()), indent=2) + "\n")
    print(f"Authored {count} black frames and {len(future)} streamed ADPCM bytes")


if __name__ == "__main__":
    main()
