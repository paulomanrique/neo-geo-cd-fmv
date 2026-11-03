; Neo Geo CD native-tile FMV player, 320x224 at 15 fps.
; Five physical sectors correspond to every frame: four video, one ADPCM.

    cpu 68000
    supmode on

    INCLUDE "../build/generated/video.inc"
    INCLUDE "../build/generated/disc.inc"

RING_START              equ $020000
RING_END                equ $080000
RING_BYTES              equ RING_END-RING_START
SECTOR_BYTES            equ 2048
AUDIO_BUFFER            equ $080000
AUDIO_BUFFER_B          equ $110000
AUDIO_BUFFER_BYTES      equ $100000
PREVIOUS_TILES          equ $1A0000
CURRENT_TILES           equ $1A8C00
PREVIOUS_MAP            equ $1B1800
CURRENT_MAP             equ $1B1A30
PREPARED_PALETTE        equ $1B1C60
SUPPLY_POSITIONS        equ $1B1EE0
SUPPLY_DESTINATIONS     equ $1B1FA0
FRAME_INPUT             equ $1B2100
BASELINE_FLAGS          equ $1B4900
TILE_VALID_A            equ $1B4A18
TILE_VALID_B            equ TILE_VALID_A+280
PREPARED_MAP            equ TILE_VALID_B+280
SLOT_TO_POSITION        equ $1B5000

RAMSTART                equ $100000
RAM_CLEAR_END           equ $100100

PALETTES                equ $400000
BACKDROPCOLOR           equ $401FFE
VRAM_ADDR               equ $3C0000
VRAM_RW                 equ $3C0002
VRAM_MOD                equ $3C0004
REG_LSPCMODE            equ $3C0006
REG_IRQACK              equ $3C000C
REG_DIPSW               equ $300001
REG_SOUND               equ $320000
REG_PALBANK0            equ $3A001F

REG_CD_IRQ_MASK         equ $FF0002
REG_CD_IRQ_ACK          equ $FF000F
REG_CD_MODE             equ $FF0017
REG_DMA_START           equ $FF0061
REG_DMA_DEST            equ $FF0064
REG_DMA_SOURCE          equ $FF0068
REG_DMA_LENGTH          equ $FF0070
REG_DMA_CONFIG0         equ $FF007E
REG_LC_ADDRESS          equ $FF0101
REG_LC_DATA             equ $FF0103
REG_TRANSFER_AREA       equ $FF0105
REG_SPR_DISABLE         equ $FF0111
REG_SPR_REQUEST         equ $FF0121
REG_PCM_REQUEST         equ $FF0123
REG_SPR_RELEASE         equ $FF0141
REG_PCM_RELEASE         equ $FF0143
REG_CDD_OUTPUT          equ $FF0163
REG_CDD_CONTROL         equ $FF0165
REG_WATCHDOG            equ $FF016F
REG_CDD_ENABLE          equ $FF0181
REG_SPR_BANK            equ $FF01A1
REG_PCM_BANK            equ $FF01A3
UPLOAD_WINDOW           equ $E00000

SCB1                    equ $0000
SCB2                    equ $8000
SCB3                    equ $8200
SCB4                    equ $8400
VIDEO_FIRST_SPRITE      equ 1
VIDEO_TOP_SCANLINE      equ 16
SPRITE_BANK_TILES       equ 8192
SPRITE_UPLOAD_BURST     equ VIDEO_MAX_SUPPLIES
PCM_UPLOAD_BURST        equ 192
PCM_HALF_BYTES          equ $80000
VIDEO_PRESENT_NUM       equ VIDEO_FPS_NUM

BIOSF_SYSTEM_INT1       equ $C00438
BIOSF_SYSTEM_IO         equ $C0044A
BIOSF_FIX_CLEAR         equ $C004C2
BIOSF_LSP_1ST           equ $C004C8
BIOS_SYSTEM_MODE        equ $10FD80

    org RAMSTART

Playing:                ds.b 1
PreparedReady:          ds.b 1
NeedPrepare:            ds.b 1
PalettePending:         ds.b 1
StreamOverflow:         ds.b 1
AudioReply:             ds.b 1
AudioUploadActive:      ds.b 1
AudioUploadBank:        ds.b 1
DiscPaused:             ds.b 1
                        align 2
CurrentFrame:           ds.w 1
NextFrame:              ds.w 1
SupplyCount:            ds.w 1
UploadIndex:            ds.w 1
SectorsBuffered:        ds.w 1
                        align 4
