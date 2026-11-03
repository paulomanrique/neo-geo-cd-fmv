# Architecture

## Data path

```text
source video
    │  ffmpeg: aspect-preserving scale/pad, 320×224, 15 fps
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

Palettes are trained over groups of four frames. Median-cut seeds a weighted
Oklab fit whose centroids are projected onto representable 5-bit colors; the
fit is checked against the original RGB pixels before it replaces the seed.
A region keeps its previous
palette when its Oklab error is within 2% of the newly fitted palette's error.
Unchanged palette payloads are omitted. Palette indices are matched using
Oklab distance. Within each frame the encoder
constructs the image the decoder would actually display, not an ideal target
that may exceed the packet budget. Future deltas are always computed from this
reconstructed state.

Adaptive preprocessing trials apply a small median filter only where a pixel
differs from its neighborhood by at most 8 or 16 channel levels. High-contrast
edges are left untouched. Each trial encodes the complete four-frame group
from the same state, including cache allocation, map bytes, and upload limits.
It is selected only if neither mean nor 95th-percentile displayed Oklab error
regresses against the unfiltered group. Original RGB frames remain the scoring
reference. This is a local group guarantee, not a whole-film quality promise.

## Graphics RAM as a dictionary

Four MiB of graphics RAM provide 32,768 native tile slots. Slot zero is a
permanent black tile. The offline allocator assigns every uploaded tile a cache
slot and never overwrites a slot referenced by the visible or prepared map.
Eviction removes the overwritten slot from the exact-pattern lookup.

Before sending pixel data, the encoder checks whether the exact target tile was
visible in the preceding frame, then checks the complete resident dictionary.
Duplicates first uploaded in the current frame share that upload too. A hit
becomes a map reference and costs no graphics upload. A miss is represented by
the smallest valid encoding:

- `RAW`: 128 native bytes;
- `PACKBITS`: byte-oriented PackBits;
- `XOR_PREVIOUS`: XOR against the same screen position, then zero-run RLE;
- `PLANES_PREVIOUS`: a four-bit changed-plane mask plus 32 bytes per plane.

A historical cache reference can be displayed without copying its pixels back
into program RAM. The encoder and 68000 track which software positions have
valid pixels. XOR and plane updates are forbidden without a valid previous
CPU baseline; the first subsequent upload then uses RAW or PackBits. This
avoids graphics-RAM readbacks and keeps historical reuse independent of CPU
tile-buffer capacity.

Maps use either 560 raw bytes or a sorted list of changed position/slot pairs,
whichever is smaller. Map growth is included while selecting supplies, so
savings become usable video budget within the same five sectors. The player
still commits the complete prepared sprite map during presentation.

Candidates are ranked by squared Oklab error reduction per encoded byte. Positions
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

Playback starts after 96 synchronized sectors have accumulated, leaving a
read-ahead reserve before audio starts. This buffers about 1.28 seconds of
stream before playback rather than beginning at the ring's lower limit.

## Presentation and CPU work

The 68000 reconstructs a frame in alternating software tile buffers. Exact
cache references avoid redundant 128-byte baseline copies. New tiles are
uploaded during VBlank, then the 280-word sprite map and any pending palettes
are committed as one presentation step.

`FrameDecoded` distinguishes an unpresented decoded frame from an already
consumed upload. Only an unpresented frame can become `PreparedReady`, and
presentation consumes that state. If the next five sectors are unavailable,
the player holds the visible frame; a completed old upload cannot advertise
readiness again. Frame sequence numbers are also checked before decoding,
and contract failures set the `DECO` fault marker for emulator diagnostics.

A fixed-point accumulator maps the 15 fps video clock onto the Neo Geo refresh
clock of approximately 59.1856 Hz. Audio is authoritative over long playback:
video never changes the physical five-sector cadence to chase a decode burst.

## Audio path

Audio is mono YM2610 ADPCM-A at 18,518.518 samples/s. The first 1 MiB is loaded
at boot as `V1.PCM`. Later bytes arrive in the 640-byte auxiliary region and
fill two alternating 512 KiB staging banks in program RAM.

The Z80 alternates two 512 KiB ADPCM-A channels. When one half completes, it
starts the other and increments a reply byte. The 68000 observes that reply and
uploads the next staged half into the now-inactive PCM bank. The ADPCM encoder
resets its accumulator and step index at every 512 KiB boundary, matching the
YM2610 decoder reset when a channel starts.

The producer can begin filling the following staging bank while the previous
one is being uploaded. A single 512 KiB ring is insufficient: CD read-ahead
can overwrite its beginning before the first upload starts.

## Program RAM layout

| Range | Purpose |
|---|---|
| `$020000–$07FFFF` | 384 KiB CDC sector ring |
| `$080000–$0FFFFF` | Audio staging bank A, 512 KiB |
| `$100000–$1000FF` | Player state and diagnostics |
| `$110000–$18FFFF` | Audio staging bank B, 512 KiB |
| `$1A0000–$1B17FF` | Alternating native-tile buffers |
| `$1B1800–$1B1C5F` | Alternating 280-word maps |
| `$1B1C60–$1B1EDF` | Prepared palettes |
| `$1B1EE0–$1B20FF` | Supply position/destination tables |
| `$1B2100–$1B48FF` | Five-sector frame input |
| `$1B4900–$1B4A17` | Baseline-copy flags |
| `$1B4A18–$1B4C47` | Two 280-byte CPU-baseline validity arrays |
| `$1B4C48–$1B4E77` | Differential-map reconstruction scratch |
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
