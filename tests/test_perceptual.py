"""Regression checks for palette stability and authored-stream validation."""

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from encode_native_stream import quantize_group
from export_native_video import frames, unpack_tiles
from perceptual import oklab
from render_native_stream import tile_indices


def black_packet() -> bytes:
    body = bytes(560)
    header = struct.pack(">4sHHHHHHIHH8s", b"NTV1", 0, 0, 592, 0, 32, 592,
                         zlib.crc32(body), 0, 8832, bytes(8))
    aux = struct.pack(">4sHHI", b"NTA1", 0, 0, 0)
    return (header + body).ljust(8192, b"\0") + aux.ljust(2048, b"\0")


class PerceptualTests(unittest.TestCase):
    def test_reference_colors(self):
        result = oklab(np.array([[0, 0, 0], [255, 255, 255], [255, 0, 0]]))
        np.testing.assert_allclose(result[0], [0, 0, 0], atol=1e-6)
        np.testing.assert_allclose(result[1], [1, 0, 0], atol=1e-6)
        np.testing.assert_allclose(result[2], [.627955, .224863, .125846], atol=1e-6)

    def test_stationary_palette_survives_but_cut_replaces_it(self):
        black = np.zeros((224, 320, 3), dtype=np.uint8)
        _, palette = quantize_group([black], None)
        indices, retained = quantize_group([black], palette)
        self.assertEqual(retained, palette)
        self.assertEqual(indices[0].max(), 1)
        _, changed = quantize_group([np.full_like(black, 255)], palette)
        self.assertNotEqual(changed, palette)

    def test_vector_decoder_matches_reference(self):
        rng = np.random.default_rng(8)
        tiles = [rng.bytes(128) for _ in range(280)]
        for tile, decoded in zip(tiles, unpack_tiles(tiles), strict=True):
            np.testing.assert_array_equal(decoded, tile_indices(tile))

    def test_valid_black_frame_and_corrupted_video_audio_and_map(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "STREAM.DAT"
            packet = black_packet()
            path.write_bytes(packet)
            decoded = list(frames(path))
            self.assertEqual(len(decoded), 1)
            self.assertFalse(decoded[0].any())
            for offset in (32, 8192, 8200):
                damaged = bytearray(packet)
                damaged[offset] ^= 1
                path.write_bytes(damaged)
                with self.assertRaises(ValueError):
                    list(frames(path))


if __name__ == "__main__":
    unittest.main()
