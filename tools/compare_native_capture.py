#!/usr/bin/env python3
"""Compare a native-resolution emulator screenshot with an authored frame."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
from PIL import Image

from export_native_video import frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stream", type=Path)
    parser.add_argument("frame", type=int)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--overscan", type=int, default=0)
    parser.add_argument("--max-mean-error", type=float, default=0.0)
    args = parser.parse_args()
    if args.frame < 0 or not 0 <= args.overscan < 160 or args.max_mean_error < 0:
        parser.error("frame, overscan, or error limit is invalid")
    reference = next(itertools.islice(frames(args.stream), args.frame, args.frame + 1), None)
    if reference is None:
        parser.error("frame is outside the stream")
    reference = reference[:, args.overscan:320 - args.overscan]
    capture = np.asarray(Image.open(args.capture).convert("RGB"))
    if capture.shape != reference.shape:
        parser.error(f"capture must be {reference.shape[1]}x224 without UI, scaling, or shaders")
    difference = np.abs(capture.astype(np.int16) - reference.astype(np.int16))
    mean = float(difference.mean())
    print(json.dumps({"frame": args.frame, "mean_absolute_rgb_error": mean,
                      "exact_match": bool(np.array_equal(capture, reference)),
                      "pass": mean <= args.max_mean_error}, indent=2))
    return int(mean > args.max_mean_error)


if __name__ == "__main__":
    raise SystemExit(main())