FrameAccumulator:       ds.l 1
RingReadPointer:        ds.l 1
RingWritePointer:       ds.l 1
SectorsReceived:        ds.l 1
PreviousTilesPointer:   ds.l 1
CurrentTilesPointer:    ds.l 1
PreviousMapPointer:     ds.l 1
CurrentMapPointer:      ds.l 1
AudioWriteOffset:       ds.l 1
AudioUploadOffset:      ds.l 1
DecoderBusy             equ $10003C
PrimarySupplyCount      equ $10003E
LateFields              equ $10004C
PreviousValidPointer    equ $100050
CurrentValidPointer     equ $100054
FrameDecoded            equ $100058

    org $0000

    dc.l $0010F300,$00C00402,BusError,AddressError
    dc.l Generic,Generic,$00C00426,$00C00426
    dc.l $00C0041A,$00C00420,$00C00426,$00C00426
    dc.l $00C00426,$00C00426,$00C00426,$00C0042C
    dc.l $00C00522,$00C00528,$00C0052E,$00C00534
    dc.l $00C0053A,CDDecoderIRQ,$00C004EC,$00C004E6
    dc.l $00C004E0,RLI,VBlank,IRQ3
    dc.l Generic,Generic,Generic,Generic
    dc.l Generic,Generic,Generic,Generic
    dc.l Generic,Generic,Generic,Generic
    dc.l Generic,Generic,Generic,Generic
    dc.l Generic,Generic,Generic,Generic
    dc.l $00C00426,$00C00426,$00C00426,$00C00426
    dc.l $00C00426,$00C00426,$00C00426,$00C00426
    dc.l $00C00426,$00C00426,$00C00426,$00C00426
    dc.l $00C00426,$00C00426,$00C00426,$00C00426

    dc.b "NEO-GEO",$02
    dc.w $0777
    dc.l $00200000
    dc.l $0010F000
    dc.w $0000
    dc.w $0000
    dc.l Config,Config,Config

    jmp Start
    jmp Generic
    jmp Generic
    jmp Generic

    org $013A
    dc.w $0000

Generic:
    rte
    rts

BusError:
    move.l #$42555345,$100040
.halt:
    bra .halt

AddressError:
    move.l #$41444452,$100040
    move.l 2(a7),$100044
    move.l 8(a7),$100048
.halt:
    bra .halt

    org $0184
Config:
    dc.b "VIDEO DEMO     "
    dc.w $FFFF,$FFFF,$FFFF
    dc.b $00,$00,$00,$00,$00,$00,$00,$00,$00,$00
    dc.b "            "
    dc.b "            "
    dc.b "            "

; The LC8951 decoder IRQ fires once per data sector. Copy its 2048-byte
; payload into the program-RAM ring before acknowledging the decoder.
CDDecoderIRQ:
    movem.l d0-d3/a0,-(a7)

    ; Read PT, then program DAC=PT+4 and DBC=$7FF.
    move.b #8,REG_LC_ADDRESS
    moveq #0,d0
    move.b REG_LC_DATA,d0
    moveq #0,d1
    move.b REG_LC_DATA,d1
    addq.w #4,d0
    cmpi.w #$0100,d0
    blo .dac_ready
    andi.w #$00FF,d0
    addq.b #1,d1
.dac_ready:
    move.b #2,REG_LC_ADDRESS
    move.b #$FF,REG_LC_DATA
    move.b #$07,REG_LC_DATA
    move.b d0,REG_LC_DATA
    move.b d1,REG_LC_DATA
    move.b d2,REG_LC_DATA

    move.l RingWritePointer,REG_DMA_DEST
    clr.l REG_DMA_SOURCE
    move.l #$00000400,REG_DMA_LENGTH
    move.w #$FFC5,REG_DMA_CONFIG0
    move.b #$40,REG_DMA_START

    ; STAT3 read clears the decoder interrupt condition.
    move.b #15,REG_LC_ADDRESS
    move.b REG_LC_DATA,d0
    move.b #$20,REG_CD_IRQ_ACK

    movea.l RingWritePointer,a0
    adda.l #$800,a0
    cmpa.l #RING_END,a0
    blo .store_write
    lea RING_START,a0
.store_write:
    move.l a0,RingWritePointer
    cmpi.w #(RING_BYTES/SECTOR_BYTES),SectorsBuffered
    bhs .overflow
    addq.w #1,SectorsBuffered
    addq.l #1,SectorsReceived
    bra .done
.overflow:
    move.b #1,StreamOverflow
.done:
    movem.l (a7)+,d0-d3/a0
    rte

VBlank:
    btst #7,BIOS_SYSTEM_MODE
    bne .game_vblank
    jmp BIOSF_SYSTEM_INT1
