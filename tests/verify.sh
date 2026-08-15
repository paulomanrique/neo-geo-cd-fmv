#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${MAME_ROMPATH:?Set MAME_ROMPATH to the directory containing neocdz.zip}"
MAME_BIN=${MAME_BIN:-mame}
BIOS="$MAME_ROMPATH/neocdz.zip"

test -s "$ROOT/dist/neogeocd-video-demo.iso"
test -s "$ROOT/dist/neogeocd-video-demo.bin"
test -s "$ROOT/dist/neogeocd-video-demo.cue"
test -f "$BIOS"

isoinfo -i "$ROOT/dist/neogeocd-video-demo.iso" -f > "$ROOT/build/iso-files.txt"
for file in /000.DAT /IPL.TXT /M1.Z80 /MAIN.PRG /S1.FIX /V1.PCM; do
    grep -Fx "$file" "$ROOT/build/iso-files.txt" >/dev/null
done

python3 "$ROOT/tools/disc_layout.py" "$ROOT/build/generated/disc.inc" \
    --lba 24 --verify-iso "$ROOT/dist/neogeocd-video-demo.iso"
python3 "$ROOT/tests/test_audio_banks.py"

CAPTURE_DIR=$(mktemp -d "$ROOT/build/mame-run.XXXXXX")
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
    "$MAME_BIN" neocdz -rompath "$MAME_ROMPATH" -bios 0 \
    -cdrom "$ROOT/dist/neogeocd-video-demo.cue" \
    -skip_gameinfo -nothrottle -video soft -sound none -window \
    -snapshot_directory "$CAPTURE_DIR" -snapname frame \
    -autoboot_script "$ROOT/tests/mame_short.lua" \
    > "$CAPTURE_DIR/mame.log" 2>&1

grep -F 'FMV_SMOKE_OK' "$CAPTURE_DIR/mame.log" >/dev/null
test -s "$CAPTURE_DIR/frame.png"
echo "verified: ISO extent, native-tile decode, graphics upload, and CDZ flow control"
