"""Working-set, differential-map, palette, and preprocessing regressions."""

import copy
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from argparse import Namespace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from encode_native_stream import native_tiles, palette_bytes, render_indexed
from export_native_video import frames, unpack_tiles
from palette_fit import color_grid, fit, assign
from perceptual import squared_error
from video_encoder import VideoEncoder, TileCache, pack_map, encode_group, denoise_group


def options(**changes):
    values = dict(metric="oklab", palette_stability=0, palette_fitter="rgb",
                  full_cache=True, differential_maps=True, denoise="0")
    values.update(changes)
    return Namespace(**values)


class AdvancedCodecTests(unittest.TestCase):
    def test_native_encoder_preserves_every_pixel(self):
        indexed = np.random.default_rng(2).integers(0, 16, (224, 320), dtype=np.uint8)
        tiles = unpack_tiles(native_tiles(indexed))
        restored = tiles.reshape(14, 20, 16, 16).transpose(0, 2, 1, 3).reshape(224, 320)
        np.testing.assert_array_equal(restored, indexed)

    def test_sparse_and_dense_maps(self):
        old = [0] * 280
        flag, body = pack_map(old, old)
        self.assertEqual((flag, body), (2, b"\0\0"))
        new = old.copy()
        new[279] = 32767
        self.assertEqual(pack_map(old, new), (2, struct.pack(">HHH", 1, 279, 32767)))
        self.assertEqual(pack_map(old, [1] * 280)[0], 0)
        self.assertEqual(len(pack_map(old, [1] * 280)[1]), 560)

    def test_cache_eviction_removes_only_the_overwritten_alias(self):
        cache = TileCache()
        a, b = bytes([1]) * 128, bytes([2]) * 128
        cache.store(1, a)
        cache.store(2, a)
        cache.store(1, b)
        self.assertEqual(cache.find(a), 2)
        cache.store(2, b)
        self.assertIsNone(cache.find(a))
        cache.next_slot = 32767
        self.assertEqual(cache.allocate({32767, 1, 2}), 3)
        self.assertEqual(cache.tiles[0], bytes(128))

    def test_historical_reuse_and_safe_return_to_independent_uploads(self):
        encoder = VideoEncoder(options())
        grid, _ = color_grid()
        colors = [tuple(int(c) for c in grid[(v << 10) | (v << 5) | v]) for v in range(15)]
        palettes = [colors] * 20
        packed = palette_bytes(palettes)
        blocks, expected = [], []
        for value in (2, 3, 2, 2):
            target = np.ones((224, 320), dtype=np.uint8)
            target[:16, :16] = value
            if len(blocks) == 3:
                target[0, 0] = 4
            rgb = render_indexed(target, palettes)
            record = encoder.frame(rgb, target, palettes, packed)
            blocks.append(record.block)
            expected.append(rgb)
            if len(blocks) == 3:
                self.assertEqual(record.uploads, 0)
                self.assertFalse(encoder.valid[0])
        self.assertGreater(encoder.stats["historical_cache_hits"], 0)
        supply_offset = struct.unpack_from(">H", blocks[-1], 14)[0]
        self.assertIn(blocks[-1][supply_offset + 4], (0, 1))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STREAM.DAT"
            path.write_bytes(b"".join(blocks))
            for wanted, actual in zip(expected, frames(path), strict=True):
                np.testing.assert_array_equal(actual, wanted)
            damaged = bytearray(blocks[-1])
            damaged[supply_offset + 4] = 2
            path.write_bytes(b"".join(blocks[:-1]) + damaged)
            with self.assertRaisesRegex(ValueError, "valid CPU baseline"):
                list(frames(path))

    def test_same_frame_duplicates_share_one_upload(self):
        encoder = VideoEncoder(options())
        colors = [(i * 8, i * 8, i * 8) for i in range(15)]
        palettes = [colors] * 20
        target = np.ones((224, 320), dtype=np.uint8)
        target[:16, :32] = 12
        record = encoder.frame(render_indexed(target, palettes), target, palettes, palette_bytes(palettes))
        self.assertEqual(record.uploads, 1)
        self.assertEqual(encoder.map[0], encoder.map[1])

    def test_recycled_cache_slots_decode_correctly_after_multiple_wraps(self):
        rng = np.random.default_rng(17)
        encoder = VideoEncoder(options())
        grid, _ = color_grid()
        colors = [tuple(int(c) for c in grid[(v << 10) | (v << 5) | v]) for v in range(15)]
        palettes = [colors] * 20
        blocks, expected = [], []
        with patch("encode_native_stream.SPRITE_CACHE_TILES", 16):
            for number in range(24):
                target = np.ones((224, 320), dtype=np.uint8)
                target[:16, :64] = rng.integers(1, 16, (16, 64), dtype=np.uint8)
                rgb = render_indexed(target, palettes)
                blocks.append(encoder.frame(rgb, target, palettes, palette_bytes(palettes)).block)
                expected.append(rgb)
        self.assertGreater(encoder.stats["total_supplies"], 32)
        self.assertLessEqual(len(encoder.cache.tiles), 16)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STREAM.DAT"
            path.write_bytes(b"".join(blocks))
            for actual, wanted in zip(frames(path), expected, strict=True):
                np.testing.assert_array_equal(actual, wanted)

    def test_palette_fit_improves_a_poor_palette_on_realizable_colors(self):
        grid, _ = color_grid()
        rgb = np.tile(grid[np.array([0, 31744, 992, 31])], (16, 16, 1)).reshape(32, 32, 3)
        seed = [(0, 0, 0)] * 15
        fitted = fit(rgb, seed)
        before = squared_error(rgb, np.asarray(seed, dtype=np.uint8)[assign(rgb, seed)]).mean()
        after = squared_error(rgb, np.asarray(fitted, dtype=np.uint8)[assign(rgb, fitted)]).mean()
        self.assertLess(after, before)
        representable = set(map(tuple, grid))
        self.assertTrue(all(tuple(color) in representable for color in fitted))

    def test_adaptive_denoise_does_not_regress_group_mean_or_tail(self):
        rng = np.random.default_rng(4)
        source = [rng.integers(120, 127, (224, 320, 3), dtype=np.uint8) for _ in range(4)]
        initial = VideoEncoder(options(denoise="auto"))
        baseline = copy.deepcopy(initial).group(source)
        selected, records = encode_group(initial, source)
        old = [r.perceptual_error for r in baseline]
        new = [r.perceptual_error for r in records]
        self.assertLessEqual(np.mean(new), np.mean(old) + 1e-12)
        self.assertLessEqual(np.percentile(new, 95), np.percentile(old, 95) + 1e-12)
        self.assertEqual(initial.number, 0)
        self.assertEqual(selected.number, 4)

    def test_denoise_keeps_high_contrast_text_edges(self):
        image = np.full((224, 320, 3), 255, dtype=np.uint8)
        image[10:100, 20:23] = 0
        np.testing.assert_array_equal(denoise_group([image], 1)[0], image)


if __name__ == "__main__":
    unittest.main()
