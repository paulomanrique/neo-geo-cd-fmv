#!/usr/bin/env python3
"""Measure Neo Geo-native tile codec candidates on source videos.

This is an analysis encoder, not the runtime format. It quantizes 320x224 RGB
frames into twenty spatial 15-color palettes held for four frames, converts the
result to native 16x16/4bpp sprite tiles, and lets a per-tile optimizer choose
the least expensive simple command. Every reported mode has a bounded decoder:
raw, byte RLE, XOR+zero RLE, bitplane update, previous-frame motion reference,
or an exact graphics-cache reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


WIDTH = 320
HEIGHT = 224
TILE = 16
TILES_X = WIDTH // TILE
TILES_Y = HEIGHT // TILE
TILES_PER_FRAME = TILES_X * TILES_Y
PALETTE_REGION = 64
PALETTE_COLUMNS = math.ceil(WIDTH / PALETTE_REGION)
PALETTE_ROWS = math.ceil(HEIGHT / PALETTE_REGION)
PALETTES = PALETTE_COLUMNS * PALETTE_ROWS
PALETTE_HOLD = 4
PALETTE_BYTES = PALETTES * 16 * 2
CHANGE_BITMAP_BYTES = (TILES_PER_FRAME + 7) // 8
GRAPHICS_CACHE_TILES = 32768
SOFTWARE_CACHE_TILES = 4096
VIDEO_FILTER = (
    f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
    f"crop={WIDTH}:{HEIGHT},fps={{fps}}"
)


def hungarian(cost: list[list[int]]) -> list[int]:
    n = len(cost)
    u = [0] * (n + 1)
    v = [0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for row in range(1, n + 1):
        p[0] = row
        column0 = 0
        minimum = [10**18] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[column0] = True
            row0 = p[column0]
            delta = 10**18
            column1 = 0
            for column in range(1, n + 1):
                if used[column]:
                    continue
                current = cost[row0 - 1][column - 1] - u[row0] - v[column]
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(n + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break
    result = [0] * n
    for column in range(1, n + 1):
        result[p[column] - 1] = column - 1
    return result


def video_frames(path: Path, fps: int):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vf", VIDEO_FILTER.format(fps=fps), "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    frame_bytes = WIDTH * HEIGHT * 3
    try:
        while True:
            data = process.stdout.read(frame_bytes)
            if not data:
                break
            if len(data) != frame_bytes:
                raise RuntimeError("ffmpeg returned a partial RGB frame")
            yield np.frombuffer(data, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
    finally:
        process.stdout.close()
        return_code = process.wait()
        if return_code:
            raise subprocess.CalledProcessError(return_code, command)


def quantize_group(
    frames: list[np.ndarray], previous_palettes: list[list[tuple[int, int, int]]] | None
) -> tuple[list[np.ndarray], list[list[tuple[int, int, int]]]]:
    indexed = [np.zeros((HEIGHT, WIDTH), dtype=np.uint8) for _ in frames]
    palettes: list[list[tuple[int, int, int]]] = []
    for py in range(PALETTE_ROWS):
        for px in range(PALETTE_COLUMNS):
            left = px * PALETTE_REGION
            top = py * PALETTE_REGION
            right = min(left + PALETTE_REGION, WIDTH)
            bottom = min(top + PALETTE_REGION, HEIGHT)
            block_width = right - left
            block_height = bottom - top
            combined = np.concatenate(
                [frame[top:bottom, left:right] for frame in frames], axis=1
            )
            image = Image.fromarray(combined, "RGB").quantize(
                colors=15, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
            )
            raw_palette = image.getpalette()[:45]
            raw_palette.extend([0] * (45 - len(raw_palette)))
            colors = [tuple(raw_palette[i * 3 : i * 3 + 3]) for i in range(15)]
            current = np.asarray(image, dtype=np.uint8)

            palette_id = py * PALETTE_COLUMNS + px
            if previous_palettes is not None:
                old = previous_palettes[palette_id]
                cost = [
                    [sum((a - b) ** 2 for a, b in zip(old_color, new_color)) for new_color in colors]
                    for old_color in old
                ]
                slot_to_current = hungarian(cost)
                current_to_slot = np.zeros(15, dtype=np.uint8)
                for slot, source in enumerate(slot_to_current):
                    current_to_slot[source] = slot
                colors = [colors[source] for source in slot_to_current]
                current = current_to_slot[current]
            palettes.append(colors)

            for frame_index, destination in enumerate(indexed):
                x0 = frame_index * block_width
                destination[top:bottom, left:right] = current[:, x0 : x0 + block_width] + 1
    return indexed, palettes


def tile_bytes(indexed: np.ndarray) -> list[bytes]:
    output: list[bytes] = []
    for ty in range(TILES_Y):
        for tx in range(TILES_X):
            tile = indexed[ty * TILE : (ty + 1) * TILE, tx * TILE : (tx + 1) * TILE]
            encoded = bytearray()
            for half in (1, 0):
                for y in range(TILE):
                    pixels = tile[y, half * 8 : half * 8 + 8]
                    for plane in (1, 0, 3, 2):
                        value = 0
                        for x, pixel in enumerate(pixels):
                            value |= ((int(pixel) >> plane) & 1) << x
                        encoded.append(value)
            assert len(encoded) == 128
            output.append(bytes(encoded))
    return output


def zero_rle_size(data: bytes) -> int:
    size = 0
    position = 0
    while position < len(data):
        if data[position] == 0:
            end = position + 1
            while end < len(data) and data[end] == 0 and end - position < 128:
                end += 1
            size += 1
        else:
            end = position + 1
            while end < len(data) and end - position < 128:
                if data[end] == 0 and end + 1 < len(data) and data[end + 1] == 0:
                    break
                end += 1
            size += 1 + end - position
        position = end
    return size


def packbits_size(data: bytes) -> int:
    size = 0
    position = 0
    while position < len(data):
        run = 1
        while position + run < len(data) and data[position + run] == data[position] and run < 128:
            run += 1
        if run >= 3:
            size += 2
            position += run
            continue
        start = position
        position += run
        while position < len(data) and position - start < 128:
            run = 1
            while position + run < len(data) and data[position + run] == data[position] and run < 3:
                run += 1
            if run >= 3:
                break
            position += run
        size += 1 + position - start
    return size


def xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def bitplane_update_size(previous: bytes, current: bytes) -> int:
    # Native layout has two 64-byte halves; each plane contributes 16 bytes in
    # each half. The two-byte command header carries the changed-plane and
    # raw/RLE masks. An RLE plane has one additional encoded-length byte.
    total = 2
    for plane_offset in range(4):
        old_plane = previous[plane_offset:64:4] + previous[64 + plane_offset : 128 : 4]
        new_plane = current[plane_offset:64:4] + current[64 + plane_offset : 128 : 4]
        if old_plane == new_plane:
            continue
        total += min(32, 1 + packbits_size(new_plane))
    return total


@dataclass
class Aggregate:
    frames: int = 0
    stream_bytes: int = 0
    palette_bytes: int = 0
    changed_tiles: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    uploads: int = 0
    peak_frame_bytes: int = 0
    peak_uploads: int = 0
    modes: Counter[str] | None = None
    mode_bytes: Counter[str] | None = None
    source_mae: float = 0.0
    source_tiles_mae_le_2: int = 0
    source_tiles_mae_le_4: int = 0
    source_tiles_mae_le_8: int = 0

    def __post_init__(self):
        self.modes = Counter()
        self.mode_bytes = Counter()


class TileCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.entries: OrderedDict[bytes, int] = OrderedDict()
        self.next_id = 0
        self.free_ids: list[int] = []

    def find(self, tile: bytes) -> int | None:
        tile_id = self.entries.get(tile)
        if tile_id is not None:
            self.entries.move_to_end(tile)
        return tile_id

    def insert(self, tile: bytes) -> int:
        existing = self.entries.get(tile)
        if existing is not None:
            self.entries.move_to_end(tile)
            return existing
        if len(self.entries) >= self.capacity:
            _, tile_id = self.entries.popitem(last=False)
        elif self.free_ids:
            tile_id = self.free_ids.pop()
        else:
            tile_id = self.next_id
            self.next_id += 1
        self.entries[tile] = tile_id
        return tile_id


def analyze(path: Path, fps: int) -> dict[str, object]:
    aggregate = Aggregate()
    cache = TileCache(GRAPHICS_CACHE_TILES)
    software_cache = TileCache(SOFTWARE_CACHE_TILES)
    previous_tiles = [bytes(128)] * TILES_PER_FRAME
    previous_available = [True] * TILES_PER_FRAME
    previous_rgb: np.ndarray | None = None
    previous_palettes = None
    group_rgb: list[np.ndarray] = []

    def consume(rgb_frames: list[np.ndarray]) -> None:
        nonlocal previous_tiles, previous_available, previous_rgb, previous_palettes
        indexed_frames, previous_palettes = quantize_group(rgb_frames, previous_palettes)
        for rgb, indexed in zip(rgb_frames, indexed_frames, strict=True):
            current_tiles = tile_bytes(indexed)
            current_available = [False] * TILES_PER_FRAME
            frame_bytes = CHANGE_BITMAP_BYTES
            uploads = 0
            aggregate.palette_bytes += PALETTE_BYTES if aggregate.frames % PALETTE_HOLD == 0 else 0

            if previous_rgb is not None:
                diff = np.abs(rgb.astype(np.int16) - previous_rgb.astype(np.int16))
                aggregate.source_mae += float(diff.mean())
                for ty in range(TILES_Y):
                    for tx in range(TILES_X):
                        mae = float(diff[ty*TILE:(ty+1)*TILE, tx*TILE:(tx+1)*TILE].mean())
                        aggregate.source_tiles_mae_le_2 += mae <= 2
                        aggregate.source_tiles_mae_le_4 += mae <= 4
                        aggregate.source_tiles_mae_le_8 += mae <= 8

            for index, current in enumerate(current_tiles):
                previous = previous_tiles[index]
                if current == previous:
                    aggregate.modes["same"] += 1
                    current_available[index] = previous_available[index]
                    continue
                aggregate.changed_tiles += 1

                cached = cache.find(current)
                if cached is not None:
                    mode = "cache_copy"
                    cost = 3
                    aggregate.cache_hits += 1
                    current_available[index] = software_cache.find(current) is not None
                else:
                    candidates = {
                        "raw": 129,
                        "rle": 1 + packbits_size(current),
                    }
                    if previous_available[index]:
                        candidates["xor_previous"] = 1 + zero_rle_size(
                            xor_bytes(previous, current)
                        )
                        candidates["bitplane"] = bitplane_update_size(previous, current)
                    tx = index % TILES_X
                    ty = index // TILES_X
                    for name, candidate_index in (
                        ("motion_left", index - 1 if tx else -1),
                        ("motion_right", index + 1 if tx + 1 < TILES_X else -1),
                        ("motion_up", index - TILES_X if ty else -1),
                        ("motion_down", index + TILES_X if ty + 1 < TILES_Y else -1),
                    ):
                        if candidate_index >= 0 and previous_available[candidate_index]:
                            candidates[name] = 2 + zero_rle_size(
                                xor_bytes(previous_tiles[candidate_index], current)
                            )
                    if tx and current_available[index - 1]:
                        candidates["current_left"] = 1 + zero_rle_size(
                            xor_bytes(current_tiles[index - 1], current)
                        )
                    if ty and current_available[index - TILES_X]:
                        candidates["current_up"] = 1 + zero_rle_size(
                            xor_bytes(current_tiles[index - TILES_X], current)
                        )
                    mode, cost = min(candidates.items(), key=lambda item: (item[1], item[0]))
                    cache.insert(current)
                    software_cache.insert(current)
                    aggregate.cache_misses += 1
                    uploads += 1
                    current_available[index] = True

                aggregate.modes[mode] += 1
                aggregate.mode_bytes[mode] += cost
                frame_bytes += cost

            palette_for_frame = PALETTE_BYTES if aggregate.frames % PALETTE_HOLD == 0 else 0
            frame_bytes += palette_for_frame
            aggregate.stream_bytes += frame_bytes
            aggregate.uploads += uploads
            aggregate.peak_frame_bytes = max(aggregate.peak_frame_bytes, frame_bytes)
            aggregate.peak_uploads = max(aggregate.peak_uploads, uploads)
            aggregate.frames += 1
            previous_tiles = current_tiles
            previous_available = current_available
            previous_rgb = rgb

    for frame in video_frames(path, fps):
        group_rgb.append(frame)
        if len(group_rgb) == PALETTE_HOLD:
            consume(group_rgb)
            group_rgb = []
        if aggregate.frames and aggregate.frames % (fps * 10) == 0:
            print(f"{path.name} {fps}fps: {aggregate.frames} frames", file=sys.stderr, flush=True)
    if group_rgb:
        consume(group_rgb)

    comparisons = max(0, aggregate.frames - 1) * TILES_PER_FRAME
    duration = aggregate.frames / fps
    return {
        "file": path.name,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fps": fps,
        "frames": aggregate.frames,
        "duration_seconds": duration,
        "raw_bytes_per_frame": WIDTH * HEIGHT // 2,
        "stream_bytes": aggregate.stream_bytes,
        "average_bytes_per_frame": aggregate.stream_bytes / aggregate.frames,
        "average_bytes_per_second": aggregate.stream_bytes / duration,
        "peak_frame_bytes": aggregate.peak_frame_bytes,
        "changed_tile_percent": 100 * aggregate.changed_tiles / (aggregate.frames * TILES_PER_FRAME),
        "same_tile_percent": 100 * aggregate.modes["same"] / (aggregate.frames * TILES_PER_FRAME),
        "source_tile_mae_le_2_percent": 100 * aggregate.source_tiles_mae_le_2 / comparisons if comparisons else 0,
        "source_tile_mae_le_4_percent": 100 * aggregate.source_tiles_mae_le_4 / comparisons if comparisons else 0,
        "source_tile_mae_le_8_percent": 100 * aggregate.source_tiles_mae_le_8 / comparisons if comparisons else 0,
        "source_frame_mean_absolute_error": aggregate.source_mae / max(1, aggregate.frames - 1),
        "cache_hit_percent_of_changed": 100 * aggregate.cache_hits / max(1, aggregate.changed_tiles),
        "cache_misses": aggregate.cache_misses,
        "graphics_cache_tiles": GRAPHICS_CACHE_TILES,
        "software_cache_tiles": SOFTWARE_CACHE_TILES,
        "software_cache_bytes": SOFTWARE_CACHE_TILES * 128,
        "average_tile_uploads_per_frame": aggregate.uploads / aggregate.frames,
        "peak_tile_uploads_per_frame": aggregate.peak_uploads,
        "mode_counts": dict(aggregate.modes),
        "mode_bytes": dict(aggregate.mode_bytes),
        "palette_bytes": aggregate.palette_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--fps", nargs="+", type=int, default=[15, 20, 24, 30])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for fps in args.fps:
        for video in args.videos:
            results.append(analyze(video, fps))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
