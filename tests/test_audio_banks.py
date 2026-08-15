#!/usr/bin/env python3
"""Verify that ADPCM encoder state resets at every playback-bank boundary."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from encode_native_stream import encode_adpcm_a, encode_adpcm_a_banks


def main() -> int:
    bank_bytes = 64
    samples_per_bank = bank_bytes * 2
    samples = [((index * 997) & 0xFFFF) - 0x8000 for index in range(samples_per_bank * 3)]
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    encoded = encode_adpcm_a_banks(pcm, bank_bytes)

    for bank in range(3):
        start = bank * samples_per_bank * 2
        end = start + samples_per_bank * 2
        expected = encode_adpcm_a(pcm[start:end])
        actual = encoded[bank * bank_bytes : (bank + 1) * bank_bytes]
        assert actual == expected

    assert encoded != encode_adpcm_a(pcm)
    print("verified: ADPCM encoder resets at playback-bank boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
