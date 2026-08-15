# Native-tile stream format

Date: 2026-08-15

## Physical schedule

Each 15 fps frame occupies exactly five consecutive Mode 1 sectors:

1. Four video sectors (`8,192` bytes total).
2. One auxiliary sector (`2,048` bytes): a 640-byte ADPCM region followed by a
   1,408-byte video continuation region.

The stream begins at ISO LBA 24 as `/000.DAT`. The build checks that extent and
generates the absolute CDD play packet from it. A valid frame boundary has an
`NTV1` video header and an `NTA1` header exactly four sectors later. The player
uses both signatures to discard any decoder sector still pending when direct
streaming takes over from the BIOS.

## Video packet

All integers are big-endian. The 32-byte header is:

```text
offset  size  field
0x00       4  "NTV1"
0x04       2  frame number
0x06       2  flags (bit 0: palette payload present)
0x08       2  used video bytes including header
0x0A       2  supply count (0..96)
0x0C       2  tile-map offset
0x0E       2  supply-list offset
0x10       4  CRC32 of the unpadded body
0x14       2  number of supply records in the primary video packet
0x16       2  frame-block offset of the video continuation
0x18       8  reserved
```

When flag bit 0 is set, the header is followed by 20 Neo Geo palettes. Every
palette contains transparent color zero plus 15 visible colors, for 640 bytes
total. Palettes are spatially assigned in a 5×4 grid and normally held for four
frames.

The map contains 280 unsigned 16-bit graphics-cache slot IDs in row-major
order. It is followed by the primary supply records. Remaining records begin
at the continuation offset in the fifth sector. A record never straddles the
boundary.

```text
offset  size  field
0x00       2  tile position (0..279)
0x02       2  destination graphics-cache slot (1..32767)
0x04       1  mode
0x05       1  payload length
0x06       n  payload
...      0/1  pad byte so the next record is word-aligned
```

Modes are:

- `0 RAW`: 128 native Neo Geo tile bytes.
- `1 PACKBITS`: byte-oriented PackBits producing 128 bytes.
- `2 XOR_PREVIOUS`: zero-RLE data XORed into the tile at the same position in
  the previous frame.
- `3 PLANES_PREVIOUS`: one changed-byte-lane mask followed by 32 bytes for
  every selected lane.

The offline encoder reconstructs every packet through the reference decoder
before accepting it. It also owns the circular 32,768-slot graphics-RAM cache;
slot zero is permanent black, and an allocation may never replace a slot still
referenced by the visible map.

## Auxiliary audio packet

The 12-byte header is:

```text
offset  size  field
0x00       4  "NTA1"
0x04       2  frame number
0x06       2  ADPCM payload bytes
0x08       4  payload CRC32
```

Audio is YM2610 ADPCM-A at `18,518.518 Hz`. Its header and per-frame payload
fit in the first 640 bytes of the auxiliary sector; the remaining 1,408 bytes
increase the logical video budget from 8,192 to 9,600 bytes without changing
the physical five-sector cadence. The first 1 MiB is loaded by the
BIOS as `V1.PCM`. Remaining bytes arrive in auxiliary packets and fill one
512 KiB circular staging buffer in program RAM. The Z80 alternates the two
512 KiB PCM banks and increments the sound reply byte whenever a bank becomes
inactive. The 68000 uploads the staging buffer to that inactive PCM bank while
the other bank is playing. Its upload rate is higher than the incoming ADPCM
rate, so the reader stays ahead of the producer as the circular buffer is
refilled for the following half.

Every 512 KiB ADPCM bank is encoded independently. The encoder resets its
accumulator and step index at each bank boundary, matching the state reset in
the YM2610 ADPCM-A channel-start routine.

## Program-RAM layout

```text
$020000-$07FFFF  384 KiB CDC sector ring (192 sectors)
$080000-$0FFFFF  512 KiB streamed-audio circular staging buffer
$100000-$1000FF  player state and fault diagnostics
$1A0000-$1A8BFF   previous native tiles
$1A8C00-$1B17FF   current native tiles
$1B1800-$1B1C5F   two 280-word maps
$1B1C60-$1B1EDF   prepared palettes
$1B1EE0-$1B20FF   supply position/destination lists
$1B2100-$1B48FF   five-sector frame input
$1B4900-$1B4A17   N-2 baseline-copy flags
$1B5000...        graphics-slot to previous-position lookup
```

Sprite uploads are bounded to 64 tiles per frame so the added visual data does
not consume the CPU headroom that keeps presentation cadence smooth. Before
reconstructing a baseline tile, the decoder compares its cache
slot with the slot already held by the alternating N-2 software buffer and
skips the 128-byte copy when they match. The player prepares one frame ahead
and presents only a completely decoded and uploaded map.

The offline selector ranks candidates by visual-error reduction per byte and
multiplies the score inside a one-tile screen border by 1.25. The 64-upload CPU
ceiling therefore retains the most efficient changes, with the center winning
contention as requested. The byte budget is enforced independently across the
primary and continuation regions.

The CDZ flow controller pauses at 128 buffered sectors and resumes at 48. The
CDD packet terminator writes control values `3` then `1`: the first supplies
the hardware/MAME send edge, while the second advances the NeoCD libretro
packet pointer without creating another hardware edge. The player also
disables the firmware watchdog before taking over continuous playback.

NeoCD libretro's `Skip CD Loading` option must be disabled. The core marks a
frame as loading whenever a decoder-sector IRQ arrives, and this stream
deliberately produces such IRQs continuously. With the option enabled the core
fast-forwards emulated frames and discards their audio output; the disc cannot
change a frontend core option.

`tools/render_native_stream.py` reconstructs any encoded frame directly from
`STREAM.DAT`, including cache references and native sprite byte order. It is
the offline oracle used to distinguish encoder/format errors from 68000 upload
or emulator faults:

```sh
python3 tools/render_native_stream.py \
  build/generated/STREAM.DAT 900 build/render-900.png
```
