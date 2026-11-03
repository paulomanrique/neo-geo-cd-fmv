#!/usr/bin/env python3
"""Export an NTV1 stream to a lossless video with decoded ADPCM-A audio."""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import wave
import zlib
from pathlib import Path

import numpy as np

from encode_native_stream import (
    ADPCM_RATE_DEN, ADPCM_RATE_NUM, FPS, FRAME_BLOCK_BYTES, PCM_BANK_BYTES,
    PCM_PRELOAD_BYTES, TILES, TILES_X, TILES_Y, VIDEO_PACKET_BYTES,
    AUX_AUDIO_REGION_BYTES, SPRITE_CACHE_TILES, decode_supply,
)
from render_native_stream import palette_color, tile_indices


def decode_audio(payload: bytes) -> bytes:
    steps = (
        16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60,
        66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
        253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552,
    )
    increments = (-1, -1, -1, -1, 2, 5, 7, 9)
    samples = np.empty(len(payload) * 2, dtype="<i2")
    accumulator = step_index = 0
    for offset, byte in enumerate(payload):
        if offset % PCM_BANK_BYTES == 0:
            accumulator = step_index = 0
        for nibble, code in enumerate((byte >> 4, byte & 15)):
            delta = (2 * (code & 7) + 1) * steps[step_index] // 8
            accumulator += -delta if code & 8 else delta
            accumulator &= 0xFFF
            if accumulator & 0x800:
                accumulator -= 0x1000
            step_index = max(0, min(48, step_index + increments[code & 7]))
            samples[offset * 2 + nibble] = accumulator << 4
    return samples.tobytes()


def unpack_tiles(tiles: list[bytes]) -> np.ndarray:
    native = np.frombuffer(b"".join(tiles), dtype=np.uint8).reshape(TILES, 128)
    pixels = np.zeros((TILES, 16, 16), dtype=np.uint8)
    shifts = np.arange(8, dtype=np.uint8)
    for half in range(2):
        for plane, lane in enumerate((1, 0, 3, 2)):
            indices = (1 - half) * 64 + np.arange(16) * 4 + lane
            values = native[:, indices]
            pixels[:, :, half * 8 : half * 8 + 8] |= (
                ((values[:, :, None] >> shifts) & 1) << plane
            )
    return pixels


