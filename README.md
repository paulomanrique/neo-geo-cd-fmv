# Neo Geo CD FMV

A purpose-built full-motion video codec and bootable player for the original
Neo Geo CD architecture.

The project converts ordinary video offline into native Neo Geo 16×16 sprite
tiles, streams the result at the full 1× CD rate, decodes it on the 68000, and
plays synchronized YM2610 ADPCM-A audio. It does not use MPEG, Cinepak, MJPEG,
or a framebuffer.

No source video, BIOS, copyrighted media, or generated disc image is included.
Bring media you have the right to use and a legally obtained Neo Geo CD BIOS
for optional emulator verification.

## What it does

- 320×224 video at 15 fps.
- 20×14 native 16×16, 4 bpp sprite tiles per frame.
- Twenty spatial 15-color palettes, refreshed every four frames.
- Reconstruction-aware differential encoding with RAW, PackBits,
  XOR-plus-zero-RLE, bitplane updates, and exact graphics-cache reuse.
- A 32,768-tile graphics RAM dictionary managed entirely by the offline
  encoder.
- Continuous 75-sector/s Mode 1 streaming with a 384 KiB read-ahead ring.
- 18,518.518 Hz mono ADPCM-A audio, double-buffered across the YM2610's 1 MiB
  PCM RAM.
- Direct boot into playback and BIN/CUE output.

## Why a custom codec

The Neo Geo is already a tile decompressor in hardware. A full 320×224 4 bpp
frame costs 35,840 bytes, but the screen is also exactly 280 native sprite
tiles. The codec treats graphics RAM as a persistent dictionary: unchanged or
previously seen tiles cost only a map reference, and the stream sends pixel
data only for selected cache misses.

At 15 fps, a 1× drive supplies exactly five 2,048-byte sectors per frame. Four
sectors form the primary video packet. The fifth reserves 640 bytes for audio
and uses its remaining 1,408 bytes as a video continuation, producing a 9,600
byte logical video budget without increasing optical bandwidth.

See [Architecture](docs/architecture.md),
[Stream format](docs/stream-format.md), and
[Measured results](docs/benchmarks.md).

## Build

Install the native tools listed in [Build and verification](docs/build.md),
then:

```sh
git clone https://github.com/paulomanrique/neo-geo-cd-fmv.git
cd neo-geo-cd-fmv
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp /path/to/your/video.mp4 assets/input.mp4
make -j4
```

The loadable disc pair is:

```text
dist/neo-geo-cd-fmv.cue
dist/neo-geo-cd-fmv.bin
```

Open the `.cue`, not the `.bin`. The CUE describes cooked `MODE1/2048` data.

To use a different input path without copying it:

```sh
make INPUT_VIDEO=/absolute/path/to/video.mp4
```

## Verification status

The complete 3,432-frame development reel was encoded and the shipping disc
path was exercised in MAME 0.285 through frame 1,800, including two streamed
PCM bank transitions. The measured presentation interval was 7,102 display
fields, exactly matching 1,800 frames at 15 fps on the Neo Geo refresh clock.

Original-hardware validation is still wanted. The implementation is designed
from the Neo Geo CD memory map, BIOS/CD controller behavior, YM2610 behavior,
and measured emulator execution, but this repository does not claim a physical
console test that has not happened.

For the NeoCD libretro core, disable `Skip CD Loading`. Continuous decoder
sector interrupts otherwise look like a loading screen to that core, causing
fast-forwarded video and dropped frontend audio. This option is not relevant to
MAME or original hardware.

## Repository policy

This repository intentionally contains no GitHub Actions. Builds and emulator
tests are local because they require native media tools and a user-supplied
BIOS. Generated media and all files under `assets/` are ignored.

## License

The original source in this repository is available under the MIT License.
External tools fetched by the build, including Macroassembler AS, retain their
own licenses.
