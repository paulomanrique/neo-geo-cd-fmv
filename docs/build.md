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

## Emulator notes

- MAME: load `dist/neo-geo-cd-fmv.cue` with the Neo Geo CDZ machine.
- NeoCD libretro: load the CUE, set `Skip CD Loading` to `Off`, and set horizontal
  overscan to `0` to show the full 320-pixel image.
- Original hardware: report console model, drive condition, media type, burner,
  observed end-to-end playback, audio behavior, and any repeated failure point.
