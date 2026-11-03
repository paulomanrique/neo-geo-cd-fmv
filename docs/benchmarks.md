# Measured results

## Long complex clip and streamed-audio regression

A 198.13-second animated music video produced 2,972 frames. The encoder averaged
63.315 tile uploads and 8,057.39 used video bytes per frame, with peaks of 64
and 9,518 respectively. This is substantially closer to the upload ceiling
than the earlier 1,233-frame clip.

NeoCD b1e04c7 checked all 2,972 displayed frames, maps, valid CPU tile bytes,
and validity flags with zero mismatches. The initial 1 MiB PCM load and both
subsequent refills also matched every authored byte. MAME 0.289 reached frame
2,971 in 11,722 presentation fields, with a final capture mean RGB difference
of 0.0762/255.

The long test exposed a staging-buffer bug: the producer could wrap a single
512 KiB ring before upload of the previous half began. A deterministic
three-minute fixture reproduced 26,161 altered bytes in the first refill.
Two separate staging banks fixed the failure. A five-minute fixture then
checked all 4,500 frames and four refills, including reuse of both staging
banks, with zero image or PCM mismatches.

Timing remains a separate limitation. The player schedules against 59.1856 Hz,
while this NeoCD core reports 59.599905 Hz. The complex clip ended about 1.3
seconds ahead of its audio (1.221 seconds measured). Exact image/PCM reconstruction does not imply
correct synchronization; the current headless checks report timing separately.
These results do not establish physical-console playback quality.

## NeoCD sector-underrun regression

NeoCD 2022 b1e04c7 reproduced a player bug not exposed by the MAME captures:
an exhausted upload could set readiness again while the main loop waited for
CD sectors. The first CPU/map mismatch appeared at frame 369, and the old
image stopped at frame 792 with audio continuing.

The corrected player consumes each decoded frame's readiness once, checks
stream sequence numbers, and starts with 96 buffered sectors. Headless tests
of the same NeoCD core checked all 1,233 frames against its RGB565 color model:
all images, displayed maps, valid CPU tile bytes, and validity flags matched,
with zero pixel error. Repeating the test with a forced 12-field sector wait
at frame 300 also completed with zero mismatches or sequence errors. The MAME
test still completed in 4,861 presentation fields.

## Additional 1,233-frame evaluation

A separate 82.2-second clip was normalized with the same fit/pad geometry for
all three encoders. These are full displayed-frame measurements from the same
stream renderer, not encoder-only palette errors or results from the older
3,432-frame corpus below.

| Encoder | Mean RGB absolute error | Mean squared Oklab error | P95 frame Oklab error | Average uploads | Average used video bytes |
|---|---:|---:|---:|---:|---:|
| Original, corrected geometry | 3.78544 | 0.00360937 | 0.00823723 | 28.054 | 2,775.47 |
| Perceptual selection and palette retention | 3.69598 | 0.00353487 | 0.00847944 | 22.036 | 2,494.61 |
| Oklab palette fit, resident cache, differential maps, adaptive filtering | 2.75154 | 0.00159267 | 0.00472381 | 18.742 | 1,787.57 |

Against the first perceptual version, the combined changes reduce mean Oklab
error by 54.94%, RGB error by 25.55%, and P95 frame Oklab error by 44.29%.
These are content-specific results, not guaranteed gains for arbitrary films.
The optical rate remains 153,600 B/s: savings become additional payload
headroom, not a smaller physical stream.

The combined stream uses 1,200 differential maps, averaging 97.35 map bytes
instead of 560. It reuses 3,869 historical tiles and collapses 432 same-frame
duplicates. Adaptive filtering wins 75 four-frame groups under the local
mean/P95 guards.

MAME 0.289 checked the combined image at frames 800, 1,200, and 1,232. The final
interval was 4,861 display fields with two late fields. Native captures had
mean RGB differences of 0.413, 0.260, and 0.335 against authored frames, under
the 1.0/255 palette-conversion tolerance. The updated player also passed the
previous full-map stream through frame 1,232. No physical-console test is
claimed.

A media-free 96-frame fixture also passed MAME capture comparison after 281
uploads through a deliberately restricted 16-slot working set. This forces
repeated cache recycling independently of how much the development video
happens to reuse, with a final mean RGB difference of 0.040/255.

## Original 3,432-frame corpus

These measurements come from a 3,432-frame, 228.8-second development corpus at
320×224 and 15 fps. The media is not distributed and no source hashes are
published. All image-error figures compare the RGB source with the encoder's
reconstructed output.

## Encoder profiles

| Profile | Logical video ceiling | Upload ceiling | Average used bytes/frame | Average uploads/frame | Mean absolute RGB error |
|---|---:|---:|---:|---:|---:|
| Four video sectors | 8,192 | 96 | 7,937.56 | 57.85 | 5.4492 |
| Auxiliary continuation, balanced | 9,600 | 64 | 8,602.15 | 62.59 | 5.2112 |
| Auxiliary continuation, unrestricted | 9,600 | 96 | 9,245.40 | 68.61 | 5.0214 |

The balanced profile reduces mean absolute RGB error by 4.37% relative to the
original four-sector encoder while preserving decoder scheduling headroom.

## Runtime scheduling

MAME 0.285 ran the bootable CDZ image through frame 1,800 and two PCM-bank
transitions:

| Profile | Display fields for 1,800 frames | Late-field counter | Sectors received near frame 1,800 |
|---|---:|---:|---:|
| Four video sectors | 7,102 | 418 | 9,021 |
| Balanced continuation, 64 uploads | 7,102 | 573 | 9,019 |
| Unrestricted continuation, 96 uploads | 7,102 | 4,392 | 9,021 |

`7,102` fields is the expected interval for 1,800 frames at 15 fps on the
approximately 59.1856 Hz Neo Geo refresh clock. The unrestricted profile could
eventually catch up, but its late counter showed that it often prepared frames
after their ideal presentation point and then recovered in bursts. It was not
selected for release.

This is the central engineering result: unused bytes in a sector are genuine
optical bandwidth headroom, but they are not automatically CPU headroom. CD
rate, decode work, graphics upload work, and presentation cadence must be
budgeted independently.

## Uncompressed comparison

A raw 320×224 4 bpp frame is 35,840 bytes. The raw rates would be:

| Frame rate | Raw video rate | Compression needed for a 150 KiB/s 1× CD before audio |
|---:|---:|---:|
| 15 fps | 537,600 B/s | 3.50:1 |
| 20 fps | 716,800 B/s | 4.67:1 |
| 24 fps | 860,160 B/s | 5.60:1 |
| 30 fps | 1,075,200 B/s | 7.00:1 |

The release uses the full 153,600 B/s cooked Mode 1 payload. Of that, about
9,259 B/s is streamed ADPCM-A; frame headers, maps, palettes, and padding share
the rest with tile supplies.
