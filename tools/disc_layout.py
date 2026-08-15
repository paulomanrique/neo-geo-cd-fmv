#!/usr/bin/env python3
"""Generate and verify the absolute CD command for the linear FMV stream."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def packet_for_lba(lba: int) -> list[int]:
    absolute = lba + 150
    minute, remainder = divmod(absolute, 75 * 60)
    second, frame = divmod(remainder, 75)
    packet = [0x30, bcd(minute), bcd(second), bcd(frame), 0]
    checksum_sum = 5
    for value in packet[:4]:
        checksum_sum += value >> 4
        checksum_sum += value & 0x0F
    checksum_sum += packet[4] >> 4
    packet[4] = (~checksum_sum) & 0x0F
    return packet


def iso_extent(path: Path, name: str) -> int:
    listing = subprocess.check_output(
        ["isoinfo", "-i", str(path), "-l"], text=True
    )
    pattern = re.compile(r"\[\s*(\d+)\s+\d+\]\s+" + re.escape(name) + r"\s*$")
    for line in listing.splitlines():
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    raise RuntimeError(f"{name} was not found in the ISO directory listing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--lba", type=int, default=24)
    parser.add_argument("--verify-iso", type=Path)
    parser.add_argument("--name", default="000.DAT")
    args = parser.parse_args()

    if args.verify_iso is not None:
        actual = iso_extent(args.verify_iso, args.name)
        if actual != args.lba:
            raise RuntimeError(
                f"stream extent moved: expected LBA {args.lba}, found {actual}"
            )

    packet = packet_for_lba(args.lba)
    nibbles: list[int] = []
    for value in packet:
        nibbles.extend((value >> 4, value & 0x0F))
    lines = [f"VIDEO_STREAM_LBA equ {args.lba}"]
    lines.extend(
        f"STREAM_COMMAND_NIBBLE_{index} equ ${value:02X}"
        for index, value in enumerate(nibbles)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"stream LBA {args.lba}, play packet " + " ".join(f"{x:02X}" for x in packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
