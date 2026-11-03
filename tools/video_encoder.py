"""Offline tile working set, differential maps, and guarded preprocessing."""

from __future__ import annotations

import copy
import struct
import zlib
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

# Imported after encode_native_stream has defined its format helpers.
from encode_native_stream import (
    ADPCM_RATE_DEN, ADPCM_RATE_NUM, AUX_AUDIO_REGION_BYTES,
    AUX_VIDEO_BYTES, CENTER_PRIORITY_DEN, CENTER_PRIORITY_INSET, CENTER_PRIORITY_NUM,
    FPS, MAX_SUPPLIES, PALETTE_BYTES, TILE, TILES, TILES_X,
    VIDEO_HEADER_BYTES, VIDEO_PACKET_BYTES, SlotAllocator, Supply, best_supply,
    decode_supply, native_tiles, packbits, palette_bytes, quantize_group,
    render_indexed, tile_error,
)
from perceptual import squared_error


def pack_map(previous, current, differential=True):
    changes = [(p, slot) for p, slot in enumerate(current) if slot != previous[p]]
    if differential and 2 + len(changes) * 4 < TILES * 2:
        return 2, struct.pack(">H", len(changes)) + b"".join(
            struct.pack(">HH", p, slot) for p, slot in changes)
    return 0, struct.pack(f">{TILES}H", *current)


class TileCache(SlotAllocator):
    def __init__(self):
        super().__init__()
        self.tiles = {0: bytes(128)}
        self.lookup = {bytes(128): {0}}

    def find(self, tile):
        slots = self.lookup.get(tile)
        return min(slots) if slots else None

    def store(self, slot, tile):
        old = self.tiles.get(slot)
        if old is not None:
            self.lookup[old].remove(slot)
            if not self.lookup[old]:
                del self.lookup[old]
        self.tiles[slot] = tile
        self.lookup.setdefault(tile, set()).add(slot)


@dataclass
class EncodedFrame:
    block: bytes
    perceptual_error: float
    uploads: int