def frames(stream: Path):
    previous = [bytes(128)] * TILES
    cache = {0: bytes(128)}
    previous_map = (0,) * TILES
    previous_valid = [True] * TILES
    palettes = np.zeros((20, 16, 3), dtype=np.uint8)
    palette_ids = np.array([(p // TILES_X // 4) * 5 + (p % TILES_X // 4)
                            for p in range(TILES)])
    with stream.open("rb") as source:
        number = 0
        while block := source.read(FRAME_BLOCK_BYTES):
            if len(block) != FRAME_BLOCK_BYTES:
                raise ValueError("partial frame block")
            (magic, encoded, flags, used, count, map_offset, supply_offset,
             checksum, primary_count, continuation, _) = struct.unpack_from(
                ">4sHHHHHHIHH8s", block)
            if magic != b"NTV1" or encoded != number:
                raise ValueError(f"invalid frame header at {number}")
            expected_map = 32 + (640 if flags & 1 else 0)
            if (flags & ~3 or map_offset != expected_map
                    or not map_offset <= supply_offset <= VIDEO_PACKET_BYTES
                    or count > 96 or primary_count > count
                    or continuation != VIDEO_PACKET_BYTES + AUX_AUDIO_REGION_BYTES):
                raise ValueError(f"invalid frame layout at {number}")
            audio_magic, audio_frame, audio_size, audio_crc = struct.unpack_from(
                ">4sHHI", block, VIDEO_PACKET_BYTES)
            audio_start = VIDEO_PACKET_BYTES + 12
            if (audio_magic != b"NTA1" or audio_frame != number
                    or audio_size > AUX_AUDIO_REGION_BYTES - 12
                    or zlib.crc32(block[audio_start:audio_start + audio_size]) != audio_crc):
                raise ValueError(f"invalid audio packet at {number}")
            if flags & 1:
                palettes = np.array([palette_color(w) for w in
                                     struct.unpack_from(">320H", block, 32)],
                                    dtype=np.uint8).reshape(20, 16, 3)
            if flags & 2:
                changed = struct.unpack_from(">H", block, map_offset)[0]
                if changed > TILES or supply_offset != map_offset + 2 + changed * 4:
                    raise ValueError(f"invalid differential map at {number}")
                tile_map = list(previous_map)
                last_position = -1
                for entry in range(changed):
                    position, slot = struct.unpack_from(">HH", block, map_offset + 2 + entry * 4)
                    if not last_position < position < TILES:
                        raise ValueError(f"unordered differential map at {number}")
                    tile_map[position] = slot
                    last_position = position
                tile_map = tuple(tile_map)
            else:
                if supply_offset != map_offset + TILES * 2:
                    raise ValueError(f"invalid full map at {number}")
                tile_map = struct.unpack_from(f">{TILES}H", block, map_offset)
            if any(slot >= SPRITE_CACHE_TILES for slot in tile_map):
                raise ValueError(f"invalid cache slot at {number}")
            current = [cache.get(slot, bytes(128)) for slot in tile_map]
            offset = supply_offset
            primary_end = offset
            continuation_end = continuation
            destinations: set[int] = set()
            positions: set[int] = set()
            for index in range(count):
                if continuation and index == primary_count:
                    offset = continuation
                position, destination, mode, length = struct.unpack_from(
                    ">HHBB", block, offset)
                end = offset + 6 + length + (length & 1)
                limit = VIDEO_PACKET_BYTES if index < primary_count else FRAME_BLOCK_BYTES
                if (end > limit or position >= TILES or position in positions
                        or not 0 < destination < SPRITE_CACHE_TILES
                        or destination in previous_map or destination in destinations
                        or tile_map[position] != destination):
                    raise ValueError(f"invalid supply at frame {number}, record {index}")
                positions.add(position)
                destinations.add(destination)
                if mode >= 2 and not previous_valid[position]:
                    raise ValueError(f"delta without a valid CPU baseline at frame {number}")
                payload = block[offset + 6 : offset + 6 + length]
                current[position] = decode_supply(previous[position], mode, payload)
                cache[destination] = current[position]
                offset += 6 + length + (length & 1)
                if index < primary_count:
                    primary_end = offset
                else:
                    continuation_end = offset
            body = block[32:primary_end] + block[continuation:continuation_end]
            if used != 32 + len(body) or zlib.crc32(body) != checksum:
                raise ValueError(f"invalid video size or CRC at frame {number}")
            if any(slot not in cache for slot in tile_map):
                raise ValueError(f"uninitialized cache reference at frame {number}")
            previous_positions = {slot: p for p, slot in enumerate(previous_map)}
            current_valid = [p in positions or (
                slot in previous_positions and previous_valid[previous_positions[slot]])
                for p, slot in enumerate(tile_map)]
            current = [cache[slot] for slot in tile_map]
            indices = unpack_tiles(current)
            if number == 0:
                for tile, unpacked in zip(current, indices, strict=True):
                    if not np.array_equal(tile_indices(tile), unpacked):
                        raise ValueError("vector tile decoder disagrees with reference")
            rgb = palettes[palette_ids[:, None, None], indices]
            yield rgb.reshape(TILES_Y, TILES_X, 16, 16, 3).transpose(
                0, 2, 1, 3, 4).reshape(224, 320, 3)
            previous = current
            previous_map = tile_map
            previous_valid = current_valid
            number += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated", type=Path)
    parser.add_argument("output", type=Path, help="lossless .mkv output")
    args = parser.parse_args()
    metadata = json.loads((args.generated / "video.json").read_text())
    stream = args.generated / "STREAM.DAT"
    audio = bytearray((args.generated / "AUDIO0.PCM").read_bytes()[:PCM_PRELOAD_BYTES])
    with stream.open("rb") as source:
        while block := source.read(FRAME_BLOCK_BYTES):
            magic, _, size, _ = struct.unpack_from(">4sHHI", block, VIDEO_PACKET_BYTES)
            if magic != b"NTA1":
                raise ValueError("invalid audio header")
            audio.extend(block[VIDEO_PACKET_BYTES + 12 : VIDEO_PACKET_BYTES + 12 + size])
    audio = audio[:metadata["adpcm_bytes"]]
    if len(audio) != metadata["adpcm_bytes"]:
        raise ValueError("incomplete ADPCM stream")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    wav = args.output.with_suffix(".wav")
    with wave.open(str(wav), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(round(ADPCM_RATE_NUM / ADPCM_RATE_DEN))
        output.writeframes(decode_audio(bytes(audio)))
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x224",
        "-r", str(FPS), "-i", "pipe:0", "-i", str(wav),
        "-map", "0:v", "-map", "1:a", "-c:v", "ffv1", "-level", "3",
        "-pix_fmt", "bgr0", "-c:a", "pcm_s16le", str(args.output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    count = 0
    try:
        for frame in frames(stream):
            process.stdin.write(frame.tobytes())
            count += 1
    finally:
        process.stdin.close()
        returncode = process.wait()
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)
    if count != metadata["frame_count"]:
        raise ValueError("exported frame count disagrees with metadata")
    print(f"Exported {count} frames to {args.output}")


if __name__ == "__main__":
    main()
