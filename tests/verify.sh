#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${MAME_ROMPATH:?Set MAME_ROMPATH to the directory containing neocdz.zip}"
MAME_BIN=${MAME_BIN:-mame}
NAME=${NAME:-neo-geo-cd-fmv}
PYTHON=${PYTHON:-python3}
BIOS="$MAME_ROMPATH/neocdz.zip"

test -s "$ROOT/dist/$NAME.iso"
test -s "$ROOT/dist/$NAME.bin"
test -s "$ROOT/dist/$NAME.cue"
test -f "$BIOS"

isoinfo -i "$ROOT/dist/$NAME.iso" -f > "$ROOT/build/iso-files.txt"
for file in /000.DAT /IPL.TXT /M1.Z80 /MAIN.PRG /S1.FIX /V1.PCM; do
    grep -Fx "$file" "$ROOT/build/iso-files.txt" >/dev/null
done

"$PYTHON" "$ROOT/tools/disc_layout.py" "$ROOT/build/generated/disc.inc" \
    --lba 24 --verify-iso "$ROOT/dist/$NAME.iso"
"$PYTHON" "$ROOT/tests/test_audio_banks.py"

FMV_TARGET_FRAME=$("$PYTHON" -c 'import json,os,sys; last=json.load(open(sys.argv[1]))["frame_count"]-1; target=int(os.environ.get("FMV_TARGET_FRAME", min(1800,last))); assert 0<=target<=last, "target frame is outside the clip"; print(target)' \
    "$ROOT/build/generated/video.json")
export FMV_TARGET_FRAME

CAPTURE_DIR=$(mktemp -d "$ROOT/build/mame-run.XXXXXX")
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
    "$MAME_BIN" neocdz -rompath "$MAME_ROMPATH" -bios 0 \
    -cdrom "$ROOT/dist/$NAME.cue" \
    -skip_gameinfo -nothrottle -video soft -sound none -window \
    -cfg_directory "$CAPTURE_DIR/cfg" -nvram_directory "$CAPTURE_DIR/nvram" \
    -snapshot_directory "$CAPTURE_DIR" -snapname frame \
    -autoboot_script "$ROOT/tests/mame_short.lua" \
    > "$CAPTURE_DIR/mame.log" 2>&1

if ! grep -F 'FMV_SMOKE_OK' "$CAPTURE_DIR/mame.log" >/dev/null; then
    cat "$CAPTURE_DIR/mame.log" >&2
    exit 1
fi
test -s "$CAPTURE_DIR/frame.png"
"$PYTHON" "$ROOT/tools/compare_native_capture.py" \
    "$ROOT/build/generated/STREAM.DAT" "$FMV_TARGET_FRAME" "$CAPTURE_DIR/frame.png" \
    --max-mean-error "${MAME_MAX_MEAN_ERROR:-1.0}"
echo "verified: ISO extent, native-tile decode, graphics upload, and CDZ flow control"