.game_vblank:
    movem.l d0-d7/a0-a6,-(a7)
    move.w #4,REG_IRQACK
    move.b d0,REG_DIPSW
    jsr BIOSF_SYSTEM_IO

    jsr CheckAudioReply
    jsr UploadSpriteBurst
    jsr UploadAudioBurst

    tst.b Playing
    beq .done
    move.l FrameAccumulator,d0
    addi.l #VIDEO_PRESENT_NUM,d0
    cmpi.l #VIDEO_REFRESH_DEN,d0
    blo .store_accumulator
    tst.b PreparedReady
    beq .late_frame
    subi.l #VIDEO_REFRESH_DEN,d0
    move.l d0,FrameAccumulator
    jsr PresentPreparedFrame
    clr.b PreparedReady

    move.w CurrentFrame,d1
    addq.w #1,d1
    move.w d1,CurrentFrame
    cmpi.w #VIDEO_FRAME_COUNT-1,d1
    bhs .movie_complete
    move.b #1,NeedPrepare
    bra .done

.late_frame:
    addq.l #1,LateFields
    bra .store_accumulator

.movie_complete:
    clr.b Playing
    clr.w REG_CD_IRQ_MASK
    clr.b REG_SOUND
    lea DiscPausePacket,a0
    jsr SendCDDCommand
    move.b #1,DiscPaused
    bra .done

.store_accumulator:
    move.l d0,FrameAccumulator
.done:
    movem.l (a7)+,d0-d7/a0-a6
    rte

RLI:
    move.w #2,REG_IRQACK
    rte

IRQ3:
    move.w #1,REG_IRQACK
    rte

Start:
    lea $10F300,a7
    move.b #1,REG_WATCHDOG
    move.b d0,REG_DIPSW
    move.w #7,REG_IRQACK
    move.w #$2700,sr

    move.l #((RAM_CLEAR_END-RAMSTART)/4)-1,d7
    lea RAMSTART,a0
.clear_work:
    clr.l (a0)+
    dbra d7,.clear_work
    move.l #((CURRENT_MAP-PREVIOUS_TILES)/4)-1,d7
    lea PREVIOUS_TILES,a0
.clear_frames:
    clr.l (a0)+
    dbra d7,.clear_frames
    move.l #279,d7
    lea PREVIOUS_MAP,a0
.clear_maps:
    clr.l (a0)+
    dbra d7,.clear_maps

    move.l #RING_START,RingReadPointer
    move.l #RING_START,RingWritePointer
    move.l #PREVIOUS_TILES,PreviousTilesPointer
    move.l #CURRENT_TILES,CurrentTilesPointer
    move.l #PREVIOUS_MAP,PreviousMapPointer
    move.l #CURRENT_MAP,CurrentMapPointer
    move.l #TILE_VALID_A,PreviousValidPointer
    move.l #TILE_VALID_B,CurrentValidPointer
    lea TILE_VALID_A,a0
    move.w #559,d7
.valid_black:
    move.b #1,(a0)+
    dbra d7,.valid_black
    move.w #$FFFF,CurrentFrame

    jsr BIOSF_LSP_1ST
    jsr BIOSF_FIX_CLEAR
    move.b d0,REG_PALBANK0
    move.w #$8000,BACKDROPCOLOR
    move.w #0,REG_LSPCMODE
    move.b #1,REG_SPR_DISABLE
    jsr UploadBlackTile
    jsr SetupSprites
    bset #7,BIOS_SYSTEM_MODE

    jsr StartDiscStream
    move.w #$2000,sr
.wait_initial_sectors:
    cmpi.w #11,SectorsBuffered
    blo .wait_initial_sectors
    jsr SynchronizeRing
.wait_synced_frames:
    ; Keep an initial read-ahead reserve instead of starting at the ring floor.
    cmpi.w #96,SectorsBuffered
    blo .wait_synced_frames
    move.w #$2100,sr

    ; Frame zero is built and displayed before the clock and audio start.
    jsr DecodePreparedFrame
    jsr UploadAllPrepared
    jsr PresentPreparedFrame
    move.w #0,CurrentFrame

    ; Frame one is completely ready, leaving one-frame optical headroom.
    jsr DecodePreparedFrame
    jsr UploadAllPrepared
    move.b #1,PreparedReady
    clr.l FrameAccumulator
    move.b #1,REG_SOUND
    move.b #1,Playing
    clr.b REG_SPR_DISABLE
    move.w #$2000,sr

.loop:
    jsr ManageDiscFlow
    tst.b NeedPrepare
    beq .loop
    cmpi.w #5,SectorsBuffered
    blo .loop
    clr.b NeedPrepare
    jsr DecodePreparedFrame
    bra .loop

