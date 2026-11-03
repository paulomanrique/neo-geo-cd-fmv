#!/usr/bin/env python3
"""Encode a movie into the Neo Geo CD native-tile stream.

The runtime cadence is deliberately physical: every 15 fps presentation block
occupies five 2048-byte sectors. Four sectors carry the primary video packet;
the fifth carries future ADPCM-A audio followed by a video continuation. A 1x
reader therefore consumes exactly 75 sectors/s without wasting the bytes that
the low-rate audio does not need.

The encoder is reconstruction-aware. Tiles which do not fit the per-frame
budget remain as the decoder would actually display them; later XOR and plane
deltas are computed from that reconstructed state, never from an unavailable
ideal frame.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from perceptual import oklab, squared_error
import palette_fit


WIDTH = 320
HEIGHT = 224
FPS = 15
TILE = 16
TILES_X = WIDTH // TILE
TILES_Y = HEIGHT // TILE
TILES = TILES_X * TILES_Y
PALETTE_REGION = 64
PALETTE_COLUMNS = math.ceil(WIDTH / PALETTE_REGION)
PALETTE_ROWS = math.ceil(HEIGHT / PALETTE_REGION)
PALETTES = PALETTE_COLUMNS * PALETTE_ROWS
PALETTE_HOLD = 4
PALETTE_BYTES = PALETTES * 16 * 2

SECTOR_BYTES = 2048
VIDEO_SECTORS = 4
AUX_SECTORS = 1
VIDEO_PACKET_BYTES = VIDEO_SECTORS * SECTOR_BYTES
FRAME_BLOCK_BYTES = (VIDEO_SECTORS + AUX_SECTORS) * SECTOR_BYTES
VIDEO_HEADER_BYTES = 32
AUX_HEADER_BYTES = 12
AUX_AUDIO_REGION_BYTES = 640
AUX_VIDEO_BYTES = SECTOR_BYTES - AUX_AUDIO_REGION_BYTES
VIDEO_BUDGET_BYTES = VIDEO_PACKET_BYTES + AUX_VIDEO_BYTES
MAX_SUPPLIES = 64
CENTER_PRIORITY_INSET = 1
CENTER_PRIORITY_NUM = 5
CENTER_PRIORITY_DEN = 4
SPRITE_CACHE_TILES = 32768
PCM_PRELOAD_BYTES = 1024 * 1024
PCM_BANK_BYTES = PCM_PRELOAD_BYTES // 2
ADPCM_RATE_NUM = 500000
ADPCM_RATE_DEN = 27  # 8 MHz / 12 / 6 / 6 = 18518.518... Hz
VIDEO_FILTER = (
    f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}"
)

MODE_RAW = 0
MODE_PACKBITS = 1
MODE_XOR_PREVIOUS = 2
MODE_PLANES_PREVIOUS = 3


def hungarian(cost: list[list[float]]) -> list[int]:
    """Return the selected column for each row of a square cost matrix."""
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


def rgb_to_ng_word(rgb: tuple[int, int, int]) -> int:
    r, g, b = (round(channel * 31 / 255) for channel in rgb)
    return (
        ((r & 1) << 14)
        | ((g & 1) << 13)
        | ((b & 1) << 12)
        | ((r & 0x1E) << 7)
        | ((g & 0x1E) << 3)
        | ((b & 0x1E) >> 1)
    )


def video_frames(path: Path, limit: int | None = None):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vf", VIDEO_FILTER,
    ]
    if limit is not None:
        command.extend(("-frames:v", str(limit)))
    command.extend(("-f", "rawvideo", "-pix_fmt", "rgb24", "-"))
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    frame_bytes = WIDTH * HEIGHT * 3
    reached_eof = False
    try:
        while True:
            data = process.stdout.read(frame_bytes)
            if not data:
                reached_eof = True
                break
            if len(data) != frame_bytes:
                raise RuntimeError("ffmpeg returned a partial RGB frame")
            yield np.frombuffer(data, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
    finally:
        if not reached_eof:
            process.terminate()
        process.stdout.close()
        return_code = process.wait()
        if return_code and reached_eof:
            raise subprocess.CalledProcessError(return_code, command)


def probe_frame_count(path: Path) -> int:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vf", VIDEO_FILTER, "-an", "-progress", "pipe:1", "-nostats",
        "-f", "null", "-",
    ]
    progress = subprocess.check_output(command, text=True)
    values = [
        line.removeprefix("frame=").strip()
        for line in progress.splitlines()
        if line.startswith("frame=")
    ]
    value = values[-1] if values else ""
    if not value.isdigit() or int(value) <= 0:
        raise RuntimeError(f"ffmpeg returned an invalid frame count: {value!r}")
    return int(value)


def quantize_group(
    frames: list[np.ndarray],
    previous_palettes: list[list[tuple[int, int, int]]] | None,
    *,
    metric: str = "oklab",
    stability: float = 0.02,
    fitter: str = "oklab",
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
            combined = np.concatenate(
                [frame[top:bottom, left:right] for frame in frames], axis=1
            )
            image = Image.fromarray(combined, "RGB").quantize(
                colors=15,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.NONE,
            )
            raw_palette = image.getpalette()[:45]
            raw_palette.extend([0] * (45 - len(raw_palette)))
            colors = [tuple(raw_palette[i * 3 : i * 3 + 3]) for i in range(15)]
            current = np.asarray(image, dtype=np.uint8)
            if fitter == "oklab":
                colors = palette_fit.fit(combined, colors)
                current = palette_fit.assign(combined, colors)

            palette_id = py * PALETTE_COLUMNS + px
            if previous_palettes is not None:
                old = previous_palettes[palette_id]
                if stability > 0:
                    old_indices = palette_fit.assign(combined, old)
                    old_rgb = np.asarray(old, dtype=np.uint8)[old_indices]
                    new_rgb = np.asarray(colors, dtype=np.uint8)[current]
                    if squared_error(combined, old_rgb).mean() <= (
                        squared_error(combined, new_rgb).mean() * (1 + stability) + 1e-12
                    ):
                        colors = old
                        current = old_indices
                old_values = oklab(np.asarray(old)) if metric == "oklab" else np.asarray(old)
                new_values = oklab(np.asarray(colors)) if metric == "oklab" else np.asarray(colors)
                cost = [
                    [
                        sum((a - b) ** 2 for a, b in zip(old_color, new_color))
                        for new_color in new_values
                    ]
                    for old_color in old_values
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
                destination[top:bottom, left:right] = (
                    current[:, x0 : x0 + block_width] + 1
                )
    return indexed, palettes


def palette_bytes(palettes: list[list[tuple[int, int, int]]]) -> bytes:
    words: list[int] = []
    for palette in palettes:
        words.append(0x8000)
        words.extend(rgb_to_ng_word(color) for color in palette)
    return struct.pack(f">{len(words)}H", *words)


def native_tile(indexed: np.ndarray, tx: int, ty: int) -> bytes:
    tile = indexed[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]
    lanes = (tile[..., None] >> np.array([1, 0, 3, 2], dtype=np.uint8)) & 1
    packed = np.packbits(lanes, axis=1, bitorder="little")
    return packed[:, ::-1, :].transpose(1, 0, 2).tobytes()


def native_tiles(indexed: np.ndarray) -> list[bytes]:
    tiles = indexed.reshape(TILES_Y, TILE, TILES_X, TILE).transpose(0, 2, 1, 3)
    tiles = tiles.reshape(TILES, TILE, TILE)
    lanes = (tiles[..., None] >> np.array([1, 0, 3, 2], dtype=np.uint8)) & 1
    packed = np.packbits(lanes, axis=2, bitorder="little")
    packed = packed[:, :, ::-1, :].transpose(0, 2, 1, 3).reshape(TILES, 128)
    return [tile.tobytes() for tile in packed]


def packbits(data: bytes) -> bytes:
    output = bytearray()
    position = 0
    while position < len(data):
        run = 1
        while (
            position + run < len(data)
            and data[position + run] == data[position]
            and run < 128
        ):
            run += 1
        if run >= 3:
            output.extend((257 - run, data[position]))
            position += run
            continue
        start = position
        position += run
        while position < len(data) and position - start < 128:
            run = 1
            while (
                position + run < len(data)
                and data[position + run] == data[position]
                and run < 3
            ):
                run += 1
            if run >= 3:
                break
            position += run
        output.append(position - start - 1)
        output.extend(data[start:position])
    return bytes(output)


def unpackbits(data: bytes, expected: int) -> bytes:
    output = bytearray()
    position = 0
    while position < len(data):
        command = data[position]
        position += 1
        if command <= 127:
            count = command + 1
            output.extend(data[position : position + count])
            position += count
        elif command >= 129:
            count = 257 - command
            output.extend(bytes((data[position],)) * count)
            position += 1
    if position != len(data) or len(output) != expected:
        raise RuntimeError("PackBits reference decoder length mismatch")
    return bytes(output)


def zero_rle(data: bytes) -> bytes:
    output = bytearray()
    position = 0
    while position < len(data):
        if data[position] == 0:
            end = position + 1
            while end < len(data) and data[end] == 0 and end - position < 128:
                end += 1
            output.append(end - position - 1)
        else:
            end = position + 1
            while end < len(data) and end - position < 128:
                if data[end] == 0 and end + 1 < len(data) and data[end + 1] == 0:
                    break
                end += 1
            output.append(0x80 | (end - position - 1))
            output.extend(data[position:end])
        position = end
    return bytes(output)


def zero_rle_decode(data: bytes, expected: int) -> bytes:
    output = bytearray()
    source = 0
    while source < len(data):
        command = data[source]
        source += 1
        count = (command & 0x7F) + 1
        if command & 0x80:
            output.extend(data[source : source + count])
            source += count
        else:
            output.extend(bytes(count))
    if source != len(data) or len(output) != expected:
        raise RuntimeError("zero-RLE reference decoder length mismatch")
    return bytes(output)


def plane_delta(previous: bytes, current: bytes) -> bytes:
    mask = 0
    output = bytearray((0,))
    for plane in range(4):
        old = previous[plane:64:4] + previous[64 + plane : 128 : 4]
        new = current[plane:64:4] + current[64 + plane : 128 : 4]
        if old != new:
            mask |= 1 << plane
            output.extend(new)
    output[0] = mask
    return bytes(output)


def apply_plane_delta(previous: bytes, payload: bytes) -> bytes:
    output = bytearray(previous)
    source = 1
    for plane in range(4):
        if not (payload[0] & (1 << plane)):
            continue
        values = payload[source : source + 32]
        source += 32
        output[plane:64:4] = values[:16]
        output[64 + plane : 128 : 4] = values[16:]
    if source != len(payload):
        raise RuntimeError("plane delta reference decoder length mismatch")
    return bytes(output)


def best_supply(previous: bytes, current: bytes) -> tuple[int, bytes]:
    xor = bytes(a ^ b for a, b in zip(previous, current, strict=True))
    choices = [
        (MODE_RAW, current),
        (MODE_PACKBITS, packbits(current)),
        (MODE_XOR_PREVIOUS, zero_rle(xor)),
        (MODE_PLANES_PREVIOUS, plane_delta(previous, current)),
    ]
    mode, payload = min(choices, key=lambda item: len(item[1]))
    if len(payload) > 255:
        raise RuntimeError("tile payload does not fit its one-byte length")
    return mode, payload


def render_indexed(
    indexed: np.ndarray, palettes: list[list[tuple[int, int, int]]]
) -> np.ndarray:
    output = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for py in range(PALETTE_ROWS):
        for px in range(PALETTE_COLUMNS):
            palette_id = py * PALETTE_COLUMNS + px
            colors = np.asarray([(0, 0, 0), *palettes[palette_id]], dtype=np.uint8)
            left = px * PALETTE_REGION
            top = py * PALETTE_REGION
            right = min(left + PALETTE_REGION, WIDTH)
            bottom = min(top + PALETTE_REGION, HEIGHT)
            output[top:bottom, left:right] = colors[
                indexed[top:bottom, left:right]
            ]
    return output


def tile_error(left: np.ndarray, right: np.ndarray, metric: str = "oklab") -> np.ndarray:
    if metric == "rgb":
        difference = np.abs(left.astype(np.int16) - right.astype(np.int16)).sum(axis=-1)
    else:
        difference = squared_error(left, right)
    return difference.reshape(TILES_Y, TILE, TILES_X, TILE).sum(axis=(1, 3)).reshape(-1)


@dataclass
class Supply:
    position: int
    destination: int
    mode: int
    payload: bytes

    @property
    def encoded_bytes(self) -> int:
        return 6 + len(self.payload) + (len(self.payload) & 1)

    def encode(self) -> bytes:
        record = (
            struct.pack(">HHBB", self.position, self.destination, self.mode, len(self.payload))
            + self.payload
        )
        return record + bytes(len(self.payload) & 1)


class SlotAllocator:
    def __init__(self) -> None:
        self.next_slot = 1  # tile zero remains the permanent black tile

    def allocate(self, visible: set[int]) -> int:
        for _ in range(SPRITE_CACHE_TILES - 1):
            slot = self.next_slot
            self.next_slot += 1
            if self.next_slot >= SPRITE_CACHE_TILES:
                self.next_slot = 1
            if slot not in visible:
                return slot
        raise RuntimeError("no graphics cache slot is safe to overwrite")


def decode_supply(previous: bytes, mode: int, payload: bytes) -> bytes:
    if mode == MODE_RAW:
        decoded = payload
    elif mode == MODE_PACKBITS:
        decoded = unpackbits(payload, 128)
    elif mode == MODE_XOR_PREVIOUS:
        delta = zero_rle_decode(payload, 128)
        decoded = bytes(a ^ b for a, b in zip(previous, delta, strict=True))
    elif mode == MODE_PLANES_PREVIOUS:
        decoded = apply_plane_delta(previous, payload)
    else:
        raise RuntimeError(f"unknown tile mode {mode}")
    if len(decoded) != 128:
        raise RuntimeError("decoded tile is not 128 bytes")
    return decoded


def encode_adpcm_a(pcm: bytes) -> bytes:
    if len(pcm) & 1:
        raise RuntimeError("PCM input has a partial sample")
    samples = np.frombuffer(pcm, dtype="<i2")
    steps = [
        16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60,
        66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
        253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552,
    ]
    increments = (-1, -1, -1, -1, 2, 5, 7, 9)
    accumulator = 0
    step_index = 0
    nibbles = bytearray()
    pending = 0
    for sample_index, sample in enumerate(samples):
        target = int(sample) >> 4
        best_code = 0
        best_error = 1 << 60
        best_accumulator = accumulator
        for code in range(16):
            value = (2 * (code & 7) + 1) * steps[step_index] // 8
            candidate = accumulator - value if code & 8 else accumulator + value
            candidate &= 0xFFF
            if candidate & 0x800:
                candidate -= 0x1000
            error = (target - candidate) ** 2
            if error < best_error:
                best_error = error
                best_code = code
                best_accumulator = candidate
        accumulator = best_accumulator
        step_index = max(0, min(48, step_index + increments[best_code & 7]))
        if sample_index & 1:
            nibbles.append(pending | best_code)
        else:
            pending = best_code << 4
    if len(samples) & 1:
        nibbles.append(pending | 0x08)
    return bytes(nibbles)


def encode_adpcm_a_banks(pcm: bytes, bank_bytes: int = PCM_BANK_BYTES) -> bytes:
    """Match the YM2610 decoder reset at every 512 KiB playback bank."""
    pcm_bytes_per_bank = bank_bytes * 4
    return b"".join(
        encode_adpcm_a(pcm[offset : offset + pcm_bytes_per_bank])
        for offset in range(0, len(pcm), pcm_bytes_per_bank)
    )


def extract_audio(path: Path, expected_samples: int) -> bytes:
    rate = round(ADPCM_RATE_NUM / ADPCM_RATE_DEN)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-",
    ]
    pcm = subprocess.check_output(command)
    expected_bytes = expected_samples * 2
    if len(pcm) < expected_bytes:
        pcm += bytes(expected_bytes - len(pcm))
    else:
        pcm = pcm[:expected_bytes]
    return encode_adpcm_a_banks(pcm)


def write_attributes(path: Path) -> None:
    with path.open("wb") as output:
        for tx in range(TILES_X):
            for ty in range(TILES_Y):
                palette_id = (ty // 4) * PALETTE_COLUMNS + (tx // 4)
                output.write(struct.pack(">H", palette_id << 8))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--metric", choices=("rgb", "oklab"), default="oklab")
    parser.add_argument("--palette-stability", type=float, default=0.02,
                        help="relative palette-error tolerance for keeping existing colors; 0 disables")
    parser.add_argument("--palette-fitter", choices=("rgb", "oklab"), default="oklab")
    parser.add_argument("--full-cache", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--differential-maps", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--denoise", choices=("auto", "0", "1", "2"), default="auto")
    args = parser.parse_args()
    if not 0 <= args.palette_stability <= 1:
        parser.error("--palette-stability must be between 0 and 1")
    args.output.mkdir(parents=True, exist_ok=True)

    frame_count = probe_frame_count(args.input)
    if args.max_frames is not None:
        frame_count = min(frame_count, args.max_frames)
    expected_samples = (frame_count * ADPCM_RATE_NUM + FPS * ADPCM_RATE_DEN - 1) // (
        FPS * ADPCM_RATE_DEN
    )
    adpcm = extract_audio(args.input, expected_samples)
    preload = adpcm[:PCM_PRELOAD_BYTES].ljust(PCM_PRELOAD_BYTES, b"\x08")
    future_audio = adpcm[PCM_PRELOAD_BYTES:]
    (args.output / "AUDIO0.PCM").write_bytes(preload)

    from video_encoder import VideoEncoder, encode_group

    encoder = VideoEncoder(args, future_audio)
    stream = bytearray()
    group = []
    for frame in video_frames(args.input, frame_count):
        group.append(frame)
        if len(group) == PALETTE_HOLD or encoder.number + len(group) == frame_count:
            encoder, records = encode_group(encoder, group)
            stream.extend(b"".join(record.block for record in records))
            group = []
            if encoder.number % 100 == 0:
                print(f"encoded {encoder.number}/{frame_count} frames; "
                      f"cache_hits={encoder.stats['historical_cache_hits']}; "
                      f"denoised_groups={encoder.stats['denoised_groups']}",
                      file=sys.stderr, flush=True)
    encoded_frame = encoder.number
    if encoded_frame != frame_count:
        raise RuntimeError(f"ffmpeg produced {encoded_frame} frames; expected {frame_count}")
    total_used = encoder.stats["total_used"]
    peak_used = encoder.stats["peak_used"]
    total_supplies = encoder.stats["total_supplies"]
    peak_supplies = encoder.stats["peak_supplies"]
    total_absolute_error = encoder.stats["total_absolute_error"]
    total_perceptual_error = encoder.stats["total_perceptual_error"]
    palette_updates = encoder.stats["palette_updates"]
    mode_counts = encoder.mode_counts

    if len(stream) != frame_count * FRAME_BLOCK_BYTES:
        raise RuntimeError("stream does not contain exactly five sectors per frame")
    (args.output / "STREAM.DAT").write_bytes(stream)
    write_attributes(args.output / "tile_attributes.bin")

    metadata = {
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "frame_count": frame_count,
        "duration_seconds": frame_count / FPS,
        "sector_bytes": SECTOR_BYTES,
        "sectors_per_frame": VIDEO_SECTORS + AUX_SECTORS,
        "stream_bytes": len(stream),
        "stream_sectors": len(stream) // SECTOR_BYTES,
        "video_payload_bytes_per_frame": VIDEO_PACKET_BYTES,
        "video_continuation_bytes_per_frame": AUX_VIDEO_BYTES,
        "video_budget_bytes_per_frame": VIDEO_BUDGET_BYTES,
        "average_used_video_bytes": total_used / frame_count,
        "peak_used_video_bytes": peak_used,
        "average_supplies": total_supplies / frame_count,
        "peak_supplies": peak_supplies,
        "maximum_supplies": MAX_SUPPLIES,
        "mean_absolute_rgb_error": total_absolute_error / (frame_count * WIDTH * HEIGHT * 3),
        "mean_squared_oklab_error": total_perceptual_error / (frame_count * WIDTH * HEIGHT),
        "selection_metric": args.metric,
        "palette_stability": args.palette_stability,
        "palette_updates": palette_updates,
        "palette_fitter": args.palette_fitter,
        "full_cache": args.full_cache,
        "differential_maps": args.differential_maps,
        "denoise": args.denoise,
        "historical_cache_hits": encoder.stats["historical_cache_hits"],
        "same_frame_cache_hits": encoder.stats["same_frame_cache_hits"],
        "differential_map_frames": encoder.stats["differential_maps"],
        "average_map_bytes": encoder.stats["map_bytes"] / frame_count,
        "denoised_groups": encoder.stats["denoised_groups"],
        "denoise_trials": encoder.stats["denoise_trials"],
        "center_priority_inset_tiles": CENTER_PRIORITY_INSET,
        "center_priority_multiplier": CENTER_PRIORITY_NUM / CENTER_PRIORITY_DEN,
        "mode_counts": mode_counts,
        "adpcm_rate": ADPCM_RATE_NUM / ADPCM_RATE_DEN,
        "adpcm_bytes": len(adpcm),
        "adpcm_preload_bytes": len(preload),
        "adpcm_streamed_bytes": len(future_audio),
    }
    (args.output / "video.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    constants = [
        f"VIDEO_FRAME_COUNT equ {frame_count}",
        f"VIDEO_FPS_NUM equ {FPS * 10000}",
        "VIDEO_REFRESH_DEN equ 591856",
        f"VIDEO_COLUMNS equ {TILES_X}",
        f"VIDEO_ROWS equ {TILES_Y}",
        f"VIDEO_TILES_PER_FRAME equ {TILES}",
        f"VIDEO_PALETTES_PER_FRAME equ {PALETTES}",
        f"VIDEO_PALETTE_BYTES equ {PALETTE_BYTES}",
        f"VIDEO_PACKET_BYTES equ {VIDEO_PACKET_BYTES}",
        f"VIDEO_AUX_DATA_OFFSET equ {VIDEO_PACKET_BYTES + AUX_AUDIO_REGION_BYTES}",
        f"VIDEO_FRAME_BLOCK_BYTES equ {FRAME_BLOCK_BYTES}",
        f"VIDEO_MAX_SUPPLIES equ {MAX_SUPPLIES}",
        "",
    ]
    (args.output / "video.inc").write_text("\n".join(constants), encoding="ascii")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
