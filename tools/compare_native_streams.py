#!/usr/bin/env python3
"""Measure two authored streams against the same normalized source frames."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

from encode_native_stream import video_frames
from export_native_video import frames
from perceptual import squared_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--require-no-regression", action="store_true",
                        help="fail if mean or P95 displayed Oklab error increases")
    args = parser.parse_args()
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    metadata_by_name = {}
    for name, directory in (("before", args.before), ("after", args.after)):
        metadata = json.loads((directory / "video.json").read_text())
        if metadata["source_sha256"] != source_hash:
            raise ValueError(f"{name} was encoded from a different source file")
        metadata_by_name[name] = metadata
    errors = {name: {"rgb": [], "oklab": []} for name in ("before", "after")}
    rows = itertools.zip_longest(video_frames(args.source),
                                 frames(args.before / "STREAM.DAT"),
                                 frames(args.after / "STREAM.DAT"))
    for source, before, after in rows:
        if source is None or before is None or after is None:
            raise ValueError("source and both streams must have the same frame count")
        for name, image in (("before", before), ("after", after)):
            errors[name]["rgb"].append(float(np.abs(
                source.astype(np.int16) - image.astype(np.int16)).mean()))
            errors[name]["oklab"].append(float(squared_error(source, image).mean()))
    report = {}
    for name, directory in (("before", args.before), ("after", args.after)):
        metadata = metadata_by_name[name]
        if not errors[name]["rgb"] or len(errors[name]["rgb"]) != metadata["frame_count"]:
            raise ValueError("stream length disagrees with metadata")
        report[name] = {
            "frames": len(errors[name]["rgb"]),
            "mean_absolute_rgb_error": float(np.mean(errors[name]["rgb"])),
            "mean_squared_oklab_error": float(np.mean(errors[name]["oklab"])),
            "p95_frame_oklab_error": float(np.percentile(errors[name]["oklab"], 95)),
            "average_supplies": metadata["average_supplies"],
            "average_used_video_bytes": metadata["average_used_video_bytes"],
        }
    for key in ("mean_absolute_rgb_error", "mean_squared_oklab_error"):
        before = report["before"][key]
        report[key + "_reduction_percent"] = (
            100 * (before - report["after"][key]) / before if before else 0.0
        )
    passed = all(report["after"][key] <= report["before"][key] + 1e-12
                 for key in ("mean_squared_oklab_error", "p95_frame_oklab_error"))
    report["no_perceptual_regression"] = passed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(args.require_no_regression and not passed)


if __name__ == "__main__":
    raise SystemExit(main())