StartDiscStream:
    ; LC8359 program used by the Neo Geo CD BIOS for contiguous CDC-to-RAM
    ; transfers. MAME recognizes the mode word; original hardware executes
    ; the complete microprogram.
    move.w #$FFC5,$FF007E
    move.w #$FCF5,$FF0080
    move.w #$A6F6,$FF0082
    move.w #$2602,$FF0084
    move.w #$FDFF,$FF0086
    move.w #$FC2D,$FF0088
    move.w #$FCF5,$FF008A
    move.w #$8492,$FF008C
    move.w #$DA92,$FF008E
    move.b #1,REG_CDD_ENABLE
    move.b #1,REG_CD_MODE

    ; IFCTRL: decoder IRQ + data output.
    move.b #1,REG_LC_ADDRESS
    move.b #$22,REG_LC_DATA
    ; CTRL0: decoder enable + write request. Without WRRQ the LC8951 keeps
    ; raising decode interrupts but leaves the old IPL sector in its buffer.
    move.b #10,REG_LC_ADDRESS
    move.b #$84,REG_LC_DATA
    move.b #$0C,REG_LC_DATA
    move.w #$0500,REG_CD_IRQ_MASK

    lea DiscPlayPacket,a0
    jsr SendCDDCommand
    rts

; The CDZ reads twice as fast as the original drive. Pause and resume the
; sequential command at generous ring-buffer watermarks so both machines
; present the same 15 fps stream without dropping or overwriting sectors.
ManageDiscFlow:
    tst.b Playing
    beq .done
    tst.b DiscPaused
    bne .paused
    cmpi.w #128,SectorsBuffered
    blo .done
    lea DiscPausePacket,a0
    jsr SendCDDCommand
    move.b #1,DiscPaused
    rts
.paused:
    cmpi.w #48,SectorsBuffered
    bhi .done
    lea DiscResumePacket,a0
    jsr SendCDDCommand
    clr.b DiscPaused
.done:
    rts

SendCDDCommand:
    movem.l d7/a0,-(a7)
    moveq #9,d7
.command_nibble:
    move.b (a0)+,REG_CDD_OUTPUT
    move.b #0,REG_CDD_CONTROL
    tst.w d7
    beq .command_send
    move.b #1,REG_CDD_CONTROL
    bra .command_next
.command_send:
    ; Value 3 supplies the send edge required by hardware/MAME. The trailing
    ; value 1 leaves the clock high (so hardware sees no second edge) while
    ; NeoCD libretro advances its packet pointer and executes the command.
    move.b #3,REG_CDD_CONTROL
    move.b #1,REG_CDD_CONTROL
.command_next:
    dbra d7,.command_nibble
    movem.l (a7)+,d7/a0
    rts

; The CDC may expose one already-decoded sector while a new play command is
; taking effect. Find a block whose first sector is NTV1 and whose fifth is
; the matching NTA1 auxiliary sector, then begin fixed five-sector framing.
SynchronizeRing:
    movem.l d0/a0-a1,-(a7)
.scan:
    cmpi.w #5,SectorsBuffered
    blo .scan
    movea.l RingReadPointer,a0
    cmpi.l #$4E545631,(a0)
    bne .discard
    movea.l a0,a1
    adda.l #VIDEO_PACKET_BYTES,a1
    cmpa.l #RING_END,a1
    blo .check_aux
    suba.l #RING_BYTES,a1
.check_aux:
    cmpi.l #$4E544131,(a1)
    beq .done
.discard:
    adda.l #SECTOR_BYTES,a0
    cmpa.l #RING_END,a0
    blo .store
    lea RING_START,a0
.store:
    move.w sr,d0
    move.w #$2700,sr
    move.l a0,RingReadPointer
    subq.w #1,SectorsBuffered
    move.w d0,sr
    bra .scan
.done:
    movem.l (a7)+,d0/a0-a1
    rts

; Copy one five-sector block out of the ring and account for it atomically.
AcquireFrameBlock:
    movem.l d0-d1/d7/a0-a1,-(a7)
    movea.l RingReadPointer,a0
    lea FRAME_INPUT,a1
    move.w #(VIDEO_FRAME_BLOCK_BYTES/4)-1,d7
.copy:
    move.l (a0)+,(a1)+
    cmpa.l #RING_END,a0
    blo .copy_next
    lea RING_START,a0
.copy_next:
    dbra d7,.copy
    move.w sr,d0
    move.w #$2700,sr
    move.l a0,RingReadPointer
    subi.w #5,SectorsBuffered
    move.w d0,sr
    jsr StoreAudioPayload
    movem.l (a7)+,d0-d1/d7/a0-a1
    rts

StoreAudioPayload:
    movem.l d0-d3/d7/a0-a1,-(a7)
    lea FRAME_INPUT+VIDEO_PACKET_BYTES,a0
    cmpi.l #$4E544131,(a0)      ; "NTA1"
    bne .done
    moveq #0,d7
    move.w 6(a0),d7
    beq .done
    subq.w #1,d7
    lea 12(a0),a0
    move.l AudioWriteOffset,d0
    andi.l #AUDIO_BUFFER_BYTES-1,d0
    lea AUDIO_BUFFER,a1
    cmpi.l #PCM_HALF_BYTES,d0
    blo .audio_address
    subi.l #PCM_HALF_BYTES,d0
    lea AUDIO_BUFFER_B,a1