class VideoEncoder:
    def __init__(self, args, future_audio=b""):
        self.args = args
        self.audio = future_audio
        self.cache = TileCache()
        self.indexed = np.zeros((224, 320), dtype=np.uint8)
        self.native = [bytes(128)] * TILES
        self.map = [0] * TILES
        self.valid = [True] * TILES
        self.palettes = None
        self.packed_palette = None
        self.number = 0
        self.stats = dict(total_supplies=0, peak_supplies=0, total_used=0, peak_used=0,
                          total_absolute_error=0, total_perceptual_error=0.0,
                          palette_updates=0, cache_hits=0, historical_cache_hits=0,
                          same_frame_cache_hits=0, differential_maps=0, map_bytes=0,
                          denoised_groups=0, denoise_trials=0)
        self.mode_counts = {str(mode): 0 for mode in range(4)}

    def group(self, source, training=None):
        targets, palettes = quantize_group(
            training if training is not None else source, self.palettes,
            metric=self.args.metric, stability=self.args.palette_stability,
            fitter=self.args.palette_fitter)
        self.palettes = palettes
        packed = palette_bytes(palettes)
        return [self.frame(rgb, target, palettes, packed)
                for rgb, target in zip(source, targets, strict=True)]

    def frame(self, rgb, target, palettes, packed_palette):
        target_native = native_tiles(target)
        indexed = self.indexed.copy()
        current_native = self.native.copy()
        current_map = self.map.copy()
        previous_positions = {slot: p for p, slot in enumerate(self.map)}
        exact = {}
        for p, tile in enumerate(self.native):
            exact.setdefault(tile, self.map[p])
        reused = set()

        def set_target(position, slot):
            ty, tx = divmod(position, TILES_X)
            ys, xs = slice(ty * TILE, (ty + 1) * TILE), slice(tx * TILE, (tx + 1) * TILE)
            indexed[ys, xs] = target[ys, xs]
            current_native[position] = target_native[position]
            current_map[position] = slot

        for p, tile in enumerate(target_native):
            slot = exact.get(tile)
            if slot is None and self.args.full_cache:
                slot = self.cache.find(tile)
            if slot is not None:
                set_target(p, slot)
                reused.add(p)
                self.stats["cache_hits"] += 1
                self.stats["historical_cache_hits"] += int(slot not in previous_positions)

        baseline_error = tile_error(rgb, render_indexed(indexed, palettes), self.args.metric)
        target_error = tile_error(rgb, render_indexed(target, palettes), self.args.metric)
        palette_flag = int(packed_palette != self.packed_palette)
        self.stats["palette_updates"] += palette_flag
        self.packed_palette = packed_palette
        map_flag, encoded_map = pack_map(self.map, current_map, self.args.differential_maps)
        map_size = len(encoded_map)
        primary_used = VIDEO_HEADER_BYTES + (PALETTE_BYTES if palette_flag else 0) + map_size
        continuation_used = 0
        candidates = []
        for p, tile in enumerate(target_native):
            if p in reused or tile == self.native[p]:
                continue
            improvement = float(baseline_error[p]) - float(target_error[p])
            if improvement <= 0:
                continue
            if self.valid[p]:
                mode, payload = best_supply(self.native[p], tile)
            else:
                payload = packbits(tile)
                mode, payload = (1, payload) if len(payload) < 128 else (0, tile)
            cost = 6 + len(payload) + (len(payload) & 1)
            ty, tx = divmod(p, TILES_X)
            center = (CENTER_PRIORITY_INSET <= tx < TILES_X - CENTER_PRIORITY_INSET
                      and CENTER_PRIORITY_INSET <= ty < 14 - CENTER_PRIORITY_INSET)
            score = improvement / cost * (CENTER_PRIORITY_NUM / CENTER_PRIORITY_DEN if center else 1)
            candidates.append((score, p, mode, payload))
        candidates.sort(reverse=True)
        primary, continuation = [], []
        visible = set(self.map) | set(current_map)
        supplied = set()
        for _, p, mode, payload in candidates:
            tile = target_native[p]
            # An earlier supply in this frame may already have interned this pattern.
            slot = self.cache.find(tile) if self.args.full_cache else None
            if slot is not None:
                tentative = current_map.copy()
                tentative[p] = slot
                growth = len(pack_map(self.map, tentative, self.args.differential_maps)[1]) - map_size
                if primary_used + growth <= VIDEO_PACKET_BYTES:
                    set_target(p, slot)
                    visible.add(slot)
                    primary_used += growth
                    map_size += growth
                    self.stats["same_frame_cache_hits"] += 1
                continue
            if len(primary) + len(continuation) >= MAX_SUPPLIES:
                continue
            # A new slot changes this position relative to N-1, adding at most one map entry.
            changes = sum(a != b for a, b in zip(self.map, current_map))
            next_size = min(TILES * 2, 2 + (changes + 1) * 4) if self.args.differential_maps else TILES * 2
            growth = next_size - map_size
            cost = 6 + len(payload) + (len(payload) & 1)
            if primary_used + growth + cost <= VIDEO_PACKET_BYTES:
                output = primary
            elif primary_used + growth <= VIDEO_PACKET_BYTES and continuation_used + cost <= AUX_VIDEO_BYTES:
                output = continuation
            else:
                continue
            primary_used += growth
            map_size += growth
            if output is primary:
                primary_used += cost
            else:
                continuation_used += cost
            destination = self.cache.allocate(visible)
            visible.add(destination)
            self.cache.store(destination, tile)
            output.append(Supply(p, destination, mode, payload))
            supplied.add(p)
            set_target(p, destination)

        current_valid = []
        for p, slot in enumerate(current_map):
            previous_position = previous_positions.get(slot)
            current_valid.append(p in supplied or (
                previous_position is not None and self.valid[previous_position]))
        for supply in primary + continuation:
            if supply.mode >= 2 and not self.valid[supply.position]:
                raise ValueError("delta uses an unavailable CPU baseline")
            if decode_supply(self.native[supply.position], supply.mode, supply.payload) != current_native[supply.position]:
                raise ValueError("tile supply failed round-trip")
        if any(self.cache.tiles[slot] != tile for slot, tile in zip(current_map, current_native)):
            raise ValueError("graphics dictionary and displayed state disagree")
        map_flag, encoded_map = pack_map(self.map, current_map, self.args.differential_maps)
        self.stats["differential_maps"] += int(bool(map_flag))
        self.stats["map_bytes"] += len(encoded_map)
        map_offset = VIDEO_HEADER_BYTES + (PALETTE_BYTES if palette_flag else 0)
        body = (packed_palette if palette_flag else b"") + encoded_map
        body += b"".join(s.encode() for s in primary)
        tail = b"".join(s.encode() for s in continuation)
        used = VIDEO_HEADER_BYTES + len(body) + len(tail)
        if VIDEO_HEADER_BYTES + len(body) > VIDEO_PACKET_BYTES or len(tail) > AUX_VIDEO_BYTES:
            raise ValueError("frame byte budget exceeded")
        header = struct.pack(">4sHHHHHHIHH8s", b"NTV1", self.number, palette_flag | map_flag,
                             used, len(primary) + len(continuation), map_offset,
                             map_offset + len(encoded_map), zlib.crc32(tail, zlib.crc32(body)),
                             len(primary), VIDEO_PACKET_BYTES + AUX_AUDIO_REGION_BYTES, bytes(8))
        start = self.number * ADPCM_RATE_NUM // (FPS * ADPCM_RATE_DEN * 2)
        end = (self.number + 1) * ADPCM_RATE_NUM // (FPS * ADPCM_RATE_DEN * 2)
        audio = self.audio[start:end]
        aux = struct.pack(">4sHHI", b"NTA1", self.number, len(audio), zlib.crc32(audio))
        if len(aux) + len(audio) > AUX_AUDIO_REGION_BYTES:
            raise ValueError("audio byte budget exceeded")
        block = (header + body).ljust(VIDEO_PACKET_BYTES, b"\0")
        block += (aux + audio).ljust(AUX_AUDIO_REGION_BYTES, b"\x08") + tail.ljust(AUX_VIDEO_BYTES, b"\0")
        rendered = render_indexed(indexed, palettes)
        error = float(squared_error(rgb, rendered).mean())
        self.stats["total_perceptual_error"] += error * 320 * 224
        self.stats["total_absolute_error"] += int(np.abs(rgb.astype(np.int16) - rendered.astype(np.int16)).sum())
        uploads = len(primary) + len(continuation)
        self.stats["total_supplies"] += uploads
        self.stats["peak_supplies"] = max(self.stats["peak_supplies"], uploads)
        self.stats["total_used"] += used
        self.stats["peak_used"] = max(self.stats["peak_used"], used)
        for supply in primary + continuation:
            self.mode_counts[str(supply.mode)] += 1
        self.indexed, self.native, self.map, self.valid = indexed, current_native, current_map, current_valid
        self.number += 1
        return EncodedFrame(block, error, uploads)


