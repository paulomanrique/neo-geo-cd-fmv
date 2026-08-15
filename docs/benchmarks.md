# Measured results

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