.audio_address:
    adda.l d0,a1
.copy_audio:
    move.b (a0)+,(a1)+
    cmpa.l #AUDIO_BUFFER+PCM_HALF_BYTES,a1
    bne .audio_wrap
    lea AUDIO_BUFFER_B,a1
    bra .audio_next
.audio_wrap:
    cmpa.l #AUDIO_BUFFER_B+PCM_HALF_BYTES,a1
    bne .audio_next
    lea AUDIO_BUFFER,a1
.audio_next:
    addq.l #1,AudioWriteOffset
    dbra d7,.copy_audio
.done:
    movem.l (a7)+,d0-d3/d7/a0-a1
    rts

DecodePreparedFrame:
    movem.l d0-d7/a0-a6,-(a7)
    move.b #1,DecoderBusy
    clr.b FrameDecoded
    clr.b PreparedReady
    jsr AcquireFrameBlock
    lea FRAME_INPUT,a6
    cmpi.l #$4E545631,(a6)      ; "NTV1"
    bne DecoderFatal
    move.w 4(a6),d0
    cmp.w NextFrame,d0
    bne DecoderFatal
    move.w 6(a6),d0
    andi.b #1,d0
    move.b d0,PalettePending
    move.w 10(a6),SupplyCount
    move.w 20(a6),PrimarySupplyCount
    clr.w UploadIndex

    tst.b PalettePending
    beq .copy_map
    lea 32(a6),a0
    lea PREPARED_PALETTE,a1
    move.w #(VIDEO_PALETTE_BYTES/4)-1,d7
.palette_copy:
    move.l (a0)+,(a1)+
    dbra d7,.palette_copy

.copy_map:
    moveq #0,d0
    move.w 12(a6),d0
    lea 0(a6,d0.l),a0
    btst #1,7(a6)
    beq .full_map
    ; Start a differential map from N-1, retaining N-2 copy hints first.
    movea.l PreviousMapPointer,a1
    lea PREPARED_MAP,a2
    move.w #VIDEO_TILES_PER_FRAME-1,d7
.map_seed:
    move.w (a1)+,(a2)+
    dbra d7,.map_seed
    move.w (a0)+,d7
    cmpi.w #VIDEO_TILES_PER_FRAME,d7
    bhi DecoderFatal
    tst.w d7
    beq .delta_ready
    subq.w #1,d7
    lea PREPARED_MAP,a2
.map_delta:
    moveq #0,d0
    move.w (a0)+,d0
    cmpi.w #VIDEO_TILES_PER_FRAME,d0
    bhs DecoderFatal
    add.w d0,d0
    move.w (a0)+,0(a2,d0.w)
    dbra d7,.map_delta
.delta_ready:
    lea PREPARED_MAP,a0
.full_map:
    movea.l CurrentMapPointer,a1
    lea BASELINE_FLAGS,a2
    move.w #VIDEO_TILES_PER_FRAME-1,d7
.map_copy:
    move.w (a0)+,d0
    cmp.w (a1),d0
    sne (a2)+
    move.w d0,(a1)+
    dbra d7,.map_copy

    ; Build a transient graphics-slot -> previous-position table.
    movea.l PreviousMapPointer,a0
    lea SLOT_TO_POSITION,a1
    moveq #0,d6
    move.w #VIDEO_TILES_PER_FRAME-1,d7
.slot_table:
    moveq #0,d0
    move.w (a0)+,d0
    add.w d0,d0
    move.w d6,0(a1,d0.l)
    addq.w #1,d6
    dbra d7,.slot_table

    ; Reconstruct all reuse/copy positions from the previous visible map.
    movea.l CurrentMapPointer,a0
    movea.l CurrentTilesPointer,a5
    movea.l PreviousTilesPointer,a4
    lea BASELINE_FLAGS,a6
    moveq #0,d6
    move.w #VIDEO_TILES_PER_FRAME-1,d7
.baseline_tile:
    moveq #0,d0
    move.w (a0)+,d0
    movea.l CurrentValidPointer,a3
    tst.b (a6)+
    sne d4
    ; A recycled slot can equal N-2's ID while containing different pixels.
    ; Require a valid N-1 source before trusting an N-2 copy hint.
    add.w d0,d0
    moveq #0,d1
    move.w 0(a1,d0.l),d1
    cmpi.w #VIDEO_TILES_PER_FRAME,d1
    bhs .baseline_invalid
    movea.l PreviousValidPointer,a2
    tst.b 0(a2,d1.w)
    beq .baseline_invalid
    move.l d1,d2
    add.w d2,d2
    movea.l PreviousMapPointer,a2
    move.w -2(a0),d0
    cmp.w 0(a2,d2.w),d0
    bne .baseline_invalid
    tst.b d4
    bne .baseline_required
    tst.b 0(a3,d6.w)
    bne .baseline_next