def denoise_group(frames, strength):
    """Remove only small local variations; high-contrast edges remain untouched."""
    result = []
    for frame in frames:
        median = np.asarray(Image.fromarray(frame).filter(ImageFilter.MedianFilter(3)))
        mask = np.abs(frame.astype(np.int16) - median.astype(np.int16)).max(axis=-1) <= 8 * strength
        result.append(np.where(mask[..., None], median, frame).astype(np.uint8))
    return result


def encode_group(encoder, source):
    initial = encoder
    baseline = copy.deepcopy(initial)
    records = baseline.group(source)
    if encoder.args.denoise == "0":
        return baseline, records
    strengths = (1, 2) if encoder.args.denoise == "auto" else (int(encoder.args.denoise),)
    best, best_records = baseline, records
    base_errors = [r.perceptual_error for r in records]
    mean_limit, tail_limit = np.mean(base_errors), np.percentile(base_errors, 95)
    for strength in strengths:
        filtered = denoise_group(source, strength)
        if all(np.array_equal(a, b) for a, b in zip(source, filtered)):
            continue
        trial = copy.deepcopy(initial)
        candidate = trial.group(source, filtered)
        errors = [r.perceptual_error for r in candidate]
        if np.mean(errors) > mean_limit + 1e-12 or np.percentile(errors, 95) > tail_limit + 1e-12:
            continue
        score = (float(np.mean(errors)), sum(r.uploads for r in candidate))
        previous = (float(np.mean([r.perceptual_error for r in best_records])),
                    sum(r.uploads for r in best_records))
        if score < previous:
            best, best_records = trial, candidate
    best.stats["denoise_trials"] += len(strengths)
    best.stats["denoised_groups"] += int(best is not baseline)
    return best, best_records
