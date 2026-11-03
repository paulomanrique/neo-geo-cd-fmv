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
and differential maps against the decoder's CPU-baseline validity contract. Its RGB
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
Add `--require-no-regression` to fail the comparison if either mean or P95
displayed Oklab error increases.

For a known frame, compare an emulator's native-resolution PNG to the stream:

```sh
python3 tools/compare_native_capture.py build/generated/STREAM.DAT 900 capture.png
```

Disable shaders and scaling in the captured image. `--overscan 8` accepts a
304×224 capture with eight pixels removed on each side. A different palette
conversion may require an explicit `--max-mean-error` tolerance. This check is
separate from source fidelity: it checks that playback matches authored data.

Encoder experiment switches:

| Option | Purpose |
|---|---|
| `--palette-fitter rgb` | Use median-cut without Oklab palette refinement |
| `--metric rgb` | Rank tile changes and match palette indices using RGB distance |
| `--palette-stability 0` | Disable retention of previous palette colors |
| `--no-full-cache` | Limit reuse to the previous visible frame |
| `--no-differential-maps` | Always emit the full 560-byte map |
| `--denoise 0` | Disable adaptive preprocessing |
| `--denoise auto` | Default: trial two strengths against unfiltered group output |
| `--denoise 1` / `--denoise 2` | Trial only one strength; quality guards still apply |

These switches are for controlled comparisons, not byte-identical reproduction
of older encoders. All retain the 64-upload ceiling and five-sector cadence.

Run the offline regression checks without media or a BIOS:

```sh
make check PYTHON=.venv/bin/python
```

The media-free cache stress fixture forces repeated eviction in a 16-slot
offline working set while retaining the real stream and hardware slot IDs:

```sh
python3 tests/create_cache_stress.py build/cache-stress/generated
```

Its generated files can replace `build/generated/` for a separate test-disc
build and MAME verification. Preserve normal generated media first. The test
specifically covers historical references, same-frame aliases, recycled IDs,
and subsequent independent or differential updates.

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
Set `FMV_TARGET_FRAME` to capture and check a specific intermediate frame.

## Emulator notes

### NeoCD regression verification

The MAME smoke test is not sufficient to validate NeoCD's sector-delivery
timing. Run the installed libretro core without a GUI to check every presented
frame, the sprite map, valid CPU tile bytes, and validity flags:

```sh
python3 tests/verify_neocd.py dist/neo-geo-cd-fmv.cue \
  --core /path/to/neocd_libretro.dylib \
  --system-dir /path/to/RetroArch/system \
  --stream build/generated/STREAM.DAT --output build/neocd-check
```

The Python interpreter must match the core's CPU architecture, for example
an x86_64 interpreter for an x86_64 macOS core. Use the same NumPy/Pillow
requirements as the encoder. BIOS files are copied into the output directory
so test save RAM does not alter the user's BIOS directory. `--fields` defaults
to 7,000; increase it for longer clips.

The image oracle models NeoCD's RGB565 palette conversion, including the
shared dark bit in green. The default RGB tolerance is zero. The test fails
on pixel errors, CPU-state mismatches, frame-sequence errors, faults, or early
termination. Native PNGs and a diagnostic RAM dump are saved alongside JSON.
The report also records the core's refresh rate and the difference between
authored video duration and elapsed audio samples. Timing drift is diagnostic;
the zero-error reconstruction checks do not establish audio/video synchronization.

For NeoCD b1e04c7, add `--check-pcm` to compare the initial PCM load and every
completed bank refill byte for byte with the authored ADPCM. The optional
check uses that core's internal memory layout and validates its RAM/VRAM
pointers before reading PCM. Missing refills also fail the test. Add
`--audio-output build/neocd-check/audio.wav` to record the core's audio output.
`--capture-every 0` disables periodic PNGs; all frames are still compared.

Generate a five-minute media-free stream to exercise successive refills and
both staging-bank wraps:

```sh
python3 tests/create_audio_stress.py build/audio-stress/generated
```

Build its generated files as a separate test disc, then run the NeoCD verifier
with `--check-pcm --fields 21000`. Preserve normal generated media before
replacing `build/generated/` with a fixture.

Add `--starve-frame 300 --starve-fields 12` to temporarily hide available CD
sectors and then restore them. This checks that waiting for CD data cannot
re-present an old frame or corrupt the differential decoder's state.

### Loading the disc

- MAME: load `dist/neo-geo-cd-fmv.cue` with the Neo Geo CDZ machine.
- NeoCD libretro: load the CUE, set `Skip CD Loading` to `Off`, and set horizontal
  overscan to `0` to show the full 320-pixel image.
- Original hardware: report console model, drive condition, media type, burner,
  observed end-to-end playback, audio behavior, and any repeated failure point.