.baseline_required:
    move.b #1,0(a3,d6.w)
    lsl.l #7,d1
    lea 0(a4,d1.l),a2
    move.l d6,d2
    lsl.l #7,d2
    lea 0(a5,d2.l),a3
    moveq #31,d5
.baseline_copy:
    move.l (a2)+,(a3)+
    dbra d5,.baseline_copy
    bra .baseline_next
.baseline_invalid:
    movea.l CurrentValidPointer,a3
    clr.b 0(a3,d6.w)
.baseline_next:
    addq.w #1,d6
    dbra d7,.baseline_tile

    ; Decode the supplies and retain their position/destination for VBlank.
    lea FRAME_INPUT,a6
    moveq #0,d0
    move.w 14(a6),d0
    lea 0(a6,d0.l),a0
    lea SUPPLY_POSITIONS,a2
    lea SUPPLY_DESTINATIONS,a3
    move.w SupplyCount,d7
    beq .ready
    subq.w #1,d7
.supply:
    ; Supply records never straddle the four-sector boundary. Once all
    ; primary records are consumed, continue in the unused tail of the audio
    ; sector; the low-rate ADPCM payload occupies only its first 640 bytes.
    move.w SupplyCount,d0
    sub.w d7,d0
    subq.w #1,d0
    cmp.w PrimarySupplyCount,d0
    bne .supply_pointer_ready
    lea FRAME_INPUT+VIDEO_AUX_DATA_OFFSET,a0
.supply_pointer_ready:
    moveq #0,d6
    move.w (a0)+,d6
    move.w d6,(a2)+
    move.w (a0)+,(a3)+
    moveq #0,d4
    move.b (a0)+,d4             ; mode
    moveq #0,d5
    move.b (a0)+,d5             ; payload length
    cmpi.w #VIDEO_TILES_PER_FRAME,d6
    bhs DecoderFatal
    cmpi.b #1,d4
    bls .independent_tile
    movea.l PreviousValidPointer,a6
    tst.b 0(a6,d6.w)
    beq DecoderFatal
.independent_tile:
    movea.l CurrentValidPointer,a6
    move.b #1,0(a6,d6.w)
    move.l d6,d0
    lsl.l #7,d0
    movea.l CurrentTilesPointer,a1
    adda.l d0,a1
    movea.l PreviousTilesPointer,a4
    adda.l d0,a4
    cmpi.b #0,d4
    beq .raw
    cmpi.b #1,d4
    beq .packbits
    cmpi.b #2,d4
    beq .xor_previous
    cmpi.b #3,d4
    beq .planes_previous
    bra DecoderFatal

.raw:
    moveq #31,d3
.raw_loop:
    move.l (a0)+,(a1)+
    dbra d3,.raw_loop
    bra .supply_next

.packbits:
    lea 0(a0,d5.l),a6
.pack_command:
    cmpa.l a6,a0
    bhs .pack_done
    moveq #0,d0
    move.b (a0)+,d0
    cmpi.b #127,d0
    bhi .pack_repeat
    move.w d0,d3
.pack_literal:
    move.b (a0)+,(a1)+
    dbra d3,.pack_literal
    bra .pack_command
.pack_repeat:
    cmpi.b #128,d0
    beq .pack_command
    move.w #256,d3
    sub.w d0,d3
    move.b (a0)+,d1
.pack_repeat_loop:
    move.b d1,(a1)+
    dbra d3,.pack_repeat_loop
    bra .pack_command
.pack_done:
    bra .supply_next

.xor_previous:
    movea.l a1,a6
    moveq #31,d3
.xor_seed:
    move.l (a4)+,(a6)+
    dbra d3,.xor_seed
    lea 0(a0,d5.l),a6
.xor_command:
    cmpa.l a6,a0
    bhs .xor_done
    moveq #0,d0
    move.b (a0)+,d0
    move.w d0,d3
    andi.w #$007F,d3
    addq.w #1,d3
    btst #7,d0
    beq .xor_skip
    subq.w #1,d3
.xor_literal:
    move.b (a0)+,d1
    eor.b d1,(a1)+
    dbra d3,.xor_literal
    bra .xor_command
.xor_skip:
    adda.w d3,a1
    bra .xor_command
.xor_done:
    bra .supply_next

.planes_previous:
    movea.l a1,a6
    moveq #31,d3
.plane_seed:
    move.l (a4)+,(a6)+
    dbra d3,.plane_seed
    moveq #0,d0
    move.b (a0)+,d0             ; changed-plane mask
    moveq #0,d2                 ; plane offset
.plane:
    btst d2,d0
    beq .next_plane
    lea 0(a1,d2.w),a6
    moveq #15,d3
.plane_first:
    move.b (a0)+,(a6)
    addq.l #4,a6
    dbra d3,.plane_first
    moveq #15,d3
