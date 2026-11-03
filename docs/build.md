# Build and verification

## Host dependencies

The build has been exercised on Linux. Install:

- Python 3.11 or newer;
- `ffmpeg` and `ffprobe`;
- GNU Make and a C compiler;
- `mkisofs` and `isoinfo` (often packaged as `cdrtools` or `genisoimage`);
- `chdman` from MAME tools;
- Git;
- Python packages from `requirements.txt`.

The Makefile fetches Macroassembler AS at a pinned commit and builds it under
`vendor/asl`. No console SDK is required.

## Input normalization

Place one source video at `assets/input.mp4`, or pass an explicit path with
`INPUT_VIDEO`. The encoder uses ffmpeg to:

1. preserve aspect ratio while scaling to fit inside 320×224;
2. center the complete picture on a black 320×224 canvas without cropping;
3. resample presentation to exactly 15 fps;
4. downmix audio to mono and resample it for YM2610 ADPCM-A.

Concatenate multiple clips before building. For example:

```sh
printf "file '%s'\n" clip-1.mp4 clip-2.mp4 > clips.txt
ffmpeg -f concat -safe 0 -i clips.txt -c copy assets/input.mp4
```

If the sources cannot be concatenated losslessly, normalize them first or let
ffmpeg re-encode the combined file.

## Build commands

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
make -j4
```

Outputs:

```text
dist/neo-geo-cd-fmv.iso   intermediate cooked ISO
dist/neo-geo-cd-fmv.bin   cooked MODE1/2048 data track
dist/neo-geo-cd-fmv.cue   emulator/burner entry point
```

Always open the CUE. A `.bin` extension alone does not identify sector layout.

## Offline inspection

Render a frame directly from the authored native stream:

```sh
python3 tools/render_native_stream.py \
  build/generated/STREAM.DAT 900 build/frame-900.png
```

The renderer follows the same cache references, continuation boundary, native
sprite byte order, and palette mapping as the target decoder.

Export the complete reconstructed stream, including decoded ADPCM-A audio, to
a lossless Matroska file:

```sh
python3 tools/export_native_video.py build/generated build/preview.mkv
```

The exporter checks frame and audio CRCs, record boundaries, cache writes,
and references against the decoder's previous-visible-map contract. Its RGB
preview uses the reference renderer's palette model; it is not an emulator
capture or a model of an analog display. Audio uses a WAV rate rounded to
18,519 Hz rather than the chip's fractional rate.

Compare two generated directories against the same source, with identical
normalization and palette reconstruction for both:

```sh
python3 tools/compare_native_streams.py input.mp4 \
  build/before/generated build/after/generated build/comparison.json
```

The report includes displayed RGB error, Oklab error, and the 95th percentile
of per-frame Oklab error. It measures complete frames, including tiles that
the encoder chose not to update.

For a known frame, compare an emulator's native-resolution PNG to the stream:

```sh
python3 tools/compare_native_capture.py build/generated/STREAM.DAT 900 capture.png
```

Disable shaders and scaling in the captured image. `--overscan 8` accepts a
304×224 capture with eight pixels removed on each side. A different palette
conversion may require an explicit `--max-mean-error` tolerance. This check is
separate from source fidelity: it checks that playback matches authored data.

`--metric rgb --palette-stability 0` on the encoder disables the new perceptual
selection and palette-retention policy for controlled experiments. Frame byte
budgets and the 64-tile upload ceiling are independent of these options.

Run the offline regression checks without media or a BIOS:

```sh
make check PYTHON=.venv/bin/python
```

Analyze codec candidates on any set of clips:

```sh
python3 tools/analyze_tile_codec.py --fps 15 20 24 30 \
  --output build/codec-analysis.json clip-1.mp4 clip-2.mp4
```

## MAME verification

MAME verification requires a legally obtained `neocdz.zip` BIOS set. Point the
test at the directory containing it:

```sh
MAME_ROMPATH=/path/to/mame/roms make verify
```

Override the executable when needed:

```sh
MAME_BIN=/path/to/mame MAME_ROMPATH=/path/to/mame/roms make verify
```

The smoke test checks:

- ISO file structure and the fixed stream extent;
- ADPCM state reset at 512 KiB playback-bank boundaries;
- boot and continuous CD decoder progress;
- frame 1,800 presentation timing;
- sprite, palette, and tile activity;
- audio production through two bank transitions;
- ring overflow and fatal decoder diagnostics.

For clips shorter than 1,801 frames, verification captures the last frame
instead. The capture is compared with the authored stream using a default
mean RGB tolerance of 1.0/255 for palette-conversion differences. Override
`MAME_MAX_MEAN_ERROR` only when using a different documented color model.

## Emulator notes

- MAME: load `dist/neo-geo-cd-fmv.cue` with the Neo Geo CDZ machine.
- NeoCD libretro: load the CUE, set `Skip CD Loading` to `Off`, and set horizontal
  overscan to `0` to show the full 320-pixel image.
- Original hardware: report console model, drive condition, media type, burner,
  observed end-to-end playback, audio behavior, and any repeated failure point.
