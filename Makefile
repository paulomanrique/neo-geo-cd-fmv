SHELL := /bin/sh

PYTHON ?= python3
FFMPEG ?= ffmpeg
MKISOFS ?= mkisofs
CHDMAN ?= chdman
ASL := vendor/asl/asl
P2BIN := vendor/asl/p2bin
ASL_COMMIT := c7155b4fd3d33110f0eb098dede4295a8c008772
ASL_STAMP := vendor/asl/.ngcd-video-built
GENERATED := build/generated
INPUT_VIDEO ?= assets/input.mp4
DISC := build/disc
DIST := dist
NAME := neo-geo-cd-fmv

.PHONY: all clean verify check

all: $(DIST)/$(NAME).cue $(DIST)/$(NAME).bin

$(GENERATED)/video.json: $(INPUT_VIDEO) tools/encode_native_stream.py tools/perceptual.py tools/palette_fit.py tools/video_encoder.py
	mkdir -p $(GENERATED)
	$(PYTHON) tools/encode_native_stream.py $(INPUT_VIDEO) $(GENERATED)

$(GENERATED)/disc.inc: tools/disc_layout.py
	mkdir -p $(GENERATED)
	$(PYTHON) tools/disc_layout.py $@ --lba 24

$(ASL) $(P2BIN): $(ASL_STAMP)
	@test -x $@

$(ASL_STAMP):
	mkdir -p vendor
	if ! test -d vendor/asl/.git; then \
		git clone --branch upstream https://github.com/Macroassembler-AS/asl-releases.git vendor/asl; \
	fi
	git -C vendor/asl fetch --depth 1 origin $(ASL_COMMIT)
	git -C vendor/asl checkout --detach $(ASL_COMMIT)
	if test "$$(uname -s)" = Darwin; then \
		case "$$(uname -m)" in \
			arm64) profile=arm-osx ;; \
			*) profile=x86_64-osx ;; \
		esac; \
	else profile=unknown-linux; fi; \
	cp vendor/asl/Makefile.def-samples/Makefile.def-$$profile vendor/asl/Makefile.def
	$(MAKE) -C vendor/asl -j4
	touch $@

$(DISC)/MAIN.PRG: src/main.asm $(GENERATED)/video.json $(GENERATED)/disc.inc $(ASL) $(P2BIN)
	mkdir -p $(DISC)
	cd src && ../$(ASL) -L main.asm
	$(P2BIN) src/main.p $@
	truncate -s %2048 $@

$(DISC)/M1.Z80: src/audio.asm $(ASL) $(P2BIN)
	mkdir -p $(DISC)
	cd src && ../$(ASL) -L audio.asm
	$(P2BIN) src/audio.p $@
	truncate -s 65536 $@

$(DISC)/IPL.TXT: $(DISC)/MAIN.PRG $(DISC)/M1.Z80 $(GENERATED)/video.json
	mkdir -p $(DISC)
	cp $(GENERATED)/STREAM.DAT $(DISC)/000.DAT
	cp $(GENERATED)/AUDIO0.PCM $(DISC)/V1.PCM
	truncate -s 131072 $(DISC)/S1.FIX
	printf 'S1.FIX,0,0\r\nM1.Z80,0,0\r\nV1.PCM,0,0\r\nMAIN.PRG,0,0\r\n\032' > $@
	printf 'Abstracted by SNK corporation\032' > $(DISC)/ABS.TXT
	printf 'Bibliographiced by SNK corporation\032' > $(DISC)/BIB.TXT
	printf 'Copyright by SNK corporation\032' > $(DISC)/CPY.TXT

$(DIST)/$(NAME).iso: $(DISC)/IPL.TXT
	mkdir -p $(DIST)
	$(MKISOFS) -quiet -iso-level 1 -pad -N -V NGCDVIDEO -o $@ \
		$(DISC)/000.DAT $(DISC)/S1.FIX $(DISC)/M1.Z80 $(DISC)/V1.PCM \
		$(DISC)/MAIN.PRG $(DISC)/ABS.TXT $(DISC)/BIB.TXT \
		$(DISC)/CPY.TXT $(DISC)/IPL.TXT
	$(PYTHON) tools/disc_layout.py $(GENERATED)/disc.inc --lba 24 --verify-iso $@

$(DISC)/$(NAME)-data.cue: $(DIST)/$(NAME).iso
	printf 'FILE "../../$(DIST)/$(NAME).iso" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n' > $@

$(DISC)/$(NAME).chd: $(DISC)/$(NAME)-data.cue
	$(CHDMAN) createcd -i $< -o $@ -f

$(DIST)/$(NAME).cue $(DIST)/$(NAME).bin &: $(DISC)/$(NAME).chd
	$(CHDMAN) extractcd -i $< -o $(DIST)/$(NAME).cue -ob $(DIST)/$(NAME).bin -f

verify: all
	NAME=$(NAME) PYTHON=$(PYTHON) tests/verify.sh

check:
	$(PYTHON) tests/test_audio_banks.py
	$(PYTHON) tests/test_perceptual.py
	$(PYTHON) tests/test_advanced_codec.py

clean:
	rm -rf build dist src/main.bin src/main.lst src/main.p \
		src/audio.bin src/audio.lst src/audio.p