.plane_second:
    move.b (a0)+,(a6)
    addq.l #4,a6
    dbra d3,.plane_second
.next_plane:
    addq.w #1,d2
    cmpi.w #4,d2
    blo .plane

.supply_next:
    btst #0,d5
    beq .supply_aligned
    addq.l #1,a0
.supply_aligned:
    dbra d7,.supply
    bra .decoded
.ready:
.decoded:
    addq.w #1,NextFrame
    move.b #1,FrameDecoded
    clr.b DecoderBusy
    movem.l (a7)+,d0-d7/a0-a6
    rts

DecoderFatal:
    move.l #$4445434F,$100040   ; "DECO": authored-frame contract failure
    move.w #$2700,sr
    clr.b FrameDecoded
    clr.b PreparedReady
    clr.b Playing
.hang:
    bra .hang

UploadAllPrepared:
    clr.b PreparedReady
.again:
    jsr UploadSpriteBurst
    tst.b PreparedReady
    beq .again
    clr.b PreparedReady
    rts

UploadSpriteBurst:
    tst.b DecoderBusy
    bne .busy
    tst.b FrameDecoded
    beq .busy
    movem.l d0-d7/a0-a3,-(a7)
    move.w SupplyCount,d7
    sub.w UploadIndex,d7
    beq .already_done
    cmpi.w #SPRITE_UPLOAD_BURST,d7
    bls .count_ready
    move.w #SPRITE_UPLOAD_BURST,d7
.count_ready:
    subq.w #1,d7
    move.b #0,REG_TRANSFER_AREA
    move.b d0,REG_SPR_REQUEST
.tile:
    moveq #0,d0
    move.w UploadIndex,d0
    add.w d0,d0
    lea SUPPLY_POSITIONS,a0
    moveq #0,d1
    move.w 0(a0,d0.l),d1
    lsl.l #7,d1
    movea.l CurrentTilesPointer,a1
    adda.l d1,a1
    lea SUPPLY_DESTINATIONS,a0
    moveq #0,d1
    move.w 0(a0,d0.l),d1
    move.w d1,d2
    lsr.w #8,d2
    lsr.w #5,d2
    move.b d2,REG_SPR_BANK
    andi.l #$00001FFF,d1
    lsl.l #7,d1
    lea UPLOAD_WINDOW,a2
    adda.l d1,a2
    moveq #31,d3
.tile_copy:
    move.l (a1)+,(a2)+
    dbra d3,.tile_copy
    addq.w #1,UploadIndex
    dbra d7,.tile
    move.b d0,REG_SPR_RELEASE
    move.w SupplyCount,d0
    cmp.w UploadIndex,d0
    bne .done
.already_done:
    move.b #1,PreparedReady
.done:
    movem.l (a7)+,d0-d7/a0-a3
.busy:
    rts

CheckAudioReply:
    move.b REG_SOUND,d0
    cmp.b AudioReply,d0
    beq .done
    move.b d0,AudioReply
    tst.b d0
    beq .done
    andi.b #1,d0
    eori.b #1,d0                ; odd reply -> bank 0 became inactive
    move.b d0,AudioUploadBank
    clr.l AudioUploadOffset
    move.b #1,AudioUploadActive
.done:
    rts

UploadAudioBurst:
    tst.b AudioUploadActive
    beq .done
    movem.l d0-d3/d7/a0-a1,-(a7)
    move.l AudioUploadOffset,d0
    move.l #PCM_HALF_BYTES,d1
    sub.l d0,d1
    cmpi.l #PCM_UPLOAD_BURST,d1
    bls .audio_count_ready
    move.l #PCM_UPLOAD_BURST,d1
.audio_count_ready:
    move.w d1,d7
    subq.w #1,d7
    lea AUDIO_BUFFER,a0
    moveq #0,d3
    move.b AudioUploadBank,d3
    beq .audio_source_ready
    lea AUDIO_BUFFER_B,a0
.audio_source_ready:
    adda.l d0,a0
    move.l d0,d2
    add.l d2,d2
    lea UPLOAD_WINDOW+1,a1
    adda.l d2,a1
    move.b #1,REG_TRANSFER_AREA
    move.b AudioUploadBank,REG_PCM_BANK
    move.b d3,REG_PCM_REQUEST
.audio_copy:
    move.b (a0)+,(a1)
    addq.l #2,a1
    dbra d7,.audio_copy
    move.b d3,REG_PCM_RELEASE
    add.l d1,AudioUploadOffset
    cmpi.l #PCM_HALF_BYTES,AudioUploadOffset
    blo .audio_done
    clr.b AudioUploadActive
.audio_done:
    movem.l (a7)+,d0-d3/d7/a0-a1
.done:
    rts

