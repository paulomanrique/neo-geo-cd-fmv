# Architecture

## Data path

```text
source video
    │  ffmpeg: aspect-preserving scale/crop, 320×224, 15 fps
    ▼
offline encoder
    │  spatial palettes + native tiles + cache allocation + rate control
    ▼
five-sector frame blocks on CD
    │  68000 CDC interrupt + 384 KiB program-RAM ring
    ▼
tile reconstruction and graphics-RAM upload
    │  one fully prepared frame ahead
    ▼
20 sprite columns + YM2610 ADPCM-A audio
```

The encoder is intentionally expensive and the decoder intentionally simple.
Every candidate tile representation is tested offline, and only bounded
operations that map cleanly to the 68000 are emitted.

## Picture model

The visible 320×224 area is exactly 20×14 native 16×16 tiles. Each tile is 128
bytes in Neo Geo sprite bitplane order. The screen uses a fixed 5×4 grid of
64×64-pixel palette regions. Each region receives one transparent entry and 15
visible colors; palette entries are matched across updates so indices remain as
stable as possible.

Palettes are trained over groups of four frames. Within each frame the encoder
constructs the image the decoder would actually display, not an ideal target
that may exceed the packet budget. Future deltas are always computed from this
reconstructed state.

## Graphics RAM as a dictionary

Four MiB of graphics RAM provide 32,768 native tile slots. Slot zero is a
permanent black tile. The offline allocator assigns every uploaded tile a cache
slot and never overwrites a slot referenced by the visible map.

Before sending pixel data, the encoder checks whether the exact target tile was
visible in the preceding frame. A hit becomes a map reference and costs no
graphics upload. A miss is represented by the smallest of:

- `RAW`: 128 native bytes;
- `PACKBITS`: byte-oriented PackBits;
- `XOR_PREVIOUS`: XOR against the same screen position, then zero-run RLE;
- `PLANES_PREVIOUS`: a four-bit changed-plane mask plus 32 bytes per plane.

Candidates are ranked by visual-error reduction per encoded byte. Positions
inside a one-tile border receive a 1.25× score, preserving detail in the center
of the frame. At most 64 tiles are uploaded per frame; optical space and CPU
upload work are separate budgets.

## Physical CD schedule

At 15 fps, five sectors per frame consume the complete 75-sector/s cooked 1×
rate:

| Sector bytes | Purpose |
|---:|---|
| 8,192 | Primary video packet: header, optional palettes, map, supplies |
| 640 | Auxiliary header and 617–618 bytes of ADPCM-A |
| 1,408 | Video supply continuation |

The logical video ceiling is therefore 9,600 bytes per frame while the physical
frame block remains 10,240 bytes. Supply records never cross the primary/
continuation boundary.

The LC8951 decoder interrupt copies each 2,048-byte sector into a 384 KiB ring.
Flow control pauses CD reading at 128 buffered sectors and resumes at 48. Each
prepared frame consumes exactly five sectors.

## Presentation and CPU work

The 68000 reconstructs a frame in alternating software tile buffers. Exact
cache references avoid redundant 128-byte baseline copies. New tiles are
uploaded during VBlank, then the 280-word sprite map and any pending palettes
are committed as one presentation step.

A fixed-point accumulator maps the 15 fps video clock onto the Neo Geo refresh
clock of approximately 59.1856 Hz. Audio is authoritative over long playback:
video never changes the physical five-sector cadence to chase a decode burst.

## Audio path

Audio is mono YM2610 ADPCM-A at 18,518.518 samples/s. The first 1 MiB is loaded
at boot as `V1.PCM`. Later bytes arrive in the 640-byte auxiliary region and
fill a 512 KiB circular staging buffer in program RAM.

The Z80 alternates two 512 KiB ADPCM-A channels. When one half completes, it
starts the other and increments a reply byte. The 68000 observes that reply and
uploads the next staged half into the now-inactive PCM bank. The ADPCM encoder
resets its accumulator and step index at every 512 KiB boundary, matching the
YM2610 decoder reset when a channel starts.

## Program RAM layout

| Range | Purpose |
|---|---|
| `$020000–$07FFFF` | 384 KiB CDC sector ring |
| `$080000–$0FFFFF` | 512 KiB streamed-audio staging buffer |
| `$100000–$1000FF` | Player state and diagnostics |
| `$1A0000–$1B17FF` | Alternating native-tile buffers |
| `$1B1800–$1B1C5F` | Alternating 280-word maps |
| `$1B1C60–$1B1EDF` | Prepared palettes |
| `$1B1EE0–$1B20FF` | Supply position/destination tables |
| `$1B2100–$1B48FF` | Five-sector frame input |
| `$1B4900–$1B4A17` | Baseline-copy flags |
| `$1B5000…` | Graphics-slot-to-position lookup |

## Current constraints

- Video format, geometry, and rate are fixed at 320×224 and 15 fps.
- The stream consumes the complete 1× cooked sector rate; additional features
  must reuse bytes already present or reduce another payload.
- The 64-tile upload ceiling is deliberate. A 96-tile profile improved offline
  image error but produced visibly riskier presentation scheduling in measured
  execution.
- The public release has emulator validation but still needs physical Neo Geo
  CD and CDZ testing.