PresentPreparedFrame:
    movem.l d0-d7/a0-a3,-(a7)
    ; A completed upload cannot advertise the same frame again while the
    ; main loop waits for the next five CD sectors.
    clr.b FrameDecoded
    tst.b PalettePending
    beq .map
    lea PREPARED_PALETTE,a0
    lea PALETTES,a1
    move.w #(VIDEO_PALETTE_BYTES/2)-1,d7
.palette:
    move.w (a0)+,(a1)+
    dbra d7,.palette
    clr.b PalettePending

.map:
    movea.l CurrentMapPointer,a3
    move.w #2,VRAM_MOD
    moveq #0,d6
.map_column:
    moveq #0,d0
    move.w d6,d0
    lsl.w #6,d0
    addi.w #SCB1+VIDEO_FIRST_SPRITE*64,d0
    move.w d0,VRAM_ADDR
    moveq #0,d0
    move.w d6,d0
    add.w d0,d0
    lea 0(a3,d0.l),a0
    moveq #VIDEO_ROWS-1,d7
.map_row:
    move.w (a0)+,VRAM_RW
    adda.w #(VIDEO_COLUMNS*2)-2,a0
    dbra d7,.map_row
    addq.w #1,d6
    cmpi.w #VIDEO_COLUMNS,d6
    blo .map_column

    ; The prepared buffers become the sole previous-frame source.
    move.l PreviousTilesPointer,d0
    move.l CurrentTilesPointer,PreviousTilesPointer
    move.l d0,CurrentTilesPointer
    move.l PreviousMapPointer,d0
    move.l CurrentMapPointer,PreviousMapPointer
    move.l d0,CurrentMapPointer
    move.l PreviousValidPointer,d0
    move.l CurrentValidPointer,PreviousValidPointer
    move.l d0,CurrentValidPointer
    movem.l (a7)+,d0-d7/a0-a3
    rts

UploadBlackTile:
    movem.l d0/d7/a0,-(a7)
    move.b #0,REG_TRANSFER_AREA
    move.b #0,REG_SPR_BANK
    move.b d0,REG_SPR_REQUEST
    lea UPLOAD_WINDOW,a0
    moveq #31,d7
.clear:
    clr.l (a0)+
    dbra d7,.clear
    move.b d0,REG_SPR_RELEASE
    movem.l (a7)+,d0/d7/a0
    rts

SetupSprites:
    move.w #1,VRAM_MOD
    moveq #0,d0
.column:
    move.w d0,d1
    addi.w #SCB2+VIDEO_FIRST_SPRITE,d1
    move.w d1,VRAM_ADDR
    move.w #$0FFF,VRAM_RW
    move.w d0,d1
    addi.w #SCB3+VIDEO_FIRST_SPRITE,d1
    move.w d1,VRAM_ADDR
    ; The visible raster starts at scanline 16; sprite zero is not drawn.
    move.w #((512-VIDEO_TOP_SCANLINE)<<7)+VIDEO_ROWS,VRAM_RW
    move.w d0,d1
    addi.w #SCB4+VIDEO_FIRST_SPRITE,d1
    move.w d1,VRAM_ADDR
    move.w d0,d1
    lsl.w #4,d1
    lsl.w #7,d1
    move.w d1,VRAM_RW
    addq.w #1,d0
    cmpi.w #VIDEO_COLUMNS,d0
    blo .column

    lea TileAttributes,a0
    move.w #2,VRAM_MOD
    moveq #0,d6
.attribute_column:
    move.w d6,d0
    lsl.w #6,d0
    addi.w #SCB1+VIDEO_FIRST_SPRITE*64+1,d0
    move.w d0,VRAM_ADDR
    moveq #VIDEO_ROWS-1,d7
.attribute_row:
    move.w (a0)+,VRAM_RW
    dbra d7,.attribute_row
    addq.w #1,d6
    cmpi.w #VIDEO_COLUMNS,d6
    blo .attribute_column
    rts

    align 2
DiscPlayPacket:
    dc.b STREAM_COMMAND_NIBBLE_0,STREAM_COMMAND_NIBBLE_1
    dc.b STREAM_COMMAND_NIBBLE_2,STREAM_COMMAND_NIBBLE_3
    dc.b STREAM_COMMAND_NIBBLE_4,STREAM_COMMAND_NIBBLE_5
    dc.b STREAM_COMMAND_NIBBLE_6,STREAM_COMMAND_NIBBLE_7
    dc.b STREAM_COMMAND_NIBBLE_8,STREAM_COMMAND_NIBBLE_9
DiscPausePacket:
    dc.b $06,$00,$00,$00,$00,$00,$00,$00,$00,$04
DiscResumePacket:
    dc.b $07,$00,$00,$00,$00,$00,$00,$00,$00,$03
TileAttributes:
    BINCLUDE "../build/generated/tile_attributes.bin"
